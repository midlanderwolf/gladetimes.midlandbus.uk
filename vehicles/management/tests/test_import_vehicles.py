from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase

from busstops.models import Operator

from ...models import Vehicle


def response_with_json(data):
    response = mock.Mock()
    response.json.return_value = data
    return response


def vehicle_data(fleet_number, **kwargs):
    return {
        "fleet_number": fleet_number,
        "fleet_code": str(fleet_number),
        "reg": "",
        "slug": f"test-{fleet_number}",
        "vehicle_type": None,
        "livery": None,
        "name": "",
        "branding": "",
        "notes": "",
        "withdrawn": False,
        "special_features": None,
        **kwargs,
    }


class ImportVehiclesTest(TestCase):
    @mock.patch("vehicles.management.commands.import_vehicles.requests.get")
    def test_follows_paginated_api_results(self, get):
        operator = Operator.objects.create(noc="TEST")
        Vehicle.objects.create(operator=operator, code="1", name="Old name")

        initial_url = (
            "https://bustimes.org/api/vehicles/"
            "?format=json&limit=9999&operator=TEST"
        )
        next_url = (
            "https://bustimes.org/api/vehicles/"
            "?format=json&limit=9999&operator=TEST&offset=9999"
        )
        get.side_effect = [
            response_with_json(
                {
                    "results": [
                        vehicle_data("1", name="Updated name"),
                        vehicle_data("2"),
                    ],
                    "next": next_url,
                }
            ),
            response_with_json(
                {
                    "results": [
                        vehicle_data("3"),
                    ],
                    "next": None,
                }
            ),
        ]

        out = StringIO()
        call_command("import_vehicles", "TEST", stdout=out)

        self.assertEqual(Vehicle.objects.filter(operator=operator).count(), 3)
        self.assertEqual(
            Vehicle.objects.get(operator=operator, code="1").name,
            "Updated name",
        )
        self.assertEqual(
            get.call_args_list,
            [
                mock.call(initial_url),
                mock.call(next_url),
            ],
        )
        self.assertIn("Vehicles for TEST: 2 created, 1 updated", out.getvalue())
