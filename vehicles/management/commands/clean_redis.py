from django.conf import settings
from django.core.cache import caches
from django.core.cache.backends.base import InvalidCacheBackendError
from django.core.management.base import BaseCommand


def decode(v):
    if isinstance(v, bytes):
        return v.decode()
    return v


def get_redis():
    for alias in ("redis", "default"):
        try:
            return caches[alias]._cache.get_client()
        except (InvalidCacheBackendError, KeyError):
            continue
    return None


def get_memory_info(r):
    info = r.info("memory")
    return (
        info.get("used_memory", 0),
        info.get("maxmemory", 0),
        info.get("total_system_memory", 0),
    )


def get_cache_prefix():
    prefix = settings.CACHES.get("redis", {}).get("KEY_PREFIX", "")
    return f"{prefix}:1:" if prefix else ":1:"


class Command(BaseCommand):
    help = "Safely clean Redis when memory usage exceeds a threshold"

    @staticmethod
    def add_arguments(parser):
        parser.add_argument("--maxmemory-percent", type=float, default=80)
        parser.add_argument("--target-percent", type=float, default=65)
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--force", action="store_true")
        parser.add_argument("--max-tier", type=int, default=3, choices=[1, 2, 3, 4])
        parser.add_argument("--max-history-entries", type=int, default=500)
        parser.add_argument(
            "--max-idle-days",
            type=int,
            help="Remove keys idle for more than this many days",
        )

    def handle(self, *args, **options):
        self.options = options

        r = get_redis()
        if r is None:
            self.stderr.write("Redis not available")
            return

        if options["max_idle_days"] is not None:
            self.clean_idle_keys(r, options["max_idle_days"])
            return

        used, maxmemory, _ = get_memory_info(r)
        if maxmemory and used:
            usage_pct = (used / maxmemory) * 100
        else:
            usage_pct = 0

        self.stdout.write(
            f"Redis memory: {used / 1024 / 1024:.1f}MB used / "
            f"{maxmemory / 1024 / 1024:.1f}MB max ({usage_pct:.1f}%)"
        )

        if not options["force"] and (
            not maxmemory or usage_pct < options["maxmemory_percent"]
        ):
            self.stdout.write(
                f"Usage ({usage_pct:.1f}%) below threshold "
                f"({options['maxmemory_percent']}%). No cleaning needed."
            )
            return

        target_bytes = (
            int(maxmemory * options["target_percent"] / 100) if maxmemory else 0
        )
        total_freed = 0

        tiers = {
            1: "volatile cache data (stats, trip updates, API caches)",
            2: "live vehicle data (regenerated on next data push)",
            3: "journey history lists (trimmed to max-history-entries)",
            4: "session data (logs users out)",
        }

        for tier in range(1, options["max_tier"] + 1):
            if maxmemory and total_freed and used - total_freed <= target_bytes:
                break

            self.stdout.write(f"\n--- Tier {tier}: {tiers[tier]} ---")
            before = r.info("memory")["used_memory"]

            getattr(self, f"tier_{tier}")(r)

            after = r.info("memory")["used_memory"]
            freed = before - after
            total_freed += freed
            self.stdout.write(f"  Freed {freed / 1024 / 1024:.1f}MB")

        self.stdout.write(f"\nDone. Total freed: {total_freed / 1024 / 1024:.1f}MB")

    def scan_and_delete(self, r, patterns, key_type=""):
        cursor = 0
        all_keys = set()

        for pattern in patterns:
            c, keys = 0, []
            while True:
                c, batch = r.scan(cursor=c, match=pattern, count=10000)
                keys.extend(batch)
                if c == 0:
                    break
            all_keys.update(keys)

        all_keys = list(all_keys)
        if not all_keys:
            return 0

        if self.options["dry_run"]:
            self.stdout.write(f"  Would delete {len(all_keys)} {key_type}keys")
            return 0

        for i in range(0, len(all_keys), 500):
            r.delete(*all_keys[i : i + 500])

        self.stdout.write(f"  Deleted {len(all_keys)} {key_type}keys")
        return len(all_keys)

    def clean_lock_key(self, r, key):
        if self.options["dry_run"]:
            self.stdout.write(f"  Would delete lock key: {key}")
            return
        r.delete(key)

    # --- Tier 1: Volatile cache data ---

    def tier_1(self, r):
        p = get_cache_prefix()

        raw_keys = self.scan_and_delete(
            r,
            [
                "ntaie_lock",
            ],
            "raw lock ",
        )

        cache_keys = self.scan_and_delete(
            r,
            [
                f"{p}popular_services",
                f"{p}vehicle-tracking-stats",
                f"{p}timetable-source-stats",
                f"{p}*_trip_updates",
                f"{p}*_status",
                f"{p}TflVehicle:*",
                f"{p}TflDepartures:*",
                f"{p}SiriSmDepartures:*",
                f"{p}*:poorly",
                f"{p}*_last_post",
            ],
            "volatile cache ",
        )

    # --- Tier 2: Live vehicle data ---

    def tier_2(self, r):
        p = get_cache_prefix()

        self.scan_and_delete(
            r,
            [
                "vehicle[0-9]*",
                "vehicle_location_locations",
                "service[0-9]*vehicles",
                "operator*vehicles",
            ],
            "live vehicle ",
        )

        self.scan_and_delete(
            r,
            [
                f"{p}journey[0-9]*",
                f"{p}vehicle[0-9]*dates*",
            ],
            "vehicle cache ",
        )

    # --- Tier 3: Journey history lists ---

    def tier_3(self, r):
        cursor = 0
        trimmed = 0
        deleted = 0
        max_entries = self.options["max_history_entries"]

        while True:
            cursor, keys = r.scan(cursor=cursor, count=5000)
            for key in keys:
                if isinstance(key, bytes) and len(key) == 16 and r.type(key) == b"list":
                    length = r.llen(key)
                    if length > max_entries:
                        trim_count = length - max_entries
                        if not self.options["dry_run"]:
                            r.ltrim(key, trim_count, -1)
                            trimmed += trim_count
                    elif length == 0:
                        if not self.options["dry_run"]:
                            r.delete(key)
                            deleted += 1
            if cursor == 0:
                break

        if self.options["dry_run"]:
            self.stdout.write("  Would trim large history lists and delete empty ones")
        else:
            self.stdout.write(
                f"  Trimmed {trimmed} entries, deleted {deleted} empty lists"
            )

    # --- Tier 4: Session data ---

    def tier_4(self, r):
        p = get_cache_prefix()
        self.scan_and_delete(r, [f"{p}session:*"], "session ")

    # --- Idle time cleanup ---

    def clean_idle_keys(self, r, max_idle_days):
        max_idle_seconds = max_idle_days * 86400
        cursor = 0
        deleted = 0
        skipped = 0

        persistent_patterns = {
            "liveries_css_version",
        }

        self.stdout.write(
            f"Scanning for keys idle for more than {max_idle_days} days..."
        )

        while True:
            cursor, keys = r.scan(cursor=cursor, count=5000)
            keys_to_delete = []

            for key in keys:
                key_str = decode(key)

                if any(key_str.endswith(p) for p in persistent_patterns):
                    skipped += 1
                    continue

                try:
                    idle_time = r.object("idletime", key)
                    if idle_time is not None and idle_time > max_idle_seconds:
                        ttl = r.ttl(key)
                        if ttl == -1:
                            keys_to_delete.append(key)
                except Exception:
                    continue

            if keys_to_delete:
                if self.options["dry_run"]:
                    self.stdout.write(
                        f"  Would delete {len(keys_to_delete)} idle keys"
                    )
                else:
                    for i in range(0, len(keys_to_delete), 500):
                        r.delete(*keys_to_delete[i : i + 500])
                    deleted += len(keys_to_delete)

            if cursor == 0:
                break

        if self.options["dry_run"]:
            self.stdout.write(
                f"Would delete {deleted} keys idle >{max_idle_days} days "
                f"(skipped {skipped} persistent keys)"
            )
        else:
            self.stdout.write(
                f"Deleted {deleted} keys idle >{max_idle_days} days "
                f"(skipped {skipped} persistent keys)"
            )
