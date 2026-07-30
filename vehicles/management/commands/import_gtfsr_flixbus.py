from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from google.protobuf import json_format
from google.transit import gtfs_realtime_pb2

from busstops.models import DataSource
from bustimes.models import Trip

from ...models import Vehicle, VehicleJourney, Livery
from .import_gtfsr_ie import Command as GTFSRCommand


class Command(GTFSRCommand):
    source_name = "FlixBus"
    vehicle_code_scheme = "FlixBus"

    def do_source(self):
        self.tzinfo = ZoneInfo("Europe/London")
        self.source, _ = DataSource.objects.get_or_create(name=self.source_name)
        self.url = "https://rt.flix.baguette.pirnet.si/rt.pb"
        self.livery = Livery.objects.filter(name="FlixBus").first()
        return self

    def get_items(self):
        response = self.session.get(self.url, timeout=10)
        self.session.headers.update({"if-none-match": response.headers["etag"]})

        print(response.headers)
        response.raise_for_status()

        feed = gtfs_realtime_pb2.FeedMessage()
        feed.ParseFromString(response.content)

        print(feed.header.timestamp)
        print(datetime.fromtimestamp(feed.header.timestamp, timezone.utc))

        for item in feed.entity:
            if item.HasField("vehicle") and item.vehicle.trip.trip_id.startswith("UK"):
                yield item

    def get_vehicle(self, item):
        vehicle_code = item.vehicle.vehicle.id
        defaults = {"code": vehicle_code, "livery": self.livery}
        if item.vehicle.vehicle.license_plate:
            defaults["reg"] = item.vehicle.vehicle.license_plate

        return Vehicle.objects.get_or_create(
            operator_id="FLIX", code=vehicle_code, defaults=defaults
        )

    def get_journey(self, item, vehicle):
        journey = VehicleJourney(code=item.vehicle.trip.trip_id)

        start_date = datetime.strptime(
            f"{item.vehicle.trip.start_date} 12:00:00",
            "%Y%m%d %H:%M:%S",
        )
        journey.date = start_date.date()

        journey.route_name = journey.code.split("-", 1)[0].removeprefix("UK")

        try:
            trip = Trip.objects.get(operator="FLIX", vehicle_journey_code=journey.code)
        except Trip.DoesNotExist:
            pass
        else:
            journey.trip = trip

            journey.datetime = (
                start_date.replace(tzinfo=self.tzinfo)
                - timedelta(hours=12)
                + trip.start
            )
            now = self.get_datetime(item)
            if journey.datetime - now > timedelta(hours=12):
                # `start_date` is today but the trip's operational day is yesterday
                journey.datetime -= timedelta(days=1)
                journey.date -= timedelta(days=1)

            journey.service = trip.route.service
            journey.destination = str(trip.destination.locality or trip.destination)

        vehicle.latest_journey_data = json_format.MessageToDict(item)

        return journey
