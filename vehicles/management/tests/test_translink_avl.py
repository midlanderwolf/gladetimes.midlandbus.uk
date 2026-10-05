from pathlib import Path
from unittest import mock

import fakeredis
import time_machine
import vcr
from django.core.management import call_command
from django.test import TestCase

from busstops.models import Operator

from ...models import VehicleJourney


class StopLoop(Exception):
    """raised to break out of the command's polling loop in tests"""


class TranslinkAVLTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        Operator.objects.bulk_create(
            [Operator(noc="ULB"), Operator(noc="MET"), Operator(noc="GDR")]
        )

    def test(self):
        """Translink (Northern Ireland) AVL"""

        redis_client = fakeredis.FakeStrictRedis(version=7)

        with (
            vcr.use_cassette(
                str(Path(__file__).resolve().parent / "vcr" / "translink_avl.yaml")
            ) as cassette,
            mock.patch(
                "vehicles.management.import_live_vehicles.redis_client", redis_client
            ),
            mock.patch(
                "vehicles.management.import_live_vehicles.sleep", side_effect=StopLoop
            ),
            time_machine.travel("2025-09-27T05:43:00+00:00", tick=False),
        ):
            with self.assertNumQueries(96), self.assertRaises(StopLoop):
                call_command("import_translink_avl", "--immediate")

            cassette.rewind()

            with self.assertNumQueries(5), self.assertRaises(StopLoop):
                call_command("import_translink_avl", "--immediate")

        self.assertEqual(VehicleJourney.objects.count(), 8)
