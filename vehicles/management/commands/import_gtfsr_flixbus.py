import functools
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.utils.dateparse import parse_duration
from google.transit import gtfs_realtime_pb2

from busstops.models import DataSource
from bustimes.models import Trip

from ...models import Livery, VehicleJourney
from .. import import_live_vehicles
from .import_gtfsr_ie import Command as GTFSRCommand


class Command(GTFSRCommand):
    source_name = "FlixBus"

    def do_source(self):
        self.tzinfo = ZoneInfo("Europe/London")
        self.source, _ = DataSource.objects.get_or_create(name=self.source_name)
        self.url = "https://rt.flix.baguette.pirnet.si/rt.pb"
        self.livery = Livery.objects.filter(name="FlixBus").first()
        return self

    def get_items(self):
        response = self.session.get(self.url, timeout=10)
        self.session.headers.update({"if-none-match": response.headers["etag"]})
        response.raise_for_status()

        feed = gtfs_realtime_pb2.FeedMessage()
        feed.ParseFromString(response.content)

        for item in feed.entity:
            if item.HasField("vehicle") and item.vehicle.trip.trip_id.startswith("UK"):
                yield item

    @staticmethod
    def get_vehicle_identity(item):
        # not the vehicle id! we're not sure if that maps to an actual vehicle,
        # so we track journeys with no Vehicle records
        return f"{item.vehicle.trip.trip_id} {item.vehicle.trip.start_date}"

    @functools.lru_cache(maxsize=256)
    def get_journey(self, trip_id, start_date, start_time):
        date = datetime.strptime(start_date, "%Y%m%d").date()

        journey = (
            VehicleJourney.objects.filter(
                source=self.source, vehicle=None, code=trip_id, date=date
            )
            .select_related("service")
            .first()
        )
        if journey:
            return journey

        journey = VehicleJourney(
            source=self.source,
            vehicle=None,
            code=trip_id,
            date=date,
            route_name=trip_id.split("-", 1)[0].removeprefix("UK"),
        )

        # (the noon minus 12 hours trick copes with daylight saving time)
        noon = datetime.strptime(f"{start_date} 12:00:00", "%Y%m%d %H:%M:%S").replace(
            tzinfo=self.tzinfo
        )

        try:
            trip = Trip.objects.get(operator="FLIX", vehicle_journey_code=trip_id)
        except Trip.DoesNotExist:
            journey.datetime = noon - timedelta(hours=12) + parse_duration(start_time)
        else:
            journey.trip = trip
            journey.datetime = noon - timedelta(hours=12) + trip.start
            journey.service = trip.route.service
            journey.destination = str(trip.destination.locality or trip.destination)

        if journey.datetime - self.source.datetime > timedelta(hours=12):
            # `start_date` is today but the trip's operational day is yesterday
            journey.datetime -= timedelta(days=1)
            journey.date -= timedelta(days=1)

        if journey.service and not journey.service.tracking:
            journey.service.tracking = True
            journey.service.save(update_fields=["tracking"])

        journey.save()

        return journey

    def handle_items(self, items, identities):
        for item, identity in zip(items, identities):
            self.handle_item(item, self.source.datetime)
            self.identifiers[identity] = self.get_item_identity(item)

    def handle_item(self, item, now):
        journey = self.get_journey(
            item.vehicle.trip.trip_id,
            item.vehicle.trip.start_date,
            item.vehicle.trip.start_time,
        )

        updated_at = self.get_datetime(item)

        redis_client = import_live_vehicles.redis_client

        latest = redis_client.get(f"vehicle{journey.id}")
        if latest:
            latest = json.loads(latest)
            if datetime.fromisoformat(latest["datetime"]) >= updated_at:
                return

        location = self.create_vehicle_location(item)
        location.datetime = updated_at
        location.journey = journey
        location.id = journey.id  # (in lieu of a vehicle id)

        redis_json = location.get_redis_json(tz=self.tzinfo)
        redis_json["vehicle"] = {"name": item.vehicle.vehicle.license_plate}
        if self.livery:
            redis_json["vehicle"]["livery"] = self.livery.id
        if journey.service_id and "service" in redis_json:
            redis_json["service"]["url"] = journey.service.get_absolute_url()

        pipeline = redis_client.pipeline(transaction=False)
        pipeline.rpush(*location.get_appendage())
        pipeline.geoadd(
            "vehicle_location_locations",
            [location.latlong.x, location.latlong.y, journey.id],
        )
        if journey.service_id:
            pipeline.sadd(f"service{journey.service_id}vehicles", journey.id)
        pipeline.sadd("operatorFLIXvehicles", journey.id)
        pipeline.set(f"vehicle{journey.id}", json.dumps(redis_json), ex=900)
        pipeline.execute()
