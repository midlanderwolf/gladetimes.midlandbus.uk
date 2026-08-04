import datetime
from pathlib import Path
from unittest.mock import Mock, patch

import fakeredis
import time_machine
from django.core.management import call_command
from django.test import TestCase, override_settings
from google.protobuf import json_format
from google.transit import gtfs_realtime_pb2

from busstops.models import Operator, Service
from vehicles.management.commands import import_gtfsr_nevada
from vehicles.models import Vehicle, VehicleJourney

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


# test data in `rtcsnv.zip` dir (not a real zipfile) in fixtures dir
@override_settings(DATA_DIR=FIXTURES_DIR)
class NevadaTest(TestCase):
    def test_not_modified(self):
        with (
            patch(
                "bustimes.management.commands.import_gtfs_nevada.download_if_modified",
                return_value=(False, None),
            ),
            self.assertNumQueries(4),
        ):
            call_command("import_gtfs_nevada")

    @time_machine.travel("2026-08-04", tick=False)
    def test_import_gtfs_nevada(self):
        with patch(
            "bustimes.management.commands.import_gtfs_nevada.download_if_modified",
            return_value=(
                True,
                datetime.datetime(2024, 6, 18, 10, 0, 0, tzinfo=datetime.UTC),
            ),
        ):
            call_command("import_gtfs_nevada")

            self.assertEqual(Service.objects.all().count(), 2)

            # re-importing shouldn't re-create services

            call_command("import_gtfs_nevada")
            self.assertEqual(Service.objects.all().count(), 2)

        response = self.client.get("/operators/rtcsnv")
        self.assertContains(response, """<a href="/services/deuce-the-deucestrip">""")

        response = self.client.get("/services/deuce-the-deucestrip")
        self.assertContains(response, "/stops/rtcsnv-6038")

        response = self.client.get("/stops/rtcsnv-6038")
        # Pacific Time
        self.assertContains(
            response, """<input type="time" name="time" value="16:00">"""
        )

    def test_vehicle_position(self):
        Operator.objects.create(noc="RTCSNV", name="RTC")

        feed = gtfs_realtime_pb2.FeedMessage()
        json_format.ParseDict(
            {
                "header": {"gtfsRealtimeVersion": "2.0", "timestamp": "1785835126"},
                "entity": [
                    {
                        "id": "26627",
                        "vehicle": {
                            "trip": {
                                "tripId": "358022630",
                                "routeId": "4725",
                                "startDate": "20260803",
                                "startTime": "26:25:00",
                                "directionId": 0,
                                "scheduleRelationship": "SCHEDULED",
                            },
                            "stopId": "2770",
                            "vehicle": {"id": "26627", "label": "26627"},
                            "position": {
                                "latitude": 36.18293,
                                "longitude": -115.31084,
                            },
                            "timestamp": "1785835126",
                            "currentStatus": "STOPPED_AT",
                            "occupancyStatus": "MANY_SEATS_AVAILABLE",
                            "currentStopSequence": 1,
                            "occupancyPercentage": 2,
                        },
                    }
                ],
            },
            feed,
        )
        response = Mock(status_code=200, content=feed.SerializeToString(), headers={})

        command = import_gtfsr_nevada.Command()
        command.do_source()

        with (
            patch.object(command.session, "get", return_value=response),
            patch(
                "vehicles.management.import_live_vehicles.redis_client",
                fakeredis.FakeStrictRedis(),
            ),
        ):
            command.update()

        vehicle = Vehicle.objects.get()
        self.assertEqual(vehicle.fleet_code, "26627")

        journey = VehicleJourney.objects.get()
        # start_time "26:25:00" and date 2026-08-03
        # means 02:25 Pacific Time the next day
        self.assertEqual(str(journey.date), "2026-08-03")
        self.assertEqual(str(journey.datetime), "2026-08-04 09:25:00+00:00")
