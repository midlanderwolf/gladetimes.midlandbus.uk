"""Generic GTFS-RT vehicle import command

Usage:
    # Import from a specific GTFS-RT feed
    python manage.py import_gtfsr --url https://api.example.com/gtfsr --source-name "My GTFS-RT Source"

    # Import with API key
    python manage.py import_gtfsr --url https://api.example.com/gtfsr --source-name "My Source" --header "x-api-key:mykey"

    # Import with timezone (for parsing timestamps)
    python manage.py import_gtfsr --url https://api.example.com/gtfsr --source-name "My Source" --tz "Europe/London"
"""

import logging
from datetime import datetime, timedelta

from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib.gis.geos import GEOSGeometry
from django.db.models import Q
from google.protobuf import json_format
from google.transit import gtfs_realtime_pb2

from busstops.models import DataSource, Service
from bustimes.models import Trip
from bustimes.utils import get_calendars

from ...models import Vehicle, VehicleJourney, VehicleLocation
from ..import_live_vehicles import ImportLiveVehiclesCommand

logger = logging.getLogger(__name__)

# Occupancy status mapping (GTFS-RT spec)
OCCUPANCIES = {
    0: "Empty",
    1: "Many seats available",
    2: "Few seats available",
    3: "Standing room only",
    4: "Crushed standing room only",
    5: "Full",
    6: "Not accepting passengers",
    7: "No data available",
    8: "Not boardable",
}


class Command(ImportLiveVehiclesCommand):
    """Generic GTFS-RT vehicle importer

    Attributes:
        url: The GTFS-RT feed URL
        source_name: Name for the DataSource
        tzinfo: Timezone for interpreting dates
        headers: Optional HTTP headers for the request
        vehicle_code_scheme: Scheme for vehicle codes (default: GTFS-RT)
    """

    url = ""
    source_name = ""
    tzinfo = ZoneInfo("UTC")
    headers = {}
    vehicle_code_scheme = "GTFS-RT"
    OCCUPANCIES = OCCUPANCIES

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument(
            "--url",
            type=str,
            required=True,
            help="GTFS-RT feed URL",
        )
        parser.add_argument(
            "--source-name",
            type=str,
            required=True,
            help="Name for the DataSource",
        )
        parser.add_argument(
            "--tz",
            type=str,
            default="UTC",
            help="Timezone for interpreting timestamps (default: UTC)",
        )
        parser.add_argument(
            "--header",
            action="append",
            help="HTTP header in format 'Header-Name:value' (can be used multiple times)",
        )

    def do_source(self):
        self.url = self.options.get("url")
        self.source_name = self.options.get("source_name")

        if not self.url or not self.source_name:
            raise ValueError("url and source_name are required")

        # Set timezone
        tz_str = self.options.get("tz", "UTC")
        self.tzinfo = ZoneInfo(tz_str)

        # Parse headers
        self.headers = {}
        if hasattr(settings, "GTFSR_API_KEY") and settings.GTFSR_API_KEY:
            self.headers["x-api-key"] = settings.GTFSR_API_KEY

        header_args = self.options.get("header")
        if header_args is None:
            header_args = []
        for header in header_args:
            if ":" in header:
                key, value = header.split(":", 1)
                self.headers[key.strip()] = value.strip()

        # Get or create data source
        self.source, _ = DataSource.objects.get_or_create(name=self.source_name)

        logger.info(f"Using source: {self.source_name}, timezone: {tz_str}")
        return self

    def get_datetime(self, item):
        """Extract datetime from GTFS-RT item"""
        if hasattr(item.vehicle, "timestamp") and item.vehicle.timestamp:
            return datetime.fromtimestamp(item.vehicle.timestamp, tz=self.tzinfo)
        return None

    @staticmethod
    def get_vehicle_identity(item):
        """Get vehicle identifier from GTFS-RT item"""
        return item.vehicle.vehicle.id

    @staticmethod
    def get_journey_identity(item):
        """Get journey identifier from GTFS-RT item"""
        trip = item.vehicle.trip
        # Use trip_id if available, otherwise use route_id + start_time + direction
        if trip.trip_id:
            return (trip.route_id, trip.trip_id, trip.start_date)
        return (trip.route_id, trip.start_time, trip.direction_id, trip.start_date)

    @staticmethod
    def get_item_identity(item):
        """Get unique identifier for deduplication"""
        return item.vehicle.timestamp

    def get_items(self):
        """Fetch and parse GTFS-RT feed"""
        response = self.session.get(self.url, headers=self.headers, timeout=10)
        response.raise_for_status()

        feed = gtfs_realtime_pb2.FeedMessage()
        feed.ParseFromString(response.content)

        return feed.entity

    def get_vehicle(self, item):
        """Get or create vehicle from GTFS-RT item"""
        vehicle_code = self.get_vehicle_identity(item)
        vehicle, created = Vehicle.objects.get_or_create(
            code=vehicle_code,
            source=self.source,
        )
        return vehicle, created

    def get_journey(self, item, vehicle):
        """Extract or create VehicleJourney from GTFS-RT item"""
        trip_data = item.vehicle.trip
        journey = VehicleJourney(code=trip_data.trip_id)

        # Check if this is the same as the vehicle's latest journey
        if (
            trip_data.trip_id
            and vehicle.latest_journey
            and vehicle.latest_journey.code == journey.code
        ):
            return vehicle.latest_journey

        # Parse start datetime from GTFS-RT data
        if hasattr(trip_data, "start_date") and trip_data.start_date:
            try:
                if hasattr(trip_data, "start_time") and trip_data.start_time:
                    # start_time is in HH:MM:SS format (local time)
                    journey.datetime = datetime.strptime(
                        f"{trip_data.start_date} {trip_data.start_time}",
                        "%Y%m%d %H:%M:%S",
                    ).replace(tzinfo=self.tzinfo)
                    logger.info(
                        f"Parsed journey datetime: {journey.datetime} (local: {journey.datetime.astimezone(self.tzinfo)})"
                    )
                else:
                    journey.datetime = datetime.strptime(
                        f"{trip_data.start_date} 00:00:00", "%Y%m%d %H:%M:%S"
                    ).replace(tzinfo=self.tzinfo)
                    logger.info(
                        f"Parsed journey datetime (no start_time): {journey.datetime}"
                    )
            except (ValueError, TypeError) as e:
                logger.warning(f"Failed to parse journey datetime: {e}")

        # Find service
        service = None
        services = Service.objects.filter(
            current=True,
            route__source=self.source,
            route__code=trip_data.route_id,
        ).distinct()

        if not services and "_" not in trip_data.route_id:
            # Try with prefix
            services = Service.objects.filter(
                current=True,
                route__source=self.source,
                route__code__endswith=f"_{trip_data.route_id}",
            ).distinct()

        if services:
            service = services[0]
            journey.service = service
            journey.route_name = service.line_name
            logger.info(f"Found service: {service} (line {service.line_name})")
        else:
            logger.warning(
                f"No service found for route_id={trip_data.route_id}, source={self.source}"
            )

        # Find trip - use ticket_machine_code if trip_id available, otherwise match by route+time+direction
        trips = Trip.objects.none()

        trip_id = (
            trip_data.trip_id
            if hasattr(trip_data, "trip_id") and trip_data.trip_id
            else None
        )

        if trip_id:
            logger.info(f"Looking for trip with trip_id={trip_id}")
            # Try exact match on ticket_machine_code
            trips = Trip.objects.filter(
                route__source=self.source,
                ticket_machine_code=trip_id,
            )
            if trips.count() == 0:
                # Try vehicle_journey_code as fallback
                trips = Trip.objects.filter(
                    route__source=self.source,
                    vehicle_journey_code=trip_id,
                )
            logger.info(f"Found {trips.count()} trips matching trip_id={trip_id}")

            # If still no trips, log available trip_ids for debugging
            if trips.count() == 0:
                sample_trips = Trip.objects.filter(
                    route__source=self.source
                ).values_list("ticket_machine_code", flat=True)[:5]
                logger.warning(
                    f"No trip found for trip_id={trip_id}. Sample trip_ids in DB: {list(sample_trips)}"
                )

        # If no trip found yet, try matching by start_time and service/route
        if not trips.exists() and journey.datetime:
            # Convert datetime to local time seconds since midnight
            local_datetime = journey.datetime.astimezone(self.tzinfo)
            start_time_seconds = (
                local_datetime.hour * 3600
                + local_datetime.minute * 60
                + local_datetime.second
            )
            logger.info(
                f"Matching by start_time={start_time_seconds} (local time: {local_datetime.time()})"
            )

            # Build query for start_time matching
            time_filter = Q(
                start__gte=start_time_seconds - 60, start__lte=start_time_seconds + 60
            )

            if service:
                trips = (
                    Trip.objects.filter(
                        route__service=service,
                    )
                    .filter(time_filter)
                    .distinct()
                )
            else:
                # Try to match by route_id
                route_filter = Q(route__source=self.source)
                if trip_data.route_id:
                    route_filter |= Q(route__code=trip_data.route_id)
                    if "_" not in trip_data.route_id:
                        route_filter |= Q(
                            route__code__endswith=f"_{trip_data.route_id}"
                        )
                trips = Trip.objects.filter(route_filter).filter(time_filter).distinct()

            if (
                hasattr(trip_data, "direction_id")
                and trip_data.direction_id is not None
            ):
                trips = trips.filter(inbound=(trip_data.direction_id == 1))

            # If we have a journey datetime, also filter by calendar
            if journey.datetime and trips.count() > 1:
                calendar_ids = [trip.calendar_id for trip in trips]
                calendars = get_calendars(journey.datetime.date(), calendar_ids)
                trips = trips.filter(calendar__in=calendars)

            logger.debug(f"Found {trips.count()} trips matching start_time")

        if trips:
            if len(trips) > 1:
                # Try to narrow down by calendar
                if journey.datetime:
                    calendar_ids = [trip.calendar_id for trip in trips]
                    calendars = get_calendars(journey.datetime.date(), calendar_ids)
                    trips = trips.filter(calendar__in=calendars)
                trip = trips.first()
            else:
                trip = trips[0]

            if trip:
                journey.trip = trip
                journey.code = trip.ticket_machine_code or journey.code
                if not journey.service:
                    journey.service = trip.route.service
                journey.destination = trip.headsign or ""

                # Update vehicle operator from trip
                if trip.operator_id and not vehicle.operator_id:
                    vehicle.operator_id = trip.operator_id
                    vehicle.save(update_fields=["operator"])

        # Check if this is the same journey (for empty trip_id case)
        if not trip_data.trip_id and vehicle.latest_journey:
            if journey.trip and vehicle.latest_journey.trip_id == journey.trip.id:
                return vehicle.latest_journey
            if (
                journey.service_id
                and vehicle.latest_journey.service_id == journey.service_id
                and journey.datetime
                and vehicle.latest_journey.datetime
                and journey.datetime.date() == vehicle.latest_journey.datetime.date()
            ):
                # Same service and date - likely same journey
                pass

        # Store raw data
        vehicle.latest_journey_data = json_format.MessageToDict(item)

        return journey

    def create_vehicle_location(self, item):
        """Create VehicleLocation from GTFS-RT item"""
        position = item.vehicle.position
        if not position:
            return None

        heading = None
        if hasattr(position, "bearing") and position.bearing:
            heading = position.bearing if position.bearing != -1 else None

        latlong = GEOSGeometry(f"POINT({position.longitude} {position.latitude})")

        occupancy = None
        if hasattr(item.vehicle, "occupancy_status"):
            occupancy = self.OCCUPANCIES.get(item.vehicle.occupancy_status)

        return VehicleLocation(
            heading=heading,
            latlong=latlong,
            occupancy=occupancy,
        )

    def handle(self, *args, **options):
        """Main entry point"""
        self.options = options
        self.do_source()
        super().handle(*args, **options)
