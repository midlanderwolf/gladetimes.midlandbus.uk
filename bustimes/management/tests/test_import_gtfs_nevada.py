import datetime
from pathlib import Path
from unittest.mock import patch

import time_machine
from django.core.management import call_command
from django.test import TestCase, override_settings

from busstops.models import Service

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
