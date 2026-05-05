"""
Ireland GTFS-RT vehicle import command

Inherits from the generic import_gtfsr command with Ireland-specific settings.
"""

import logging
from datetime import datetime, timedelta

from zoneinfo import ZoneInfo

from django.conf import settings
from django.utils.dateparse import parse_duration
from google.protobuf import json_format
from google.transit import gtfs_realtime_pb2

from busstops.models import DataSource, Service
from bustimes.models import Trip
from bustimes.utils import get_calendars

from .import_gtfsr import Command as BaseCommand
from ...models import VehicleLocation

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """Ireland-specific GTFS-RT importer"""

    source_name = "Realtime Transport Operators"
    vehicle_code_scheme = "NTA"
    tzinfo = ZoneInfo("Europe/Dublin")
    url = "https://api.nationaltransport.ie/gtfsr/v2/Vehicles"

    def do_source(self):
        self.source, _ = DataSource.objects.get_or_create(name=self.source_name)
        self.headers = {}
        if settings.NTA_API_KEY:
            self.headers["x-api-key"] = settings.NTA_API_KEY
        return self

    def get_items(self):
        """Fetch Ireland GTFS-RT feed"""
        response = self.session.get(self.url, headers=self.headers, timeout=10)
        response.raise_for_status()

        feed = gtfs_realtime_pb2.FeedMessage()
        feed.ParseFromString(response.content)

        return feed.entity

    @staticmethod
    def get_datetime(item):
        return datetime.fromtimestamp(item.vehicle.timestamp, tz=BaseCommand.tzinfo)

    @staticmethod
    def get_vehicle_identity(item):
        return item.vehicle.vehicle.id

    def get_journey(self, item, vehicle):
        """Ireland-specific journey handling"""
        # GTFS spec for working out datetimes:
        start_date = datetime.strptime(
            f"{item.vehicle.trip.start_date} 12:00:00",
            "%Y%m%d %H:%M:%S",
        )
        start_time = parse_duration(item.vehicle.trip.start_time)
        start_date_time = (start_date + start_time - timedelta(hours=12)).replace(
            tzinfo=self.tzinfo
        )

        journey = VehicleJourney(code=item.vehicle.trip.trip_id)

        if (
            latest_journey := vehicle.latest_journey
        ) and latest_journey.code == journey.code:
            return latest_journey

        journey.datetime = start_date_time

        # Find service
        service = None
        services = Service.objects.filter(
            current=True,
            route__source=self.source,
            route__code=item.vehicle.trip.route_id,
        ).distinct()
        if not services and "_" not in item.vehicle.trip.route_id:
            services = Service.objects.filter(
                current=True,
                route__source=self.source,
                route__code__endswith=f"_{item.vehicle.trip.route_id}",
            ).distinct()

        if services:
            service = services[0]
            journey.service = service
            journey.route_name = service.line_name

        # Find trip
        trips = Trip.objects.filter(ticket_machine_code=journey.code)
        if service:
            trips = trips.filter(route__service=service)

        trip = None

        if service and not trips:
            trips = Trip.objects.filter(
                route__service=service,
                route__source=self.source,
                start=start_time,
                inbound=item.vehicle.trip.direction_id == 1,
            )

        if trips:
            if len(trips) > 1:
                calendar_ids = [trip.calendar_id for trip in trips]
                calendars = get_calendars(start_date, calendar_ids)
                trips = trips.filter(calendar__in=calendars)
                trip = trips.first()
            else:
                trip = trips[0]

        if trip:
            if not journey.service:
                journey.service = trip.route.service
            journey.trip = trip
            journey.destination = trip.headsign or ""

            # Update vehicle operator from trip
            if trip.operator_id and not vehicle.operator_id:
                vehicle.operator_id = trip.operator_id
                vehicle.save(update_fields=["operator"])

        # Store raw data
        vehicle.latest_journey_data = json_format.MessageToDict(item)

        return journey

    def create_vehicle_location(self, item):
        return VehicleLocation(
            heading=item.vehicle.position.bearing or None,
            latlong=self.create_point(
                item.vehicle.position.longitude, item.vehicle.position.latitude
            ),
            occupancy=self.OCCUPANCIES.get(item.vehicle.occupancy_status or None),
        )
