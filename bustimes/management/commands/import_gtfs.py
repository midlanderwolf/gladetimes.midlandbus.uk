import logging
from pathlib import Path
import gtfs_kit
from shapely.errors import EmptyPartError
from zipfile import BadZipFile
from django.conf import settings
from django.contrib.gis.geos import GEOSGeometry
from django.core.management.base import BaseCommand
from django.db import connection
from django.db import models as django_models
from django.db.models import Count, Exists, OuterRef, Q
from django.db.models.functions import Now
from django.utils.dateparse import parse_duration

from busstops.models import AdminArea, DataSource, Operator, Region, Service, StopPoint

from ...download_utils import download_if_modified
from ...utils import log_time_taken
from ...models import Route, Trip
from ...gtfs_utils import get_calendars, MODES, do_route_links
from .gtfs_utils import detect_region_from_feed, ensure_region_exists

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Import data even if the GTFS feeds haven't changed",
        )
        parser.add_argument("collections", nargs="*", type=str)

    def handle_operator(self, line, region_id=None):
        agency_id = line.agency_id
        name = line.agency_name

        # Always ensure NOC is max 10 chars
        if region_id:
            # Generate: US-MTAB (region + up to 4 chars from name)
            import re

            clean_name = re.sub(r"[^A-Za-z0-9]", "", name.upper())[:4]
            agency_id = f"{region_id}-{clean_name}"
        # Truncate to max 10 chars
        agency_id = agency_id[:10]

        operator = Operator.objects.filter(
            Q(name__iexact=name) | Q(noc=agency_id)
        ).first()

        if not operator:
            operator = Operator(name=name, noc=agency_id, url=line.agency_url)
            if region_id:
                operator.region_id = region_id
            operator.save()
        else:
            if region_id and operator.region_id != region_id:
                operator.region_id = region_id
                operator.save(update_fields=["region_id"])
            if operator.url != line.agency_url:
                operator.url = line.agency_url
                operator.save(update_fields=["url"])

        return operator

    def do_stops(self, feed: gtfs_kit.feed.Feed) -> dict[str, StopPoint]:
        stops = {}
        admin_areas = {}
        # Prefix stop IDs with region code to avoid AdminArea conflicts
        prefix = f"{self.region_id}-" if self.region_id else ""
        for _, line in feed.stops.iterrows():
            stop_id = f"{prefix}{line.stop_id}"
            stop = StopPoint(
                atco_code=stop_id,
                common_name=line.stop_name,
                latlong=GEOSGeometry(f"POINT({line.stop_lon} {line.stop_lat})"),
                locality_centre=False,
                active=True,
                source=self.source,
            )
            if ", stop" in stop.common_name and stop.common_name.count(", ") == 1:
                stop.common_name, stop.indicator = stop.common_name.split(", ")
            stop.common_name = stop.common_name[:48]
            stops[line.stop_id] = stop  # Use original ID as key for lookups
        existing_stops = StopPoint.objects.only(
            "atco_code", "common_name", "latlong", "source_id"
        ).in_bulk([s.atco_code for s in stops.values()])

        stops_to_create = [
            stop for stop in stops.values() if stop.atco_code not in existing_stops
        ]
        stops_to_update = [
            stop
            for stop in stops.values()
            if stop.atco_code in existing_stops
            and existing_stops[stop.atco_code].source_id in (self.source.id, None)
            and (
                existing_stops[stop.atco_code].latlong != stop.latlong
                or existing_stops[stop.atco_code].common_name != stop.common_name
            )
        ]
        StopPoint.objects.bulk_update(
            stops_to_update, ["common_name", "latlong", "indicator", "source"]
        )

        # Create or get admin area for this region
        if self.region_id:
            from .gtfs_utils import get_region_name

            country_name = get_region_name(self.region_id)
            max_id = (
                AdminArea.objects.aggregate(django_models.Max("id"))["id__max"] or 0
            )
            admin_area, created = AdminArea.objects.get_or_create(
                region_id=self.region_id,
                defaults={
                    "id": max_id + 1,
                    "name": country_name,
                    "atco_code": self.region_id[:3],
                },
            )
            if created:
                logger.info(
                    f"Created admin area {admin_area.id} ({country_name}) for region {self.region_id}"
                )
            admin_area_id = admin_area.id

        for stop in stops_to_create:
            if self.region_id:
                # Assign stops to the region's admin area
                stop.admin_area_id = admin_area_id

        StopPoint.objects.bulk_create(stops_to_create, batch_size=1000)
        # Return mapping of original stop_id -> StopPoint for trip lookups
        # Need to re-fetch stops to get proper IDs
        all_stop_ids = [stop.atco_code for stop in stops.values()]
        stop_objects = StopPoint.objects.only("atco_code", "latlong").in_bulk(
            all_stop_ids
        )
        result = {}
        for original_id, stop in stops.items():
            if stop.atco_code in stop_objects:
                result[original_id] = stop_objects[stop.atco_code]
        return result

    def handle_route(self, line):
        line_name = line.route_short_name if type(line.route_short_name) is str else ""
        description = line.route_long_name if type(line.route_long_name) is str else ""
        if not line_name and " " not in description:
            line_name = description
            if len(line_name) < 5:
                description = ""

        operator = self.operators.get(line.agency_id)
        services = Service.objects.filter(operator=operator)

        q = Exists(
            Route.objects.filter(code=line.route_id, service=OuterRef("id"))
        ) | Q(service_code=line.route_id)

        if line_name and line_name not in ("rail", "InterCity"):
            q |= Q(line_name__iexact=line_name)
        elif description:
            q |= Q(description=description)

        service = services.filter(q).order_by("id").first()
        if not service:
            service = Service(source=self.source)

        service.service_code = line.route_id
        service.line_name = line_name
        service.description = description
        if line.route_type in MODES:
            service.mode = MODES[line.route_type]
        else:
            logger.warning("unknown route type %s", line)
        service.current = True
        service.source = self.source
        if self.region_id:
            service.region_id = self.region_id
        service.save()

        if operator:
            if service.id in self.services:
                service.operator.add(operator)
            else:
                service.operator.set([operator])
        self.services[service.id] = service

        route, created = Route.objects.update_or_create(
            {
                "line_name": service.line_name,
                "description": service.description,
                "service": service,
            },
            source=self.source,
            code=line.route_id,
        )
        if not created:
            route.trip_set.all().delete()
        self.routes[line.route_id] = route
        self.route_operators[line.route_id] = operator

    def handle_zipfile(self, path, region_id=None):
        feed = gtfs_kit.read_feed(path, dist_units="km")

        # Detect region from feed if not provided
        if not region_id:
            region_id = detect_region_from_feed(feed)
            if region_id:
                ensure_region_exists(region_id)

        self.region_id = region_id
        self.operators = {}
        self.routes = {}
        self.route_operators = {}
        self.services = {}

        for agency in feed.agency.itertuples():
            self.operators[agency.agency_id] = self.handle_operator(agency, region_id)

        for route in feed.routes.itertuples():
            self.handle_route(route)

        try:
            for route in feed.get_routes(as_gdf=True).itertuples():
                self.routes[route.route_id].service.geometry = route.geometry.wkt
                if route.geometry:
                    self.routes[route.route_id].service.save(update_fields=["geometry"])
        except (AttributeError, EmptyPartError, ValueError):
            pass

        stops = self.do_stops(feed)

        calendars = get_calendars(feed, source=self.source)

        trips = {}

        # line as in line in a spreadsheet, not as in the Elizabeth Line
        for line in feed.trips.itertuples():
            route = self.routes[line.route_id]
            direction_id = getattr(line, "direction_id", None)
            if direction_id is None or (
                hasattr(direction_id, "isna") and direction_id.isna()
            ):
                direction_id = 0
            trips[line.trip_id] = Trip(
                route=route,
                calendar=calendars[line.service_id],
                inbound=direction_id == 1,
                headsign=getattr(line, "trip_headsign", None),
                ticket_machine_code=line.trip_id,
                block=getattr(line, "block_id", ""),
                vehicle_journey_code=getattr(line, "trip_short_name", ""),
                operator=self.route_operators[line.route_id],
            )

        # use stop_times.txt to calculate trips' start times, end times and destinations:

        trip = None
        previous_line = None

        for line in feed.stop_times.itertuples():
            if not previous_line or previous_line.trip_id != line.trip_id:
                if trip:
                    # Use original stop_id (key in stops dict)
                    dest = stops.get(previous_line.stop_id)
                    if dest:
                        trip.destination = dest
                    trip.end = previous_line.arrival_time

                trip = trips[line.trip_id]
                trip.start = line.departure_time

            previous_line = line

        if previous_line:
            # last trip:
            dest = stops.get(line.stop_id)
            if dest:
                trip.destination = dest
            trip.end = line.arrival_time

        for trip_id in trips:
            trip = trips[trip_id]
            if trip.start is None:
                logger.warning(f"trip {trip_id} has no stop times")
                trips[trip_id] = None

        Trip.objects.bulk_create(
            [trip for trip in trips.values() if isinstance(trip, Trip)],
            batch_size=1000,
        )

        with (
            connection.cursor() as cursor,
            cursor.copy(
                "COPY bustimes_stoptime (stop_id, arrival, departure, sequence, trip_id, timing_status, pick_up, set_down, stop_code) FROM STDIN"
            ) as copy,
        ):
            for line in feed.stop_times.itertuples():
                timing_status = "PTP" if getattr(line, "timepoint", 1) == 1 else "OTH"

                pick_up = True  # Default: regularly scheduled pickup
                if getattr(line, "pickup_type", 0) == 1:  # "No pickup available"
                    pick_up = False

                set_down = True  # Default: regularly scheduled drop off
                if getattr(line, "drop_off_type", 0) == 1:  # "No drop off available"
                    set_down = False

                departure = int(parse_duration(line.departure_time).total_seconds())
                arrival = None
                if line.arrival_time != departure:
                    arrival = int(parse_duration(line.arrival_time).total_seconds())

                # Use prefixed stop_id for all regions
                if self.region_id:
                    stop_id = f"{self.region_id}-{line.stop_id}"
                else:
                    stop_id = line.stop_id
                copy.write_row(
                    (
                        stop_id,
                        arrival,
                        departure,
                        line.stop_sequence,
                        trips[line.trip_id].pk,
                        timing_status,
                        pick_up,
                        set_down,
                        "",
                    )
                )

        del trips

        services = Service.objects.filter(id__in=self.services.keys())

        for service in services:
            service.do_stop_usages()

            region = (
                Region.objects.filter(adminarea__stoppoint__service=service)
                .annotate(Count("adminarea__stoppoint__service"))
                .order_by("-adminarea__stoppoint__service__count")
                .first()
            )
            if region and region != service.region:
                service.save(update_fields=["region"])

            service.update_search_vector()

        services.update(modified_at=Now())

        self.source.save(update_fields=["datetime"])

        for operator in self.operators.values():
            operator.region = (
                Region.objects.filter(adminarea__stoppoint__service__operator=operator)
                .annotate(Count("adminarea__stoppoint__service__operator"))
                .order_by("-adminarea__stoppoint__service__operator__count")
                .first()
            )
            if operator.region_id:
                operator.save(update_fields=["region"])

        old_routes = self.source.route_set.exclude(
            id__in=(route.id for route in self.routes.values())
        )
        logger.info(old_routes.update(service=None))

        current_services = self.source.service_set.filter(current=True)
        logger.info(
            current_services.exclude(route__in=self.routes.values()).update(
                current=False
            )
        )
        old_routes.update(service=None)

        # feed_stops needs to be mapping of stop_id -> object with stop_lon, stop_lat
        # Use the original feed data (not the StopPoint objects)
        feed_stops = {row.stop_id: row for row in feed.stops.itertuples()}

        # Create stop_codes mapping (original stop_id -> atco_code with prefix)
        stop_codes = None
        if self.region_id:
            # Map original stop_id to atco_code (with region prefix) for RouteLink creation
            stop_codes = {
                stop_id: f"{self.region_id}-{stop_id}" for stop_id in stops.keys()
            }

        do_route_links(feed, self.source, self.routes, feed_stops, stop_codes)

    def handle(self, *args, **options):
        if options["collections"]:
            # Filter by name when specific collections are requested
            collections = DataSource.objects.filter(name__in=options["collections"])
        else:
            # Get all GTFS sources (not just Ireland)
            collections = DataSource.objects.filter(
                Q(
                    url__startswith="https://www.transportforireland.ie/transitData/Data/GTFS_"
                )
                | Q(url__endswith=".zip")
                | Q(url__icontains="gtfs")
            ).distinct()

        for source in collections:
            path: Path = settings.DATA_DIR / Path(source.url).name

            modified, last_modified = download_if_modified(path, source)
            if modified or last_modified != source.datetime or options["collections"]:
                logger.info(f"{source} {last_modified}")
                if last_modified:
                    source.datetime = last_modified
                self.source = source
                try:
                    with log_time_taken(logger):
                        # Determine region from source or data
                        region_id = getattr(source, "region_id", None)
                        self.handle_zipfile(path, region_id)
                except (OSError, BadZipFile) as e:
                    logger.exception(e)

            # sleep(2)
