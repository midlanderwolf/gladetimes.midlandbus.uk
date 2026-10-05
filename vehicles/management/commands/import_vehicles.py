import math
import re
from collections import defaultdict
from functools import reduce

import requests
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q
from busstops.models import Operator, OperatorGroup, DataSource
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
    # Uno is "UNOE" on bustimes.org, but stored locally as "UNIB"
    "UNOE": "UNIB",
    "LEMN": "LEMB",
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

    # older "dateless" format registration, e.g. "413 DCD", "413DCD" (3
    # letters), or an older/vintage-style dateless reg with only 1-2
    # letters, e.g. "9383 MX" (common on heritage vehicles) - checked
    # against the whole string, so it's not confused with a current-style
    # reg that happens to have a numeric prefix stuck to it
    match = re.fullmatch(r"(\d{1,4})[\s_-]?([A-Z]{1,3})", reg_str)
    if not match:
        # same, but with a trailing "_<fleet number>" left stuck on by a
        # bad prior import, e.g. "7236_PW_464" (should be "7236PW")
        match = re.fullmatch(r"(\d{1,4})[\s_-]?([A-Z]{1,3})[\s_-]\d{1,6}", reg_str)
    if match:
        return f"{match.group(1)}{match.group(2)}"

    # Northern Ireland-style registration (letters first, then digits),
    # e.g. "SRZ 7758", "SRZ7758" - checked against the whole string, same
    # as the dateless check above, so it's not confused with a
    # current-style reg
    match = re.fullmatch(r"([A-Z]{1,3})[\s_-]?(\d{1,4})", reg_str)
    if not match:
        # same, but with a trailing "_<fleet number>" left stuck on by a
        # bad prior import, e.g. "SRZ_7758_113" (should be "SRZ7758")
        match = re.fullmatch(r"([A-Z]{1,3})[\s_-]?(\d{1,4})[\s_-]\d{1,6}", reg_str)
    if match:
        return f"{match.group(1)}{match.group(2)}"

    # Strip leading junk (fleet numbers, separators, etc)
    reg_str = re.sub(r"^[^A-Z]*", "", reg_str)

    # Match UK registration with optional separators. The end is checked
    # with a negative lookahead rather than \b: \b treats "_" as a word
    # character, so it fails to find the boundary in e.g. "BU18_YRO_180"
    # (a reg with a trailing fleet number stuck on with underscores) and
    # the whole messy string falls through to the fallback below instead.
    match = re.search(
        r"\b([A-Z]{2}\d{2})[\s_-]?([A-Z]{3})(?![A-Z])",
        reg_str,
    )
    if match:
        return f"{match.group(1)}{match.group(2)}"

    # Fallback: just clean it
    return re.sub(r"[\s_-]", "", reg_str)


def update_vehicle(vehicle, defaults):
    """Apply `defaults` onto an existing `vehicle` and save it.

    Broader matching can find a vehicle whose own operator and/or code
    differ from this run's, and moving it onto the incoming (operator,
    code) can collide with a *different* vehicle that already sits there
    (e.g. an old vehicle imported under a garbled legacy code, once a
    fresh import correctly matches and wants to give it a clean one). That
    used to just silently keep the vehicle's old operator/code to dodge
    the unique-constraint violation, leaving both as separate stale
    duplicates forever. Now it merges the colliding vehicle in instead -
    reassigning its journey/revision history onto whichever of the two is
    the better keeper - so re-running an import actually converges
    duplicates down to one record. Returns the vehicle that ends up
    holding the data (`vehicle` itself, unless it was the one merged away).
    """
    moving = (
        defaults["operator"] != vehicle.operator or defaults["code"] != vehicle.code
    )
    if moving:
        colliding = (
            Vehicle.objects.filter(
                operator=defaults["operator"], code__iexact=defaults["code"]
            )
            .exclude(pk=vehicle.pk)
            .first()
        )
        if colliding:
            keeper = pick_keeper_vehicle([vehicle, colliding])
            duplicate = colliding if keeper.pk == vehicle.pk else vehicle
            # merge (which deletes `duplicate`) has to happen BEFORE
            # saving keeper's new (operator, code): if `duplicate` is the
            # one currently holding that pair, saving keeper under it
            # first collides with the still-existing duplicate row
            merge_vehicles(keeper, [duplicate])
            for key, value in defaults.items():
                setattr(keeper, key, value)
            keeper.save()
            return keeper

    for key, value in defaults.items():
        setattr(vehicle, key, value)
    vehicle.save()
    return vehicle


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


def merge_duplicate_vehicles_in_group(operator_group):
    """Convenience wrapper around `merge_duplicate_vehicles` for an
    `OperatorGroup` - merges duplicates (sharing a fleet number) across
    every operator currently belonging to the group, rather than having
    to pass an explicit list/queryset of operators.

    Returns how many duplicate vehicles were merged away.
    """
    return merge_duplicate_vehicles(Operator.objects.filter(group=operator_group))


@transaction.atomic
def merge_duplicate_vehicles_by_reg(operators=None):
    """Find vehicles that share a registration across *different*
    operators - unlike `merge_duplicate_vehicles`, not limited to
    operators in the same numbering-scheme group, since a registration
    (unlike a fleet number) genuinely identifies one physical vehicle no
    matter who it's currently operating for. Catches a vehicle that's
    been reallocated between two otherwise-unrelated operators, where an
    ordinary import run only ever looks for a match within its own
    operator(s) and so leaves a stale duplicate behind under the old one.

    If `operators` is given, only vehicles belonging to one of those
    operators are considered; otherwise every vehicle with a reg is
    considered, across the whole fleet.

    Returns how many duplicate vehicles were merged away.
    """
    vehicles = Vehicle.objects.exclude(reg="").select_related("latest_journey")
    if operators is not None:
        vehicles = vehicles.filter(operator__in=operators)

    groups = defaultdict(list)
    for vehicle in vehicles:
        groups[vehicle.reg.upper()].append(vehicle)

    merged_count = 0

    for group in groups.values():
        if len(group) < 2:
            continue
        # two vehicles under the *same* operator sharing a reg is a
        # different kind of problem (e.g. a bad import rather than a
        # reallocation) and not what this is for - leave those alone
        if len({vehicle.operator_id for vehicle in group}) < 2:
            continue

        keeper = pick_keeper_vehicle(group)
        duplicates = [vehicle for vehicle in group if vehicle.pk != keeper.pk]
        merged_count += merge_vehicles(keeper, duplicates)

    return merged_count


def parse_slug_tokens(slug, noc):
    """Best-effort extraction of a fleet-number-like and a registration-like
    token from a bustimes.org vehicle slug.

    Different operators' data on bustimes.org encodes vehicles into slugs in
    all sorts of ways - "opco-123" (fleet number only), "opco-123-ab12cde"
    (fleet number + reg), "opco-ab12-cde" (just a reg, with its own internal
    dash preserved), "opco-ab12cde" (just a reg, no dash) - and there's no
    reliable way to know which convention a given operator uses without
    parsing it. This widens vehicle matching to catch a physical vehicle
    under whichever convention it was previously imported/stored as
    locally, rather than only matching vehicles imported under the same
    --reg/--rto/--tmsb-format flags as the current run.

    Returns a (fleet_number, reg) tuple; either may be None/"" if not
    present in the slug.
    """
    if not slug or not noc:
        return None, ""

    slug = str(slug).lower()
    prefix = f"{noc.lower()}-"
    remainder = slug[len(prefix):] if slug.startswith(prefix) else slug
    parts = [part for part in remainder.split("-") if part]

    fleet_number = None
    reg_parts = []
    for part in parts:
        if fleet_number is None and part.isdigit():
            fleet_number = part
        else:
            reg_parts.append(part)

    reg = ""
    if reg_parts and any(char.isdigit() for char in "".join(reg_parts)):
        reg = normalize_registration("".join(reg_parts))

    return fleet_number, reg


def find_existing_vehicle(
    candidate_operators, code, fleet_number, reg, slug, extra_identifiers=None
):
    """Look for an existing vehicle belonging to any of these operators that
    matches any of the incoming identifying values, since different
    operators' data represents the same vehicle in different ways (e.g.
    one vehicle's fleet number might be stored as another's registration or
    slug), and a vehicle can move between operators in the same group.

    `extra_identifiers` can carry additional candidate values (e.g. from
    `parse_slug_tokens`) to widen the match beyond the values this run
    would otherwise have used for `code`/`fleet_number`/`reg`.
    """
    identifiers = {
        value
        for value in (code, str(fleet_number) if fleet_number else None, reg, slug)
        if value
    }
    if extra_identifiers:
        identifiers |= {value for value in extra_identifiers if value}
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

    matches = list(Vehicle.objects.filter(operator__in=candidate_operators).filter(query))

    # also catch a vehicle whose `code` still holds a garbled reg+fleet-
    # number combo from a bad prior import (e.g. "SN65_OAA_622", from a
    # --tmsb-format run gone wrong) - a plain exact-match lookup above
    # can't find these, since none of its own fields hold a clean value.
    # Scoped to just codes with a separator character in them (a clean
    # fleet number or reg never has one) so this stays cheap even against
    # a large fleet.
    matched_pks = {vehicle.pk for vehicle in matches}
    garbled_candidates = Vehicle.objects.filter(
        operator__in=candidate_operators, code__regex=r"[_\s-]"
    ).exclude(pk__in=matched_pks)
    for candidate in garbled_candidates:
        if normalize_registration(candidate.code) in identifiers:
            matches.append(candidate)

    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]

    # more than one local vehicle represents this same physical bus (e.g.
    # a cleanly-matched one plus a stale garbled-code duplicate that
    # nothing else would ever have found or touched) - merge them down to
    # one now, rather than returning just the first and leaving the rest
    # behind untouched forever
    keeper = pick_keeper_vehicle(matches)
    duplicates = [vehicle for vehicle in matches if vehicle.pk != keeper.pk]
    with transaction.atomic():
        merge_vehicles(keeper, duplicates)
    return keeper


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
            # Stagecoach's own data is already a single, consistent
            # fleet-number convention across the group, so there's nothing
            # for slug-scanning to disambiguate there - leave that bulk
            # pipeline untouched and only widen matching this way for
            # single-operator runs, where conventions vary operator to
            # operator and --reg/--rto/--tmsb-format have to be guessed.
            scan_slug = False
        elif operator_group := OperatorGroup.objects.filter(
            Q(slug__iexact=noc) | Q(name__iexact=noc)
        ).first():
            nocs = list(
                Operator.objects.filter(group=operator_group)
                .order_by("noc")
                .values_list("noc", flat=True)
            )
            if not nocs:
                raise CommandError(f'Operator group "{operator_group}" has no operators')
            remove_withdrawn = True
            scan_slug = False
        else:
            nocs = [noc]
            scan_slug = True

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
                scan_slug,
            )
            self.stdout.write(
                self.style.SUCCESS(f"Successfully imported vehicles for {operator_noc}")
            )

        if len(nocs) > 1:
            # a vehicle can get created/updated as a fresh duplicate under
            # the wrong operator mid-run (e.g. one operator's pass sees it
            # under a dead-running/placeholder operator before its real
            # operator's own pass confirms the correct one) - reconcile
            # those down to one now rather than leaving them duplicated
            # until the next run's pre-pass catches them
            merged = merge_duplicate_vehicles(candidate_operators)
            if merged:
                self.stdout.write(f"Merged {merged} duplicate vehicle(s)")

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
        scan_slug=False,
    ):
        # deliberately not wrapped in one big @transaction.atomic covering
        # the whole (potentially thousand-vehicle) run: that held locks on
        # every touched vehicle row for the run's full duration, which
        # deadlocked against the live AVL pipeline (huey/listen/
        # distribute_vehicle_locations) concurrently updating those same
        # rows. Each vehicle write below gets its own short-lived
        # transaction instead, and the import is idempotent/re-runnable, so
        # a mid-run failure just leaves the rest to be picked up by a
        # re-run rather than needing an all-or-nothing rollback.
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
        marked_withdrawn_count = 0

        # pks of vehicles created/updated as active (non-withdrawn) earlier
        # in this same run - never delete one of these below, even if a
        # later, stale duplicate entry in the source data (e.g. an old
        # withdrawn record for the same vehicle, sharing its registration)
        # matches it too. Without this, a genuinely-active vehicle can be
        # deleted purely because the source API still contains a leftover
        # withdrawn duplicate that happens to share an identifier with it.
        touched_vehicle_ids = set()

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

                # Determine vehicle operator (with remapping) - trust
                # bustimes.org's own per-vehicle "operator" field over the
                # NOC we queried for, when it names one of this run's own
                # candidate_operators (e.g. a group-mate). The API can
                # include a vehicle in a PLYC query that it itself
                # attributes to TFCN (another operator in the same
                # group/run); blindly stamping every result with this
                # pass's own `operator` would misattribute it, so prefer
                # the API's own answer whenever it's one we recognise.
                vehicle_operator = operator
                if vehicle_data.get("operator"):
                    api_operator_data = vehicle_data["operator"]
                    api_operator_noc = api_operator_data.get("id") if isinstance(api_operator_data, dict) else api_operator_data
                    candidate_by_noc = {
                        candidate.noc: candidate for candidate in candidate_operators
                    }
                    if api_operator_noc in candidate_by_noc:
                        vehicle_operator = candidate_by_noc[api_operator_noc]
                    elif api_operator_noc in OPERATOR_REMAP:
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

                # widen matching beyond this run's own --reg/--rto/
                # --tmsb-format convention, so a vehicle already stored
                # locally under a different one of bustimes.org's many
                # per-operator slug conventions still gets found
                extra_identifiers = None
                if scan_slug:
                    slug_fleet_number, slug_reg = parse_slug_tokens(
                        vehicle_data.get("slug"), api_noc
                    )
                    extra_identifiers = {slug_fleet_number, slug_reg}

                if vehicle_data.get("withdrawn"):
                    if remove_withdrawn:
                        vehicle = find_existing_vehicle(
                            candidate_operators,
                            code,
                            fleet_number,
                            reg,
                            vehicle_data.get("slug"),
                            extra_identifiers,
                        )
                        # a group-wide match can land on a vehicle that's
                        # *currently* homed under a different operator in
                        # the group (e.g. this entry is PLYC's stale/
                        # dead-running echo of a vehicle that's really,
                        # actively TFCN's) - that's not evidence the real
                        # vehicle is withdrawn, just that this operator's
                        # own feed doesn't have it any more, so only
                        # delete when it's still recorded under the
                        # operator this withdrawn entry itself came from
                        if (
                            vehicle
                            and vehicle.pk not in touched_vehicle_ids
                            and vehicle.operator_id == operator.pk
                        ):
                            with transaction.atomic():
                                vehicle.delete()
                            removed_count += 1
                        continue
                    if ignore_withdrawn:
                        # not deleting, and not doing the full
                        # create/update below - but if this withdrawn
                        # vehicle already matches one we have locally,
                        # still mark it withdrawn there rather than
                        # silently leaving it looking active. Same
                        # cross-operator caveat as above applies.
                        vehicle = find_existing_vehicle(
                            candidate_operators,
                            code,
                            fleet_number,
                            reg,
                            vehicle_data.get("slug"),
                            extra_identifiers,
                        )
                        if (
                            vehicle
                            and vehicle.pk not in touched_vehicle_ids
                            and vehicle.operator_id == operator.pk
                            and not vehicle.withdrawn
                        ):
                            with transaction.atomic():
                                vehicle.withdrawn = True
                                vehicle.save(update_fields=["withdrawn"])
                            touched_vehicle_ids.add(vehicle.pk)
                            marked_withdrawn_count += 1
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

                with transaction.atomic():
                    vehicle = find_existing_vehicle(
                        candidate_operators,
                        code,
                        fleet_number,
                        reg,
                        vehicle_data.get("slug"),
                        extra_identifiers,
                    )
                    if vehicle:
                        vehicle = update_vehicle(vehicle, defaults)
                        updated_count += 1
                    elif rto:
                        vehicle = Vehicle.objects.filter(code__iexact=code, operator__isnull=True).first()
                        if vehicle:
                            vehicle = update_vehicle(vehicle, defaults)
                            updated_count += 1
                        else:
                            vehicle = Vehicle.objects.create(**defaults)
                            created_count += 1
                    else:
                        vehicle = Vehicle.objects.create(**defaults)
                        created_count += 1

                    if features:
                        vehicle.features.set(features)

                touched_vehicle_ids.add(vehicle.pk)

            url = data.get("next")

        self.stdout.write(
            f"Vehicles for {operator.noc}: "
            f"{created_count} created, {updated_count} updated, "
            f"{marked_withdrawn_count} marked withdrawn, "
            f"{removed_count} removed"
        )
