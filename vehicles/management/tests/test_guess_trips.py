import struct
from datetime import datetime, timedelta
from unittest.mock import patch

import fakeredis
from django.contrib.gis.geos import Point
from django.test import TestCase
from django.utils import timezone

from busstops.models import DataSource, Operator, Region, OperatorCode, StopPoint
from bustimes.models import Calendar, Route, Trip, StopTime, Service

from ...models import Vehicle, VehicleJourney
from ..commands.guess_trips import Command


@patch(
    "vehicles.management.commands.guess_trips.redis_client",
    fakeredis.FakeStrictRedis(),
)
class GuessTripsTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.source = DataSource.objects.create(name="Test Source")
        cls.region = Region.objects.create(id="TE")
        cls.operator = Operator.objects.create(
            noc="TEST", name="Test Operator", region=cls.region
        )
        OperatorCode.objects.create(
            operator=cls.operator, source=cls.source, code="TEST"
        )

        cls.calendar = Calendar.objects.create(
            mon=True,
            tue=True,
            wed=True,
            thu=True,
            fri=True,
            sat=True,
            sun=True,
        )

        cls.service = Service.objects.create(
            service_code="TEST01",
            line_name="1",
            operator=cls.operator,
            current=True,
        )

        cls.route = Route.objects.create(
            service=cls.service,
            source=cls.source,
            outbound_description="Point A to Point B",
            inbound_description="Point B to Point A",
        )

        cls.stop1 = StopPoint.objects.create(
            atco_code="0100001",
            naptan_code="0100001",
            common_name="Stop One",
            latlong=Point(-1.535843, 53.797578),
        )

        cls.stop2 = StopPoint.objects.create(
            atco_code="0100002",
            naptan_code="0100002",
            common_name="Stop Two",
            latlong=Point(-1.536000, 53.798000),
        )

        cls.trip = Trip.objects.create(
            route=cls.route,
            calendar=cls.calendar,
            start=timedelta(hours=9),
            end=timedelta(hours=10),
            inbound=False,
        )

        StopTime.objects.create(
            trip=cls.trip,
            stop=cls.stop1,
            departure=timedelta(hours=9),
            sequence=1,
            pick_up=True,
        )

        StopTime.objects.create(
            trip=cls.trip,
            stop=cls.stop2,
            departure=timedelta(hours=9, minutes=30),
            sequence=2,
            pick_up=True,
        )

    def test_guess_trip_for_journey(self):
        vehicle = Vehicle.objects.create(
            code="TEST001",
            operator=self.operator,
            source=self.source,
        )

        now = timezone.localtime()
        journey = VehicleJourney.objects.create(
            vehicle=vehicle,
            service=self.service,
            source=self.source,
            datetime=now,
            route_name="1",
            direction="outbound",
            destination="Stop Two",
        )

        redis_client = fakeredis.FakeStrictRedis()
        journey_key = journey.uuid.bytes

        location_data = struct.pack(
            "I 2f ?h ?h",
            int(now.timestamp()),
            -1.535843,
            53.797578,
            True,
            90,
            False,
            0,
        )
        redis_client.rpush(journey_key, location_data)

        with patch(
            "vehicles.management.commands.guess_trips.redis_client", redis_client
        ):
            command = Command()
            result = command.guess_trip_for_journey(journey)

        self.assertTrue(result)
        journey.refresh_from_db()
        self.assertEqual(journey.trip, self.trip)

    def test_guess_trip_no_location_data(self):
        vehicle = Vehicle.objects.create(
            code="TEST002",
            operator=self.operator,
            source=self.source,
        )

        now = timezone.localtime()
        journey = VehicleJourney.objects.create(
            vehicle=vehicle,
            service=self.service,
            source=self.source,
            datetime=now,
            route_name="1",
            direction="outbound",
        )

        redis_client = fakeredis.FakeStrictRedis()

        with patch(
            "vehicles.management.commands.guess_trips.redis_client", redis_client
        ):
            command = Command()
            result = command.guess_trip_for_journey(journey)

        self.assertFalse(result)
        journey.refresh_from_db()
        self.assertIsNone(journey.trip)

    def test_guess_trip_no_service(self):
        vehicle = Vehicle.objects.create(
            code="TEST003",
            operator=self.operator,
            source=self.source,
        )

        now = timezone.localtime()
        journey = VehicleJourney.objects.create(
            vehicle=vehicle,
            service=None,
            source=self.source,
            datetime=now,
            route_name="1",
            direction="outbound",
        )

        command = Command()
        result = command.guess_trip_for_journey(journey)

        self.assertFalse(result)

    def test_handle_command(self):
        vehicle = Vehicle.objects.create(
            code="TEST004",
            operator=self.operator,
            source=self.source,
        )

        now = timezone.localtime()
        journey = VehicleJourney.objects.create(
            vehicle=vehicle,
            service=self.service,
            source=self.source,
            datetime=now,
            route_name="1",
            direction="outbound",
            destination="Stop Two",
        )

        redis_client = fakeredis.FakeStrictRedis()
        journey_key = journey.uuid.bytes

        location_data = struct.pack(
            "I 2f ?h ?h",
            int(now.timestamp()),
            -1.535843,
            53.797578,
            True,
            90,
            False,
            0,
        )
        redis_client.rpush(journey_key, location_data)

        with patch(
            "vehicles.management.commands.guess_trips.redis_client", redis_client
        ):
            command = Command()
            command.handle(source="Test Source", limit=10)

        journey.refresh_from_db()
        self.assertEqual(journey.trip, self.trip)
