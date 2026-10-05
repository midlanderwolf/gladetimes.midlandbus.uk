from pathlib import Path
from unittest import mock

import fakeredis
import time_machine
import vcr
from django.core.management import call_command
from django.test import TestCase

from busstops.models import DataSource, Operator

from ...models import VehicleJourney


class GuernseyTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        DataSource.objects.create(
            name="guernsey", url="https://example.com/api", settings={}
        )
        Operator.objects.create(noc="SGUE")

    def test(self):
        """Stagecoach Guernsey (buses.gg) AVL"""

        redis_client = fakeredis.FakeStrictRedis(version=7)

        with (
            vcr.use_cassette(
                str(Path(__file__).resolve().parent / "vcr" / "newport.yaml")
            ) as cassette,
            mock.patch(
                "vehicles.management.import_live_vehicles.redis_client", redis_client
            ),
            mock.patch("vehicles.management.import_live_vehicles.sleep"),
            time_machine.travel("2025-09-24T06:35:00+00:00", tick=False),
        ):
            with (
                self.assertNumQueries(28),
                self.assertRaises(vcr.errors.CannotOverwriteExistingCassetteException),
            ):
                call_command("newport", "--immediate")

            cassette.rewind()

            with (
                self.assertNumQueries(2),
                self.assertRaises(vcr.errors.CannotOverwriteExistingCassetteException),
            ):
                call_command("newport", "--immediate")

        self.assertEqual(VehicleJourney.objects.count(), 3)
