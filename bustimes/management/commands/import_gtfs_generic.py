import logging
import pandas as pd
from pathlib import Path

import gtfs_kit

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Min, Subquery, OuterRef

from busstops.models import DataSource, Operator, OperatorCode, Region, Service, StopPoint, ServiceColour

from ...download_utils import download_if_modified
from ...models import Route, StopTime, Trip
from ...gtfs_utils import get_calendars, MODES, do_route_links

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument("source_name", help="Name of the DataSource (must already exist)")
        parser.add_argument(
            "--url",
            help="Override the DataSource URL (optional, updates the source if provided)",
        )
        parser.add_argument(
            "--filename",
            help="Local filename to save the feed (default: source_name.zip)",
        )
        parser.add_argument(
            "--stop-prefix",
            help="Prefix for stop ATCO codes (default: source_name.lower())",
        )
        parser.add_argument(
            "--region",
            help="Region ID to assign to operators (e.g., 'ATL', 'FI', 'NSW', 'OH', 'PL')",
        )
        parser.add_argument(
            "--local",
            action="store_true",
            help="Skip download and use the existing local file",
        )

    def handle(self, source_name, url=None, filename=None, stop_prefix=None, region=None, local=False, *args, **options):
        if not filename:
            filename = f"{source_name.lower()}_gtfs.zip"
        if not stop_prefix:
            stop_prefix = source_name.lower()

        path = settings.DATA_DIR / Path(filename)

        try:
            source = DataSource.objects.get(name=source_name)
        except DataSource.DoesNotExist:
            raise CommandError(f"DataSource '{source_name}' does not exist. Create it first with a URL.")

        if url:
            source.url = url
            source.save(update_fields=["url"])

        region_obj = None
        if region:
            region_obj, _ = Region.objects.get_or_create(id=region, defaults={"name": region})

        if local:
            if not path.exists():
                raise CommandError(f"Local file not found: {path}")
            from datetime import datetime, timezone
            last_modified = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
            modified = True
        else:
            modified, last_modified = download_if_modified(path, source)

        if not modified:
            return
        source.datetime = last_modified

        logger.info(f"{source} {last_modified}")

        feed = gtfs_kit.read_feed(path, dist_units="km")

        agency_timezone = self.get_agency_timezone(feed)
        logger.info(f"Using timezone: {agency_timezone}")
        logger.info(f"Feed contains: {len(feed.stops)} stops, {len(feed.get_routes())} routes, {len(feed.trips)} trips, {len(feed.stop_times)} stop_times")

        operators = {}
        for agency in feed.agency.itertuples():
            agency_id_str = str(agency.agency_id)
            agency_ids = [a.strip() for a in agency_id_str.split(",")]
            agency_name = agency.agency_name if hasattr(agency, "agency_name") else ""

            # If multiple agency_ids, create separate operators for each
            if len(agency_ids) > 1:
                for agency_id in agency_ids:
                    noc = agency_id[:10]
                    operator, _ = Operator.objects.update_or_create(
                        noc=noc,
                        defaults={"name": agency_name, "timezone": agency_timezone, "region": region_obj} if region_obj else {"name": agency_name, "timezone": agency_timezone}
                    )
                    OperatorCode.objects.get_or_create(
                        operator=operator,
                        source=source,
                        code=agency_id,
                    )
                    operators[agency_id] = operator
            else:
                # Single agency_id - use name-matching to merge if same name exists
                agency_id = agency_ids[0]
                operator = Operator.objects.filter(name__iexact=agency_name).first()
                if not operator:
                    noc = agency_id[:10]
                    defaults = {"name": agency_name, "timezone": agency_timezone}
                    if region_obj:
                        defaults["region"] = region_obj
                    operator, _ = Operator.objects.update_or_create(
                        noc=noc,
                        defaults=defaults
                    )

                if region_obj and operator.region_id != region_obj.id:
                    operator.region = region_obj
                    operator.save(update_fields=["region"])

                OperatorCode.objects.get_or_create(
                    operator=operator,
                    source=source,
                    code=agency_id,
                )
                operators[agency_id] = operator

        existing_routes = {route.code: route for route in source.route_set.all()}
        routes = []
        route_operators = {}

        logger.info("Processing stops...")
        stops = {}
        new_stops = []
        for stop in feed.stops.itertuples():
            stop_tz_raw = getattr(stop, "stop_timezone", None)
            stop_tz = agency_timezone if stop_tz_raw is None or pd.isna(stop_tz_raw) else stop_tz_raw
            new_stops.append(
                StopPoint(
                    atco_code=f"{stop_prefix}-{stop.stop_id}",
                    common_name=stop.stop_name[:48],
                    active=True,
                    source=source,
                    latlong=f"POINT({stop.stop_lon} {stop.stop_lat})",
                    timezone=stop_tz,
                    bearing=self.get_bearing(stop),
                )
            )

        StopPoint.objects.bulk_create(
            new_stops,
            update_conflicts=True,
            unique_fields=["atco_code"],
            update_fields=["common_name", "latlong", "bearing", "timezone"],
        )
        logger.info(f"Created/updated {len(new_stops)} stops")
        for stop in new_stops:
            stops[stop.atco_code.removeprefix(f"{stop_prefix}-")] = stop

        calendars = get_calendars(feed, source)

        colours = {
            (colour.background, colour.foreground): colour
            for colour in ServiceColour.objects.all()
        }

        logger.info("Processing routes...")
        for row in feed.get_routes(as_gdf=True).itertuples():
            service = Service(line_name=row.route_short_name)

            if row.route_id in existing_routes:
                route = existing_routes[row.route_id]
            else:
                route = Route(code=row.route_id)
            route.timezone = agency_timezone
            route.source = source
            route.service = service
            route.line_name = row.route_short_name
            service.source = source
            service.description = route.description = row.route_long_name
            service.current = True

            route_color = getattr(row, "route_color", None)
            route_text_color = getattr(row, "route_text_color", None)
            if route_color is not None and not pd.isna(route_color) and route_text_color is not None and not pd.isna(route_text_color):
                bg, fg = (f"#{route_color}", f"#{route_text_color}")
                if (bg, fg) not in colours:
                    colours[(bg, fg)] = ServiceColour.objects.create(
                        background=bg, foreground=fg
                    )
                service.colour = colours[(bg, fg)]

            service.mode = MODES.get(row.route_type, "bus")
            if row.geometry:
                service.geometry = row.geometry.wkt

            service.save()
            operator = operators.get(row.agency_id)
            if operator:
                service.operator.add(operator)
            route.save()

            routes.append(route)
            route_operators[route.code] = operator

            existing_routes[route.code] = route

        logger.info(f"Processed {len(routes)} routes")

        logger.info("Processing trips...")
        existing_trips = {
            trip.vehicle_journey_code: trip
            for operator in operators.values()
            for trip in operator.trip_set.all()
        }
        trips = {}
        for row in feed.trips.itertuples():
            route = existing_routes[row.route_id]
            headsign = "" if pd.isna(row.trip_headsign) else row.trip_headsign.removeprefix(f"{route.line_name} ")
            trip = Trip(
                route=route,
                calendar=calendars[row.service_id],
                inbound=not pd.isna(row.direction_id) and row.direction_id == 1,
                ticket_machine_code=row.trip_id,
                vehicle_journey_code=row.trip_id,
                operator=route_operators.get(route.code),
                headsign=headsign,
            )
            if trip.vehicle_journey_code in existing_trips:
                trip.id = existing_trips[trip.vehicle_journey_code].id
            trips[trip.vehicle_journey_code] = trip
        del existing_trips

        logger.info(f"Processed {len(trips)} trips")

        logger.info("Processing stop_times...")
        stop_times = []
        for row in feed.stop_times.itertuples():
            trip = trips[row.trip_id]

            arrival_time = row.arrival_time
            departure_time = row.departure_time

            if arrival_time[0] == " ":
                arrival_time = "0" + arrival_time[1:]
            if departure_time[0] == " ":
                departure_time = "0" + departure_time[1:]

            if not trip.start:
                trip.start = departure_time
            trip.end = arrival_time

            stop_time = StopTime(
                arrival=arrival_time,
                departure=departure_time,
                sequence=row.stop_sequence,
                trip=trip,
                timing_status="PTP" if getattr(row, "timepoint", 1) else "OTH",
                pick_up=(row.pickup_type != 1),
                set_down=(row.drop_off_type != 1),
            )

            stop_time.stop = trip.destination = stops[row.stop_id]

            stop_times.append(stop_time)

        logger.info(f"Processed {len(stop_times)} stop_times")

        feed_stops = {row.stop_id: row for row in feed.stops.itertuples()}
        stop_codes = {stop_id: stop.atco_code for stop_id, stop in stops.items()}
        do_route_links(feed, source, existing_routes, feed_stops, stop_codes)

        logger.info("Saving to database...")
        with transaction.atomic():
            existing_trips = [trip for trip in trips.values() if trip.id]
            Trip.objects.bulk_create([trip for trip in trips.values() if not trip.id])
            Trip.objects.bulk_update(
                existing_trips,
                fields=[
                    "route",
                    "calendar",
                    "start",
                    "end",
                    "destination",
                    "block",
                    "vehicle_journey_code",
                    "ticket_machine_code",
                    "inbound",
                    "headsign",
                ],
            )

            StopTime.objects.filter(trip__in=existing_trips).delete()
            StopTime.objects.bulk_create(stop_times)

            for service in source.service_set.filter(current=True):
                service.do_stop_usages()
                service.update_search_vector()

            logger.info(
                source.route_set.exclude(id__in=[route.id for route in routes]).delete()
            )
            for operator in operators.values():
                logger.info(
                    operator.trip_set.exclude(
                        id__in=[trip.id for trip in trips.values()]
                    ).delete()
                )
                logger.info(
                    operator.service_set.filter(current=True, route__isnull=True).update(
                        current=False
                    )
                )

            source.route_set.update(
                start_date=Subquery(
                    Route.objects.filter(pk=OuterRef("pk"))
                    .annotate(min_date=Min("trip__calendar__start_date"))
                    .values("min_date")[:1]
                )
            )

            source.save(update_fields=["datetime"])

        logger.info("Import complete")

    def get_agency_timezone(self, feed):
        if not hasattr(feed, "agency") or feed.agency.empty:
            raise CommandError("Feed has no agency.txt file")
        if "agency_timezone" not in feed.agency.columns:
            raise CommandError("agency.txt missing agency_timezone column")
        return feed.agency.iloc[0].agency_timezone

    def get_bearing(self, stop):
        if not stop.stop_name:
            return ""
        prefix = stop.stop_name[:2].upper()
        if prefix in ("NB", "EB", "SB", "WB"):
            return stop.stop_name[0]
        return ""
