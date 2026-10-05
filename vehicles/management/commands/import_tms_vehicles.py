import time

import requests
from django.core.management.base import BaseCommand
from django.db import transaction

from busstops.models import DataSource, Operator
from ...models import Livery, Vehicle, VehicleFeature, VehicleType
from ...utils import merge_vehicles, pick_keeper_vehicle
from .import_vehicles import (
    OPERATOR_REMAP,
    colours_from_css,
    merge_duplicate_vehicles,
    normalize_fleet_number,
    normalize_registration,
)


# SIMO (Simonds), SEFF (Flagfinders) and CHAM have all been folded into
# the "Transport Made Simple" group on bustimes.org - none of them exist
# there as an operator any more (CHAM and SIMO not at all; SEFF only has
# stale withdrawn leftovers under its own code), with every vehicle's
# live, non-withdrawn record now sitting under one of KCTB/TMSB/HEDO.
# Those three are included too so their own vehicles get looked up and
# corrected/deduped in the same pass, not just the defunct codes.
TMS_SCAN_NOCS = ["KCTB", "SIMO", "SEFF", "TMSB", "HEDO", "CHAM"]
TMS_LIVE_NOCS = ["KCTB", "TMSB", "HEDO", "CHAM"]


def resolve_tms_noc(api_noc):
    """Map an API-returned operator NOC onto the local NOC to use for it."""
    api_noc = (api_noc or "").upper()
    return OPERATOR_REMAP.get(api_noc, api_noc)


def pick_best_result(results, whitelist=TMS_LIVE_NOCS):
    """bustimes.org sometimes has more than one vehicle record for the same
    reg - typically a stale withdrawn entry left over under a defunct
    operator (SIMO/SEFF), alongside the real, current one under KCTB/TMSB/
    HEDO. Blindly taking the first result risks picking the wrong one, so
    prefer a non-withdrawn result whose operator is in `whitelist`, then
    any non-withdrawn result, then just give up and take the first.
    """
    def operator_noc(result):
        operator = result.get("operator")
        noc = operator.get("id") if isinstance(operator, dict) else operator
        return resolve_tms_noc(noc)

    non_withdrawn = [result for result in results if not result.get("withdrawn")]
    for result in non_withdrawn:
        if operator_noc(result) in whitelist:
            return result
    if non_withdrawn:
        return non_withdrawn[0]
    return results[0]


class Command(BaseCommand):
    help = (
        "Re-home vehicles sitting under the defunct SIMO/SEFF operator "
        "codes, and correct/dedupe vehicles already under KCTB/TMSB/HEDO, "
        "by looking each one up on the bustimes.org API by its "
        "registration, and adopting whatever real operator/fleet number/"
        "vehicle details bustimes.org has since recorded for it (this "
        "site doesn't get that update any other way, since a normal "
        "single-operator import run only looks at vehicles bustimes.org "
        "already lists under that operator, and bustimes.org no longer "
        "lists any under SIMO/SEFF at all)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Only process this many vehicles (for testing)",
        )
        parser.add_argument(
            "--delay",
            type=float,
            default=0.1,
            help="Seconds to pause between API requests (default 0.1)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would change without saving anything",
        )

    def set_ignore_note(self, vehicle, note, dry_run):
        """Record why a vehicle was left alone this run, so it's visible on
        the site instead of just looking unprocessed - but never clobber a
        genuine pre-existing note that isn't one of ours.
        """
        if dry_run:
            return
        if vehicle.notes and not vehicle.notes.startswith("Ignored, "):
            return
        if vehicle.notes != note:
            vehicle.notes = note
            vehicle.save(update_fields=["notes"])

    def handle(self, *args, **options):
        operators = {
            noc: Operator.objects.filter(noc__iexact=noc).first()
            for noc in TMS_SCAN_NOCS
        }
        for noc, operator in operators.items():
            if operator is None:
                self.stderr.write(f'Operator with NOC "{noc}" not found locally, skipping')

        candidate_operators = [op for op in operators.values() if op is not None]
        if not candidate_operators:
            self.stderr.write(f"None of {'/'.join(TMS_SCAN_NOCS)} found locally")
            return

        source, _ = DataSource.objects.get_or_create(
            name="bustimes.org", defaults={"url": "https://bustimes.org/"}
        )

        # Transport Made Simple uses one fleet-numbering scheme across all
        # its operating companies, so two local vehicles under different
        # operators in this group sharing a fleet number are really the
        # same physical bus (most likely reallocated between companies, or
        # left behind under a now-defunct code), not a coincidence - merge
        # those down to one record before the per-vehicle reg lookups
        # below, same as the Stagecoach bulk import does
        if options["dry_run"]:
            self.stdout.write("Skipping fleet-number merge pass [dry run]")
        else:
            merged = merge_duplicate_vehicles(candidate_operators)
            if merged:
                self.stdout.write(f"Merged {merged} duplicate vehicle(s) by fleet number")

        vehicles = Vehicle.objects.filter(
            operator__in=candidate_operators
        ).order_by("id")
        if options["limit"]:
            vehicles = vehicles[: options["limit"]]

        delay = options["delay"]
        dry_run = options["dry_run"]

        relocated_count = 0
        corrected_count = 0
        merged_count = 0
        not_found_count = 0
        not_tms_operator_count = 0
        no_reg_count = 0
        error_count = 0

        for vehicle in vehicles:
            raw_reg = vehicle.reg or vehicle.code
            if not raw_reg:
                no_reg_count += 1
                continue

            query_reg = normalize_registration(raw_reg)
            try:
                response = requests.get(
                    "https://bustimes.org/api/vehicles/",
                    params={"format": "json", "reg": query_reg},
                    timeout=10,
                )
                response.raise_for_status()
                data = response.json()
            except requests.RequestException as e:
                self.stderr.write(f"{raw_reg}: request failed ({e})")
                error_count += 1
                time.sleep(delay)
                continue

            results = data.get("results") or []
            if not results:
                note = "Ignored, not found on bustimes.org"
                self.set_ignore_note(vehicle, note, dry_run)
                self.stdout.write(
                    f"{raw_reg}: {note}" + (" [dry run]" if dry_run else "")
                )
                not_found_count += 1
                time.sleep(delay)
                continue

            vehicle_data = pick_best_result(results)
            api_operator = vehicle_data.get("operator")
            api_noc = (
                api_operator.get("id")
                if isinstance(api_operator, dict)
                else api_operator
            )
            if not api_noc:
                not_found_count += 1
                time.sleep(delay)
                continue

            local_noc = resolve_tms_noc(api_noc)

            if local_noc not in TMS_LIVE_NOCS:
                # a reg can coincidentally match an unrelated vehicle
                # elsewhere in the country - don't move a TMS vehicle onto
                # some other operator on the strength of that alone
                note = f"Ignored, no TMS op ({local_noc})"
                self.set_ignore_note(vehicle, note, dry_run)
                self.stdout.write(
                    f"{raw_reg}: {note}" + (" [dry run]" if dry_run else "")
                )
                not_tms_operator_count += 1
                time.sleep(delay)
                continue

            try:
                target_operator = Operator.objects.get(noc__iexact=local_noc)
            except Operator.DoesNotExist:
                self.stderr.write(
                    f"{raw_reg}: operator {local_noc} not found locally"
                )
                error_count += 1
                time.sleep(delay)
                continue

            fleet_number = vehicle_data.get("fleet_number")
            if fleet_number:
                code = normalize_fleet_number(fleet_number)
            elif vehicle_data.get("fleet_code"):
                code = vehicle_data["fleet_code"]
            else:
                code = query_reg

            vehicle_type = None
            if vehicle_data.get("vehicle_type"):
                vehicle_type = VehicleType.objects.filter(
                    name=vehicle_data["vehicle_type"]["name"]
                ).first()

            livery = None
            colours = ""
            livery_data = vehicle_data.get("livery")
            if livery_data:
                if livery_data.get("id"):
                    livery, _ = Livery.objects.get_or_create(
                        id=livery_data["id"],
                        defaults={"name": livery_data.get("name", "")},
                    )
                else:
                    left = livery_data.get("left")
                    right = livery_data.get("right")
                    if left and "gradient" in left:
                        colours = colours_from_css(left)
                    elif right and "gradient" in right:
                        colours = colours_from_css(right)
                    elif left and left == right:
                        colours = left
                    elif left or right:
                        colours = " ".join(c for c in (left, right) if c)

            features = []
            for feature_name in vehicle_data.get("special_features") or []:
                feature, _ = VehicleFeature.objects.get_or_create(name=feature_name)
                features.append(feature)

            defaults = {
                "operator": target_operator,
                "code": code,
                "fleet_number": fleet_number,
                "fleet_code": vehicle_data.get("fleet_code"),
                "reg": (
                    normalize_registration(vehicle_data.get("reg"))
                    if vehicle_data.get("reg")
                    else query_reg
                ),
                "vehicle_type": vehicle_type,
                "livery": livery,
                "colours": colours,
                "name": vehicle_data.get("name", ""),
                "branding": vehicle_data.get("branding", ""),
                "notes": vehicle_data.get("notes", ""),
                "withdrawn": vehicle_data.get("withdrawn", False),
                "source": source,
            }

            existing = (
                Vehicle.objects.filter(operator=target_operator, code__iexact=code)
                .exclude(pk=vehicle.pk)
                .first()
            )

            moving = vehicle.operator_id != target_operator.pk
            if moving:
                action = "merge" if existing else "move"
                self.stdout.write(
                    f"{raw_reg}: {vehicle.operator_id} -> {local_noc} {code}"
                    f" ({action})" + (" [dry run]" if dry_run else "")
                )
            elif existing:
                self.stdout.write(
                    f"{raw_reg}: {local_noc} {code} (merge)"
                    + (" [dry run]" if dry_run else "")
                )
            else:
                self.stdout.write(
                    f"{raw_reg}: {local_noc} {code} (correct)"
                    + (" [dry run]" if dry_run else "")
                )

            if dry_run:
                if existing:
                    merged_count += 1
                elif moving:
                    relocated_count += 1
                else:
                    corrected_count += 1
                time.sleep(delay)
                continue

            with transaction.atomic():
                if existing:
                    # a vehicle already sits under (target_operator, code) -
                    # fold this one's journey/revision history into it
                    # rather than leaving two rows for one physical bus.
                    # Merge (which deletes `duplicate`) has to happen
                    # BEFORE saving keeper's new (operator, code): if
                    # `duplicate` is the one currently holding that pair,
                    # saving keeper under it first collides with the
                    # still-existing duplicate row
                    keeper = pick_keeper_vehicle([vehicle, existing])
                    duplicate = existing if keeper.pk == vehicle.pk else vehicle
                    merge_vehicles(keeper, [duplicate])
                    for key, value in defaults.items():
                        setattr(keeper, key, value)
                    keeper.save()
                    if features:
                        keeper.features.add(*features)
                    merged_count += 1
                else:
                    for key, value in defaults.items():
                        setattr(vehicle, key, value)
                    vehicle.save()
                    if features:
                        vehicle.features.set(features)
                    if moving:
                        relocated_count += 1
                    else:
                        corrected_count += 1

            time.sleep(delay)

        self.stdout.write(
            f"TMS vehicles: {relocated_count} relocated, {corrected_count} corrected, "
            f"{merged_count} merged, {not_found_count} not found on bustimes.org, "
            f"{not_tms_operator_count} matched a non-TMS operator (skipped), "
            f"{no_reg_count} skipped (no reg or reg-like code), {error_count} errors"
        )
