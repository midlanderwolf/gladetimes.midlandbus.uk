from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from google.protobuf import json_format
from google.transit import gtfs_realtime_pb2

from busstops.models import DataSource
from bustimes.models import Trip

from ...models import Vehicle, VehicleJourney, Operator, Service
from .import_gtfsr_ie import Command as GTFSRCommand


class Command(GTFSRCommand):
    headers = None
    _tzinfo = None

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument("--source-name", required=True, help="DataSource name")
        parser.add_argument("--url", required=True, help="GTFS-RT feed URL")
        parser.add_argument("--noc", required=True, help="Operator NOC code")
        parser.add_argument(
            "--vehicle-code-scheme",
            help="Vehicle code scheme (default: source_name)",
        )

    def do_source(self):
        self.source_name = self.options["source_name"]
        self.url = self.options["url"]
        noc = self.options["noc"]

        if self.options.get("vehicle_code_scheme"):
            self.vehicle_code_scheme = self.options["vehicle_code_scheme"]
        else:
            self.vehicle_code_scheme = self.source_name

        self.source, _ = DataSource.objects.get_or_create(name=self.source_name)
        self.operator = Operator.objects.get(noc=noc)

        self._tzinfo = self.get_timezone()
        if not self._tzinfo:
            raise ValueError(
                f"No timezone found for operator {noc}. "
                "Ensure the operator has a timezone set from the GTFS import."
            )

        return self

    def handle(self, *args, **options):
        self.options = options
        super().handle(*args, **options)

    @property
    def tzinfo(self):
        if self._tzinfo is None:
            self._tzinfo = self.get_timezone()
        return self._tzinfo

    @tzinfo.setter
    def tzinfo(self, value):
        self._tzinfo = value

    def get_timezone(self):
        if hasattr(self, "operator") and self.operator and self.operator.timezone:
            return ZoneInfo(str(self.operator.timezone))
        return None

    def get_items(self):
        headers = {}
        if self.headers:
            headers["if-modified-since"] = self.headers["last-modified"]
            headers["if-none-match"] = self.headers["etag"]

        response = self.session.get(self.url, timeout=10)
        response.raise_for_status()

        self.headers = response.headers

        feed = gtfs_realtime_pb2.FeedMessage()
        feed.ParseFromString(response.content)

        return feed.entity

    def get_vehicle(self, item):
        return Vehicle.objects.get_or_create(
            {"fleet_code": item.vehicle.vehicle.id.strip()},
            operator=self.operator,
            code=item.vehicle.vehicle.id.strip(),
        )

    def get_journey(self, item, vehicle):
        now = self.get_datetime(item).astimezone(self.tzinfo)
        journey = VehicleJourney(
            code=item.vehicle.trip.trip_id, datetime=now, date=now.date()
        )

        start_date = None
        if item.vehicle.trip.start_date:
            start_date = datetime.strptime(
                f"{item.vehicle.trip.start_date} 12:00:00",
                "%Y%m%d %H:%M:%S",
            )

            try:
                journey.datetime = datetime.strptime(
                    f"{item.vehicle.trip.start_date} {item.vehicle.trip.start_time}",
                    "%Y%m%d %H:%M:%S",
                ).replace(tzinfo=self.tzinfo)
                journey.date = journey.datetime.date()
            except ValueError:
                pass

        journey.route_name = item.vehicle.trip.route_id

        if journey.code:
            try:
                trip = Trip.objects.get(
                    operator=self.operator, vehicle_journey_code=journey.code
                )
            except Trip.DoesNotExist:
                pass
            else:
                journey.trip = trip

                if start_date:
                    journey.datetime = (
                        start_date.replace(tzinfo=self.tzinfo)
                        - timedelta(hours=12)
                        + trip.start
                    )
                    if journey.datetime - now > timedelta(hours=12):
                        journey.datetime -= timedelta(days=1)
                        journey.date -= timedelta(days=1)

                journey.service = trip.route.service

                journey.route_name = journey.service.line_name
                journey.destination = trip.headsign or ""

        if not journey.trip and item.vehicle.trip.route_id:
            trips = Trip.objects.filter(
                route__code=item.vehicle.trip.route_id, source=self.source
            )
            if item.vehicle.trip.start_time:
                from django.utils.dateparse import parse_duration
                start_time = parse_duration(item.vehicle.trip.start_time)
                if start_time:
                    trips = trips.filter(start=start_time)
            trip = trips.first()
            if trip:
                journey.trip = trip
                journey.service = trip.route.service
                journey.route_name = journey.service.line_name
                journey.destination = trip.headsign or ""

        vehicle.latest_journey_data = json_format.MessageToDict(item)

        return journey
