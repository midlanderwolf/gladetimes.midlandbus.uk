import json
import logging
from datetime import timedelta

from django.contrib.gis.db.models.functions import Distance
from django.contrib.gis.geos import Point
from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from busstops.models import StopPoint
from bustimes.models import Route, Trip

from ...models import VehicleJourney, VehicleLocation
from ...utils import redis_client

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Guess trips for vehicle journeys that don't have a trip assigned"

    def add_arguments(self, parser):
        parser.add_argument(
            "--source",
            type=str,
            required=True,
            help="Source name to process journeys from (required)",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=1000,
            help="Maximum number of journeys to process (default: 1000)",
        )

    def handle(self, *args, **options):
        source_name = options.get("source")
        limit = options.get("limit")

        journeys = VehicleJourney.objects.filter(
            trip__isnull=True,
            service__isnull=False,
            source__name=source_name,
        )

        journeys = journeys.order_by("-datetime")[:limit]

        total = 0
        matched = 0

        for journey in journeys:
            total += 1
            if self.guess_trip_for_journey(journey):
                matched += 1

        self.stdout.write(
            f"Processed {total} journeys, matched {matched} trips"
        )

    def guess_trip_for_journey(self, journey):
        if not journey.service:
            self.stdout.write(f"Journey {journey.id}: No service")
            return False

        location_data = self.get_journey_locations(journey)
        if not location_data:
            self.stdout.write(f"Journey {journey.id}: No location data")
            return False

        destination_ref = self.get_destination_ref(journey.destination)

        # Strategy 1: Try with nearby stops (most specific)
        nearby_stops = self.get_nearby_stops_from_locations(location_data, limit=5)
        if nearby_stops:
            for stop in nearby_stops:
                trip = journey.get_trip(
                    datetime=journey.datetime,
                    approximate_datetime=True,
                    next_stop=stop.naptan_code,
                    destination_ref=destination_ref,
                )

                if trip:
                    journey.trip = trip
                    journey.save(update_fields=["trip"])
                    self.stdout.write(
                        f"Journey {journey.id}: Matched trip {trip.id} "
                        f"(stop={stop.naptan_code})"
                    )
                    return True

        # Strategy 2: Try without next_stop (less specific, just time + direction + destination)
        trip = journey.get_trip(
            datetime=journey.datetime,
            approximate_datetime=True,
            destination_ref=destination_ref,
        )

        if trip:
            journey.trip = trip
            journey.save(update_fields=["trip"])
            self.stdout.write(
                f"Journey {journey.id}: Matched trip {trip.id} (no stop constraint)"
            )
            return True

        self.stdout.write(
            f"Journey {journey.id}: No match "
            f"(time={journey.datetime.time()}, dir={journey.direction}, "
            f"dest={journey.destination})"
        )
        return False

    def get_journey_locations(self, journey):
        try:
            journey_key = journey.uuid.bytes
            locations = redis_client.lrange(journey_key, 0, -1)
            
            if not locations:
                return []

            location_data = []
            journey_start = journey.datetime

            for loc_bytes in locations:
                try:
                    loc_dict = VehicleLocation.decode_appendage(loc_bytes)
                    loc_datetime = loc_dict["datetime"]

                    if abs((loc_datetime - journey_start).total_seconds()) < 3600:
                        location_data.append(loc_dict)
                except Exception as e:
                    continue

            return location_data
        except Exception as e:
            logger.exception(f"Error getting locations for journey {journey.id}: {e}")
            return []

    def get_nearby_stops_from_locations(self, location_data, limit=5):
        if not location_data:
            return []

        points = [
            Point(loc["coordinates"][0], loc["coordinates"][1], srid=4326)
            for loc in location_data
            if loc.get("coordinates")
        ]

        if not points:
            return []

        center_lon = sum(p.x for p in points) / len(points)
        center_lat = sum(p.y for p in points) / len(points)
        center_point = Point(center_lon, center_lat, srid=4326)

        stops = list(
            StopPoint.objects.filter(
                latlong__isnull=False,
                naptan_code__isnull=False,
            )
            .annotate(distance=Distance("latlong", center_point))
            .order_by("distance")[:limit]
        )

        return stops

    def get_nearest_stop_from_locations(self, location_data):
        stops = self.get_nearby_stops_from_locations(location_data, limit=1)
        return stops[0] if stops else None

    def get_destination_ref(self, destination_name):
        if not destination_name:
            return ""

        stop = StopPoint.objects.filter(
            common_name__icontains=destination_name
        ).first()

        return stop.atco_code if stop else ""
