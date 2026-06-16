from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from google.protobuf import json_format
from google.transit import gtfs_realtime_pb2


from busstops.models import DataSource
from bustimes.models import Trip

from ...models import Vehicle, VehicleJourney, Operator, Service
from .import_gtfsr_ie import Command as GTFSRCommand


class Command(GTFSRCommand):
    source_name = "RTCSNV"
    vehicle_code_scheme = "RTCSNV"
    headers = None

    def do_source(self):
        self.tzinfo = ZoneInfo("America/Los_Angeles")
        self.source, _ = DataSource.objects.get_or_create(name=self.source_name)
        self.url = "https://developer.rtcsnv.com/transitData/vehiclePositions.pb"
        self.operator = Operator.objects.get(noc="RTCSNV")
        return self

    def get_items(self):
        headers = {}
        if self.headers:
            headers["if-modified-since"] = self.headers.get("last-modified")
            headers["if-none-match"] = self.headers.get("etag")

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
                        # `start_date` is today but the trip's operational day is yesterday
                        journey.datetime -= timedelta(days=1)
                        journey.date -= timedelta(days=1)

                journey.service = trip.route.service

                journey.route_name = journey.service.line_name
                journey.destination = trip.headsign or ""

        if not journey.trip and item.vehicle.trip.route_id:
            try:
                journey.service = Service.objects.get(
                    route__code=item.vehicle.trip.route_id, source=self.source
                )
            except Service.DoesNotExist:
                pass
            else:
                journey.route_name = journey.service.line_name

        vehicle.latest_journey_data = json_format.MessageToDict(item)

        return journey
