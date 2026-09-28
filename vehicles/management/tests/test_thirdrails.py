from pathlib import Path
from unittest import mock

import fakeredis
import time_machine
import vcr
from django.core.management import call_command
from django.test import TestCase

from busstops.models import Operator

from ...models import Vehicle, VehicleJourney


class ThirdrailsTest(TestCase):
    def test(self):
        redis_client = fakeredis.FakeStrictRedis(version=7)

        with (
            vcr.use_cassette(
                str(Path(__file__).resolve().parent / "vcr" / "import_thirdrails.yaml")
            ) as cassette,
            mock.patch(
                "vehicles.management.import_live_vehicles.redis_client", redis_client
            ),
            mock.patch("vehicles.management.import_live_vehicles.sleep"),
            time_machine.travel("2026-09-23T18:27:01Z", tick=False),
        ):
            with (
                self.assertRaises(vcr.errors.CannotOverwriteExistingCassetteException),
            ):
                call_command("import_thirdrails", "--immediate")

            cassette.rewind()

        self.assertEqual(Vehicle.objects.count(), 3)
        self.assertEqual(VehicleJourney.objects.count(), 3)

        tsw = Operator.objects.get(noc="TSW")
        self.assertEqual(tsw.name, "Train Sim World")
        self.assertEqual(tsw.vehicle_mode, "train")
        self.assertEqual(tsw.vehicle_set.count(), 2)

        tsc = Operator.objects.get(noc="TSC")
        self.assertEqual(tsc.name, "Train Sim Classic")
        self.assertEqual(tsc.vehicle_set.count(), 1)

        vehicle = Vehicle.objects.get(code="fd3cee0f9e35e1f23b3eb924672d777f")
        self.assertEqual(vehicle.name, "DB_BR442_T2_C")
        self.assertEqual(vehicle.operator_id, "TSW")
        self.assertEqual(vehicle.data["driver"], "Dampf")

        journey = vehicle.latest_journey
        self.assertEqual(journey.route_name, "DB_BR442_T2_C")
        self.assertEqual(journey.destination, "Dampf")
