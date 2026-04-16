import re
import requests
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from busstops.models import Operator, DataSource
from ...models import VehicleType, Livery, VehicleFeature, Vehicle


def normalize_fleet_number(fleet_number):
    """Normalize fleet number by capitalizing letters and extracting from combined codes"""
    if not fleet_number:
        return fleet_number

    fleet_str = str(fleet_number).upper()

    # Handle cases like "TNXB-E1133"
    if "-" in fleet_str:
        parts = fleet_str.split("-")
        fleet_part = parts[-1]
        if re.match(r"^[A-Z]+\d+$", fleet_part):
            return fleet_part

    match = re.search(r"([A-Z]+\d+)", fleet_str)
    if match:
        return match.group(1)

    return fleet_str


def normalize_registration(reg, tmsb_format=False):
    """
    Normalize vehicle registration.
    Supports:
      - AB12CDE
      - AB12-CDE
      - AB12_CDE
      - junk prefixes like 0123_-_AB12-CDE
    """
    if not reg:
        return reg

    reg_str = str(reg).upper().strip()

    # TMSB format: RX20-RJV-201 -> RX20_RJV_201
    if tmsb_format:
        return re.sub(r"[\s-]+", "_", reg_str)

    # Strip leading junk (fleet numbers, separators, etc)
    reg_str = re.sub(r"^[^A-Z]*", "", reg_str)

    # Match UK registration with optional separators
    match = re.search(
        r"\b([A-Z]{2}\d{2})[\s_-]?([A-Z]{3})\b",
        reg_str,
    )
    if match:
        return f"{match.group(1)}{match.group(2)}"

    # Fallback: just clean it
    return re.sub(r"[\s_-]", "", reg_str)


def normalize_slug(slug):
    """Normalize slug by removing duplicate prefixes and extracting clean code"""
    if not slug:
        return slug

    slug_str = str(slug).lower()

    # Handle duplicate prefixes like "tnxb-tnxb-e1133"
    parts = slug_str.split("-")
    if len(parts) >= 3 and parts[0] == parts[1]:
        return "-".join([parts[0]] + parts[2:])

    # Extract fleet-like suffix
    if len(parts) >= 2:
        last_part = parts[-1]
        if re.match(r"^[a-z]+\d+$", last_part):
            return last_part

    return slug_str


class Command(BaseCommand):
    help = "Import vehicles from bustimes.org API for a specific operator"

    def add_arguments(self, parser):
        parser.add_argument("noc", type=str, help="Operator NOC code")
        parser.add_argument(
            "-r",
            "--reg",
            action="store_true",
            help="Use registration number instead of fleet number for vehicle code",
        )
        parser.add_argument(
            "-tmsb",
            "--tmsb-format",
            action="store_true",
            help="Use TMSB registration format (WN69-FYL-200 -> WN69FYL)",
        )
        parser.add_argument(
            "--ignore-withdrawn",
            action="store_true",
            help="Skip vehicles that are marked as withdrawn",
        )

    def handle(self, *args, **options):
        noc = options["noc"]
        use_reg = options["reg"]
        tmsb_format = options["tmsb_format"]
        ignore_withdrawn = options["ignore_withdrawn"]

        try:
            operator = Operator.objects.get(noc__iexact=noc)
        except Operator.DoesNotExist:
            raise CommandError(f'Operator with NOC "{noc}" not found')

        source, _ = DataSource.objects.get_or_create(
            name="bustimes.org", defaults={"url": "https://bustimes.org/"}
        )

        self.import_vehicles(operator, source, use_reg, tmsb_format, ignore_withdrawn)
        self.stdout.write(
            self.style.SUCCESS(f"Successfully imported vehicles for {noc}")
        )

    @transaction.atomic
    def import_vehicles(
        self, operator, source, use_reg, tmsb_format=False, ignore_withdrawn=False
    ):
        url = (
            "https://bustimes.org/api/vehicles/"
            f"?format=json&limit=9999&operator={operator.noc}"
        )

        try:
            response = requests.get(url)
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as e:
            raise CommandError(f"Failed to fetch data from API: {e}")

        created_count = 0
        updated_count = 0

        for vehicle_data in data["results"]:
            if ignore_withdrawn and vehicle_data.get("withdrawn"):
                continue

            # Vehicle type
            vehicle_type = None
            if vehicle_data.get("vehicle_type"):
                vehicle_type, _ = VehicleType.objects.get_or_create(
                    id=vehicle_data["vehicle_type"]["id"],
                    defaults={
                        "name": vehicle_data["vehicle_type"]["name"],
                        "style": vehicle_data["vehicle_type"]["style"],
                        "fuel": vehicle_data["vehicle_type"]["fuel"],
                    },
                )

            # Livery (ID-based)
            livery = None
            livery_data = vehicle_data.get("livery")
            if livery_data and livery_data.get("id"):
                livery, _ = Livery.objects.get_or_create(
                    id=livery_data["id"],
                    defaults={"name": livery_data.get("name", "")},
                )

            # Determine vehicle code
            if use_reg and vehicle_data.get("reg"):
                code = normalize_registration(vehicle_data["reg"], tmsb_format)
            elif tmsb_format:
                slug = vehicle_data.get("slug", "")
                if slug and slug.startswith("tmsb-"):
                    slug_parts = slug.split("-")
                    if len(slug_parts) >= 4:
                        reg_part = "-".join(slug_parts[1:])
                        code = normalize_registration(reg_part, tmsb_format)
                    elif vehicle_data.get("reg"):
                        code = normalize_registration(vehicle_data["reg"], tmsb_format)
                    else:
                        code = normalize_slug(slug)
                else:
                    code = normalize_slug(slug)
            elif vehicle_data.get("fleet_number"):
                code = normalize_fleet_number(vehicle_data["fleet_number"])
            else:
                code = normalize_slug(vehicle_data.get("slug"))

            defaults = {
                "code": code,
                "fleet_number": normalize_fleet_number(vehicle_data.get("fleet_number"))
                if vehicle_data.get("fleet_number")
                else None,
                "fleet_code": vehicle_data.get("fleet_code"),
                "reg": normalize_registration(vehicle_data.get("reg"), tmsb_format)
                if vehicle_data.get("reg")
                else "",
                "operator": operator,
                "source": source,
                "vehicle_type": vehicle_type,
                "livery": livery,
                "name": vehicle_data.get("name", ""),
                "branding": vehicle_data.get("branding", ""),
                "notes": vehicle_data.get("notes", ""),
                "withdrawn": vehicle_data.get("withdrawn", False),
            }

            # Features (API can return null)
            features = []
            special_features = vehicle_data.get("special_features") or []

            for feature_name in special_features:
                feature, _ = VehicleFeature.objects.get_or_create(name=feature_name)
                features.append(feature)

            try:
                vehicle = Vehicle.objects.get(operator=operator, code__iexact=code)
                for key, value in defaults.items():
                    setattr(vehicle, key, value)
                vehicle.save()
                updated_count += 1
            except Vehicle.DoesNotExist:
                vehicle = Vehicle.objects.create(**defaults)
                created_count += 1

            if features:
                vehicle.features.set(features)

        self.stdout.write(
            f"Vehicles for {operator.noc}: "
            f"{created_count} created, {updated_count} updated"
        )
