import math
import re
from collections import defaultdict
from functools import reduce

import requests
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q
from busstops.models import Operator, DataSource
from ...utils import merge_vehicles, pick_keeper_vehicle
from ...models import VehicleType, Livery, VehicleFeature, Vehicle


SPARE_TICKET_MACHINE_NOTES = "Spare ticket machine"

GRADIENT_STOP_RE = re.compile(r"(#[0-9a-fA-F]{3,8})\s*(\d+(?:\.\d+)?)%")

# keep the imported colours list short enough to fit in the field
# (up to 9-char hex tokens + a space, and max_length=255 on the field)
MAX_COLOUR_TOKENS = 25

OPERATOR_REMAP = {
    "ie-978": "ie-1",
    "ie-1": "ie-2",
    "ie-01": "ie-2",
    "ie-7778008": "ie-WFRD",
    "ie-7778306": "ie-03C",
}

# "stagecoach" as the noc argument runs the import for all of these in one go
STAGECOACH_NOCS = [
    "BNSM",
    # "MNSC",
    "SCCU",
    "SCCM",
    "SCEM",
    "SCFI",
    "SCMY",
    "SCNH",
    "SCNE",
    "SBLB",
    "SCOX",
    "SCSO",
    "SCEK",
    "SSWL",
    "SDVN",
    "SCGL",
    "STWS",
    "SYRK",
]


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


def colours_from_css(css):
    """Turn a CSS colour string, as returned by the API's `livery.left`/
    `livery.right` for a vehicle with no proper Livery record of its own,
    into a value for the vehicle's `colours` field.

    This is either just the flat hex colour itself, or - if it's a
    `linear-gradient(...)` of several colour stops - a space-separated
    list of those colours, each repeated to approximate its share of the
    gradient's width (the `colours` field doesn't support explicit
    percentages, only equally-sized stripes).
    """
    if not css:
        return ""

    if "gradient" not in css:
        return css

    stops = GRADIENT_STOP_RE.findall(css)
    if not stops:
        return ""

    colours = list(dict.fromkeys(colour for colour, _ in stops))
    if len(colours) == 1:
        return colours[0]

    boundaries = sorted({float(pct) for _, pct in stops})
    edges = [0.0, *boundaries, 100.0]
    if len(edges) != len(colours) + 1:
        # unexpected shape - fall back to an unweighted list of the colours
        return " ".join(colours)

    widths = [max(1, round(edges[i + 1] - edges[i])) for i in range(len(colours))]

    divisor = reduce(math.gcd, widths)
    counts = [width // divisor for width in widths]

    if sum(counts) > MAX_COLOUR_TOKENS:
        total = sum(widths)
        counts = [
            max(1, round(width / total * MAX_COLOUR_TOKENS)) for width in widths
        ]

    return " ".join(
        colour for colour, count in zip(colours, counts) for _ in range(count)
    )


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

    # older "dateless" format registration, e.g. "413 DCD", "413DCD"
    # (checked against the whole string, so it's not confused with a
    # current-style reg that happens to have a numeric prefix stuck to it)
    match = re.fullmatch(r"(\d{1,4})[\s_-]?([A-Z]{3})", reg_str)
    if match:
        return f"{match.group(1)}{match.group(2)}"

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


def update_vehicle(vehicle, defaults):
    """Apply `defaults` onto an existing `vehicle` and save it, without
    moving it onto an (operator, code) combination that's already taken by
    a different vehicle (which would violate the unique operator+code
    constraint) - broader matching can find a vehicle whose own operator
    and/or code differ from this run's, when another vehicle already owns
    that (operator, code) pair.
    """
    if (
        (defaults["operator"] != vehicle.operator or defaults["code"] != vehicle.code)
        and Vehicle.objects.filter(
            operator=defaults["operator"], code__iexact=defaults["code"]
        )
        .exclude(pk=vehicle.pk)
        .exists()
    ):
        defaults = {**defaults, "operator": vehicle.operator, "code": vehicle.code}

    for key, value in defaults.items():
        setattr(vehicle, key, value)
    vehicle.save()


@transaction.atomic
def merge_duplicate_vehicles(operators):
    """Some operator groups (e.g. Stagecoach) use one fleet-numbering scheme
    across all their operating companies, so if two vehicles under
    different operators in the group share a fleet number, they're really
    the same physical vehicle - most likely reallocated between companies -
    not a coincidence. Merge such duplicates down to one vehicle (keeping
    journey/revision history, features, and whichever's been active more
    recently), so a subsequent import can find and correctly update/re-home
    a single, correctly-identified vehicle instead of leaving a stale
    duplicate behind under its old operator.

    Returns how many duplicate vehicles were merged away.
    """
    groups = defaultdict(list)
    for vehicle in Vehicle.objects.filter(
        operator__in=operators, fleet_number__isnull=False
    ).select_related("latest_journey"):
        groups[vehicle.fleet_number].append(vehicle)

    merged_count = 0

    for group in groups.values():
        if len(group) < 2:
            continue

        keeper = pick_keeper_vehicle(group)
        duplicates = [vehicle for vehicle in group if vehicle.pk != keeper.pk]
        merged_count += merge_vehicles(keeper, duplicates)

    return merged_count


def find_existing_vehicle(candidate_operators, code, fleet_number, reg, slug):
    """Look for an existing vehicle belonging to any of these operators that
    matches any of the incoming identifying values, since different
    operators' data represents the same vehicle in different ways (e.g.
    one vehicle's fleet number might be stored as another's registration or
    slug), and a vehicle can move between operators in the same group.
    """
    identifiers = {
        value
        for value in (code, str(fleet_number) if fleet_number else None, reg, slug)
        if value
    }
    if not identifiers:
        return None

    query = Q()
    for value in identifiers:
        query |= (
            Q(code__iexact=value)
            | Q(fleet_code__iexact=value)
            | Q(reg__iexact=value)
            | Q(slug__iexact=value)
        )
        if value.isdigit():
            query |= Q(fleet_number=int(value))

    return Vehicle.objects.filter(operator__in=candidate_operators).filter(query).first()


def resolve_operator(noc):
    """Look up an Operator by NOC, falling back to OPERATOR_REMAP."""
    try:
        return Operator.objects.get(noc__iexact=noc), noc
    except Operator.DoesNotExist:
        if noc in OPERATOR_REMAP:
            remapped_noc = OPERATOR_REMAP[noc]
            try:
                return Operator.objects.get(noc__iexact=remapped_noc), noc
            except Operator.DoesNotExist:
                raise CommandError(f'Operator with NOC "{remapped_noc}" not found')
        raise CommandError(f'Operator with NOC "{noc}" not found')


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
            "--include-withdrawn",
            action="store_true",
            help="Also import vehicles that are marked as withdrawn "
            "(by default withdrawn vehicles are skipped)",
        )
        parser.add_argument(
            "--remove-withdrawn",
            action="store_true",
            help="Delete any matching local vehicle when the source marks it as "
            "withdrawn, instead of just skipping/importing it",
        )
        parser.add_argument(
            "--rto",
            action="store_true",
            help="Use RTO format (raw reg, strip NOC prefix from slug)",
        )

    def handle(self, *args, **options):
        noc = options["noc"]
        use_reg = options["reg"]
        tmsb_format = options["tmsb_format"]
        ignore_withdrawn = not options["include_withdrawn"]
        remove_withdrawn = options["remove_withdrawn"]
        rto = options["rto"]

        if noc.lower() == "stagecoach":
            nocs = STAGECOACH_NOCS
            # a bulk Stagecoach run always cleans up withdrawn vehicles,
            # whether or not --remove-withdrawn was passed
            remove_withdrawn = True
        else:
            nocs = [noc]

        source, _ = DataSource.objects.get_or_create(
            name="bustimes.org", defaults={"url": "https://bustimes.org/"}
        )

        resolved = {}
        for operator_noc in nocs:
            try:
                resolved[operator_noc] = resolve_operator(operator_noc)
            except CommandError as e:
                if len(nocs) == 1:
                    raise
                # don't let one bad NOC stop the rest of the batch
                self.stderr.write(str(e))

        # every operator in this run, so a vehicle can be found (and
        # re-homed) even if it's currently sitting under a different one of
        # them, e.g. after being reallocated between operating companies
        candidate_operators = [operator for operator, _ in resolved.values()]

        if len(nocs) > 1:
            merged = merge_duplicate_vehicles(candidate_operators)
            if merged:
                self.stdout.write(f"Merged {merged} duplicate vehicle(s)")

        for operator_noc, (operator, api_noc) in resolved.items():
            self.import_vehicles(
                operator,
                source,
                use_reg,
                tmsb_format,
                ignore_withdrawn,
                api_noc,
                rto,
                remove_withdrawn,
                candidate_operators,
            )
            self.stdout.write(
                self.style.SUCCESS(f"Successfully imported vehicles for {operator_noc}")
            )

    @transaction.atomic
    def import_vehicles(
        self,
        operator,
        source,
        use_reg,
        tmsb_format=False,
        ignore_withdrawn=False,
        api_noc=None,
        rto=False,
        remove_withdrawn=False,
        candidate_operators=None,
    ):
        if api_noc is None:
            api_noc = operator.noc
        if candidate_operators is None:
            candidate_operators = [operator]
        url = (
            "https://bustimes.org/api/vehicles/"
            f"?format=json&limit=9999&operator={api_noc}"
        )

        created_count = 0
        updated_count = 0
        removed_count = 0

        while url:
            try:
                response = requests.get(url)
                response.raise_for_status()
                data = response.json()
            except requests.RequestException as e:
                raise CommandError(f"Failed to fetch data from API: {e}")

            for vehicle_data in data["results"]:
                if vehicle_data.get("notes") == SPARE_TICKET_MACHINE_NOTES:
                    continue

                # Determine vehicle operator (with remapping)
                vehicle_operator = operator
                if vehicle_data.get("operator"):
                    api_operator_data = vehicle_data["operator"]
                    api_operator_noc = api_operator_data.get("id") if isinstance(api_operator_data, dict) else api_operator_data
                    if api_operator_noc in OPERATOR_REMAP:
                        remapped_noc = OPERATOR_REMAP[api_operator_noc]
                        try:
                            vehicle_operator = Operator.objects.get(noc__iexact=remapped_noc)
                        except Operator.DoesNotExist:
                            vehicle_operator = operator

                # Determine vehicle code
                if rto:
                    slug = vehicle_data.get("slug", "")
                    slug_parts = slug.split("-")
                    if len(slug_parts) >= 3:
                        code = f"{slug_parts[0]}-{slug_parts[-1]}"
                    elif vehicle_operator.noc in ["ie-1", "ie-2", "ie-03C"]:
                        code = slug_parts[-1] if len(slug_parts) == 2 else slug
                        if code.upper().startswith("LH"):
                            code = code[2:]
                    else:
                        code = slug
                elif use_reg and vehicle_data.get("reg"):
                    code = normalize_registration(vehicle_data["reg"], tmsb_format)
                elif tmsb_format:
                    slug = vehicle_data.get("slug", "")
                    if slug and slug.startswith("tmsb-"):
                        slug_parts = slug.split("-")
                        if len(slug_parts) >= 4:
                            reg_part = "-".join(slug_parts[1:])
                            code = normalize_registration(reg_part, tmsb_format)
                        elif vehicle_data.get("reg"):
                            code = normalize_registration(
                                vehicle_data["reg"], tmsb_format
                            )
                        else:
                            code = normalize_slug(slug)
                    else:
                        code = normalize_slug(slug)
                elif vehicle_data.get("fleet_number"):
                    code = normalize_fleet_number(vehicle_data["fleet_number"])
                else:
                    code = normalize_slug(vehicle_data.get("slug"))

                fleet_number = (
                    normalize_fleet_number(vehicle_data.get("fleet_number"))
                    if vehicle_data.get("fleet_number")
                    else None
                )
                if rto:
                    reg = vehicle_data.get("reg")
                elif vehicle_data.get("reg"):
                    reg = normalize_registration(vehicle_data.get("reg"), tmsb_format)
                else:
                    reg = ""

                if vehicle_data.get("withdrawn"):
                    if remove_withdrawn:
                        vehicle = find_existing_vehicle(
                            candidate_operators,
                            code,
                            fleet_number,
                            reg,
                            vehicle_data.get("slug"),
                        )
                        if vehicle:
                            vehicle.delete()
                            removed_count += 1
                        continue
                    if ignore_withdrawn:
                        continue

                # Vehicle type
                vehicle_type = None
                if vehicle_data.get("vehicle_type"):
                    vehicle_type = VehicleType.objects.filter(
                        name=vehicle_data["vehicle_type"]["name"]
                    ).first()

                # Livery - either a proper Livery record (matched by id),
                # or (if the vehicle just has a plain colour or two, with no
                # actual Livery of its own) the raw colour(s) straight onto
                # the vehicle, so it doesn't just end up blank
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
                            colours = " ".join(
                                colour for colour in (left, right) if colour
                            )

                vehicle_data_dict = {}
                if vehicle_data.get("previous_reg"):
                    vehicle_data_dict["Previous reg"] = vehicle_data["previous_reg"]

                defaults = {
                    "code": code,
                    "fleet_number": fleet_number,
                    "fleet_code": vehicle_data.get("fleet_code"),
                    "reg": reg,
                    "operator": vehicle_operator,
                    "source": source,
                    "vehicle_type": vehicle_type,
                    "livery": livery,
                    "colours": colours,
                    "name": vehicle_data.get("name", ""),
                    "branding": vehicle_data.get("branding", ""),
                    "notes": vehicle_data.get("notes", ""),
                    "withdrawn": vehicle_data.get("withdrawn", False),
                    "data": vehicle_data_dict or None,
                }

                # Features (API can return null)
                features = []
                special_features = vehicle_data.get("special_features") or []

                for feature_name in special_features:
                    feature, _ = VehicleFeature.objects.get_or_create(name=feature_name)
                    features.append(feature)

                vehicle = find_existing_vehicle(
                    candidate_operators,
                    code,
                    fleet_number,
                    reg,
                    vehicle_data.get("slug"),
                )
                if vehicle:
                    update_vehicle(vehicle, defaults)
                    updated_count += 1
                elif rto:
                    vehicle = Vehicle.objects.filter(code__iexact=code, operator__isnull=True).first()
                    if vehicle:
                        update_vehicle(vehicle, defaults)
                        updated_count += 1
                    else:
                        vehicle = Vehicle.objects.create(**defaults)
                        created_count += 1
                else:
                    vehicle = Vehicle.objects.create(**defaults)
                    created_count += 1

                if features:
                    vehicle.features.set(features)

            url = data.get("next")

        self.stdout.write(
            f"Vehicles for {operator.noc}: "
            f"{created_count} created, {updated_count} updated, "
            f"{removed_count} removed"
        )
