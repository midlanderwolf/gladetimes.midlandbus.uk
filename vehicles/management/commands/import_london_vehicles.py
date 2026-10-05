import time

import requests
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from django.db.models import Q

from busstops.models import DataSource, Operator
from ...models import Livery, Vehicle, VehicleFeature, VehicleType
from ...utils import merge_vehicles, pick_keeper_vehicle
from .import_vehicles import (
    OPERATOR_REMAP,
    colours_from_css,
    normalize_fleet_number,
    normalize_registration,
)


# TFLO is a generic placeholder for "some TfL-contracted London operator, not
# yet known which one" - only ever re-home a vehicle onto one of TfL's actual
# contracted operators. A reg can coincidentally match an unrelated vehicle
# elsewhere in the country on bustimes.org (e.g. "GOCH"/Go Bus, nothing to do
# with London), and blindly trusting whatever operator the API returns would
# wrongly move a vehicle there.
LONDON_NOCS = [
    "AVLO",
    "BTRI",
    "DLBU",
    "FLON",
    "LONC",
    "LGEN",
    "MBGA",
    "MTLN",
    "ELBG",
    "ABLO",
    "UNIB",
    "FALC",
]


def pick_best_result(results, whitelist=LONDON_NOCS):
    """bustimes.org sometimes has more than one vehicle record for the same
    reg - typically a stale withdrawn entry left over under an old or
    entirely unrelated operator (e.g. "GOCH"/Go Bus, or a Plymouth Citybus
    record for a reg since reused in London), alongside the real, current
    one. Blindly taking the first result risks picking the wrong one, so
    prefer a non-withdrawn result whose operator is in `whitelist`, then
    any non-withdrawn result, then just give up and take the first.
    """
    def operator_noc(result):
        operator = result.get("operator")
        noc = operator.get("id") if isinstance(operator, dict) else operator
        return OPERATOR_REMAP.get((noc or "").upper(), (noc or "").upper())

    non_withdrawn = [result for result in results if not result.get("withdrawn")]
    for result in non_withdrawn:
        if operator_noc(result) in whitelist:
            return result
    if non_withdrawn:
        return non_withdrawn[0]
    return results[0]


class Command(BaseCommand):
    help = (
        "Re-home vehicles sitting under the generic TFLO operator, or "
        "parked with incomplete details under the wrong London operator, "
        "by looking each one up on the bustimes.org API by its "
        "registration, and adopting whatever real operator/fleet number/"
        "vehicle details bustimes.org has since recorded for it "
        "(bustimes.org moves London vehicles onto their actual operator "
        "once known; this site doesn't get that update any other way, "
        "since a normal single-operator import run only looks at "
        "vehicles bustimes.org already lists under that operator)."
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
        """Record why a TFLO vehicle was left alone this run, so it's
        visible on the site instead of just looking unprocessed - but never
        clobber a genuine pre-existing note that isn't one of ours.
        """
        if dry_run:
            return
        if vehicle.notes and not vehicle.notes.startswith("Ignored, "):
            return
        if vehicle.notes != note:
            vehicle.notes = note
            vehicle.save(update_fields=["notes"])

    def handle(self, *args, **options):
        try:
            tflo = Operator.objects.get(noc__iexact="TFLO")
        except Operator.DoesNotExist:
            raise CommandError('Operator with NOC "TFLO" not found')

        source, _ = DataSource.objects.get_or_create(
            name="bustimes.org", defaults={"url": "https://bustimes.org/"}
        )

        # most of these are TFLO's own generic placeholder vehicles, but a
        # vehicle can also get stuck under one of TfL's *actual* contracted
        # operators with none of its details ever filled in - e.g. the live
        # AVL pipeline resolves it onto whichever operator's service it
        # first saw it running (inter-working means that isn't necessarily
        # its real, contracted operator), and a normal `import_vehicles
        # <NOC>` run for that operator never revisits it because
        # bustimes.org's own vehicle list for that NOC doesn't contain it
        # (it genuinely belongs to a different operator there). Once its
        # `operator` is anything other than TFLO, this command has always
        # skipped it - a blank `reg` is what identifies one of these,
        # since a properly-imported vehicle always has one.
        vehicles = Vehicle.objects.filter(
            Q(operator=tflo) | Q(operator__noc__in=LONDON_NOCS, reg="")
        ).order_by("id")
        if options["limit"]:
            vehicles = vehicles[: options["limit"]]

        delay = options["delay"]
        dry_run = options["dry_run"]

        relocated_count = 0
        merged_count = 0
        not_found_count = 0
        unchanged_count = 0
        not_london_operator_count = 0
        no_reg_count = 0
        error_count = 0

        for vehicle in vehicles:
            # a lot of these vehicles were created by the live AVL pipeline
            # rather than a proper import, and have their registration
            # sitting in `code` (e.g. "LTZ1727") with `reg` left blank -
            # fall back to that so those aren't silently skipped
            raw_reg = vehicle.reg or vehicle.code
            if not raw_reg:
                no_reg_count += 1
                continue

            # the API's `reg` filter is an exact (case-insensitive) match,
            # so normalize first - covers every UK plate shape this fleet
            # actually uses: current (LX11BFP), dateless (185CLT), and
            # cherished/private (LTZ1137), whether or not the locally
            # stored value has a stray space or hyphen in it
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
            if not api_noc or api_noc.upper() == "TFLO":
                # bustimes.org doesn't know any better either yet
                unchanged_count += 1
                time.sleep(delay)
                continue

            # e.g. Uno is "UNOE" on bustimes.org but stored locally as
            # "UNIB" - resolve to the local NOC before checking the
            # whitelist or looking the operator up
            local_noc = OPERATOR_REMAP.get(api_noc.upper(), api_noc.upper())

            if local_noc not in LONDON_NOCS:
                # a reg can coincidentally match an unrelated vehicle
                # elsewhere in the country - don't move a TfL vehicle onto
                # some other operator on the strength of that alone. Leave
                # a note explaining why, rather than silently leaving it
                # stuck under TFLO with no visible explanation
                note = f"Ignored, no TFL op ({local_noc})"
                self.set_ignore_note(vehicle, note, dry_run)
                self.stdout.write(
                    f"{raw_reg}: {note}" + (" [dry run]" if dry_run else "")
                )
                not_london_operator_count += 1
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
                # e.g. some new-electric-bus fleet numbers only exist as a
                # fleet_code like "EA5", with fleet_number left null
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

            self.stdout.write(
                f"{raw_reg}: TFLO -> {local_noc} {code}"
                f" ({'merge' if existing else 'move'})"
                + (" [dry run]" if dry_run else "")
            )

            if dry_run:
                if existing:
                    merged_count += 1
                else:
                    relocated_count += 1
                time.sleep(delay)
                continue

            with transaction.atomic():
                if existing:
                    # a normal `import_vehicles <NOC>` run already created
                    # this vehicle under its real operator - fold the TFLO
                    # record's journey/revision history into that one
                    # rather than leaving two rows for one physical bus.
                    # Merge (which deletes `duplicate`) has to happen
                    # BEFORE saving keeper's new (operator, code): if
                    # `duplicate` is the one currently holding that
                    # (operator, code) pair, saving keeper under it first
                    # collides with the still-existing duplicate row.
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
                    relocated_count += 1

            time.sleep(delay)

        self.stdout.write(
            f"TFLO vehicles: {relocated_count} relocated, {merged_count} merged, "
            f"{not_found_count} not found on bustimes.org, "
            f"{unchanged_count} still unassigned on bustimes.org, "
            f"{not_london_operator_count} matched a non-TfL operator (skipped), "
            f"{no_reg_count} skipped (no reg or reg-like code), {error_count} errors"
        )
