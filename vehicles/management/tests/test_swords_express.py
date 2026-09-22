from pathlib import Path
from unittest.mock import patch

import fakeredis
import time_machine
import vcr
from django.core.management import call_command
from django.test import TestCase

from busstops.models import Operator

from ...models import Vehicle


class StopLoop(Exception):
    """raised to break out of the command's polling loop in tests"""


class SignalRTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        Operator.objects.create(name="Swords Express")

    def test(self):
        redis_client = fakeredis.FakeStrictRedis(version=7)

        with (
            vcr.use_cassette(
                str(Path(__file__).resolve().parent / "vcr" / "swords_express.yaml")
            ),
            patch(
                "vehicles.management.import_live_vehicles.redis_client", redis_client
            ),
            time_machine.travel("2026-06-28", tick=False),
            patch(
                "vehicles.management.import_live_vehicles.sleep",
                side_effect=[None, None, StopLoop],
            ),
        ):
            with (
                self.assertNumQueries(237),
                self.assertRaises(StopLoop),
            ):
                call_command("swords_express")

            vehicles = Vehicle.objects.order_by("id")

            self.assertEqual(vehicles[0].reg, "05-D-7790")
