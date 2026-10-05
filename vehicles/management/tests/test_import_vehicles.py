from io import StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from busstops.models import Operator, OperatorGroup

from ...models import Vehicle
from ..commands.import_vehicles import (
    merge_duplicate_vehicles_by_reg,
    merge_duplicate_vehicles_in_group,
)


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

    @mock.patch("vehicles.management.commands.import_vehicles.requests.get")
    def test_does_not_match_unrelated_vehicle_with_similar_fleet_number(self, get):
        """Fleet numbers "151" and "1151" are two different vehicles, not
        one renumbered - matching must never treat a fleet number as
        matching just because it happens to end with another vehicle's
        number (regression test for a bad "renumbered vehicle" heuristic
        that merged these together).
        """
        operator = Operator.objects.create(noc="SWWD")
        vehicle_151 = Vehicle.objects.create(
            operator=operator, code="151", fleet_number=151, slug="swwd-151"
        )
        vehicle_1151 = Vehicle.objects.create(
            operator=operator, code="1151", fleet_number=1151, slug="swwd-1151"
        )

        get.return_value = response_with_json(
            {
                "results": [vehicle_data("151", slug="swwd-151")],
                "next": None,
            }
        )

        out = StringIO()
        call_command("import_vehicles", "SWWD", stdout=out)

        self.assertTrue(Vehicle.objects.filter(pk=vehicle_151.pk).exists())
        self.assertTrue(Vehicle.objects.filter(pk=vehicle_1151.pk).exists())
        self.assertEqual(Vehicle.objects.count(), 2)
        vehicle_1151.refresh_from_db()
        self.assertEqual(vehicle_1151.fleet_number, 1151)


class ImportVehiclesByGroupTest(TestCase):
    @mock.patch("vehicles.management.commands.import_vehicles.requests.get")
    def test_imports_every_operator_in_the_group(self, get):
        group = OperatorGroup.objects.create(name="Group", slug="group")
        tfcn = Operator.objects.create(noc="TFCN", group=group)
        plyc = Operator.objects.create(noc="PLYC", group=group)
        Operator.objects.create(noc="OTHER")  # not in the group

        def fake_get(url):
            if "operator=TFCN" in url:
                return response_with_json(
                    {"results": [vehicle_data("1")], "next": None}
                )
            if "operator=PLYC" in url:
                return response_with_json(
                    {"results": [vehicle_data("2")], "next": None}
                )
            raise AssertionError(f"unexpected url {url}")

        get.side_effect = fake_get

        out = StringIO()
        call_command("import_vehicles", "group", stdout=out)

        self.assertEqual(Vehicle.objects.filter(operator=tfcn).count(), 1)
        self.assertEqual(Vehicle.objects.filter(operator=plyc).count(), 1)
        self.assertEqual(
            {call.args[0] for call in get.call_args_list},
            {
                "https://bustimes.org/api/vehicles/?format=json&limit=9999&operator=TFCN",
                "https://bustimes.org/api/vehicles/?format=json&limit=9999&operator=PLYC",
            },
        )

    @mock.patch("vehicles.management.commands.import_vehicles.requests.get")
    def test_resolves_group_by_name_case_insensitively(self, get):
        group = OperatorGroup.objects.create(name="Group", slug="group")
        Operator.objects.create(noc="TFCN", group=group)
        Operator.objects.create(noc="PLYC", group=group)

        get.return_value = response_with_json({"results": [], "next": None})

        out = StringIO()
        call_command("import_vehicles", "GROUP", stdout=out)  # matches by name, not slug

        self.assertIn("Successfully imported vehicles for TFCN", out.getvalue())
        self.assertIn("Successfully imported vehicles for PLYC", out.getvalue())
        self.assertEqual(get.call_count, 2)

    @mock.patch("vehicles.management.commands.import_vehicles.requests.get")
    def test_corrects_vehicle_to_its_real_operator_within_the_group(self, get):
        """bustimes.org can include a vehicle in one operator's list that
        it itself attributes to a *different* operator in the same group
        (e.g. a PLYC query including a vehicle it says is really TFCN's).
        Importing the whole group should put it under its real operator,
        not under whichever operator's query happened to return it.
        """
        group = OperatorGroup.objects.create(name="Group", slug="group")
        tfcn = Operator.objects.create(noc="TFCN", group=group)
        plyc = Operator.objects.create(noc="PLYC", group=group)

        def fake_get(url):
            if "operator=PLYC" in url:
                return response_with_json(
                    {
                        "results": [
                            vehicle_data("1", operator={"id": "TFCN"}),
                        ],
                        "next": None,
                    }
                )
            return response_with_json({"results": [], "next": None})

        get.side_effect = fake_get

        out = StringIO()
        call_command("import_vehicles", "group", stdout=out)

        self.assertEqual(Vehicle.objects.filter(operator=tfcn).count(), 1)
        self.assertEqual(Vehicle.objects.filter(operator=plyc).count(), 0)

    @mock.patch("vehicles.management.commands.import_vehicles.requests.get")
    def test_does_not_delete_active_vehicle_homed_under_other_group_operator(
        self, get
    ):
        """A vehicle that's genuinely active under TFCN can still show up
        as a withdrawn/dead-running echo in PLYC's own feed (sharing its
        fleet number). That echo must not delete the real, active TFCN
        vehicle just because group-wide matching happens to find it.
        """
        group = OperatorGroup.objects.create(name="Group", slug="group")
        tfcn = Operator.objects.create(noc="TFCN", group=group)
        Operator.objects.create(noc="PLYC", group=group)
        vehicle = Vehicle.objects.create(operator=tfcn, code="42", fleet_number=42)

        def fake_get(url):
            if "operator=PLYC" in url:
                return response_with_json(
                    {
                        "results": [vehicle_data("42", withdrawn=True)],
                        "next": None,
                    }
                )
            # TFCN's own feed doesn't mention it this time (e.g. a
            # transient gap) - this test is specifically about the PLYC
            # echo not being allowed to delete it regardless
            return response_with_json({"results": [], "next": None})

        get.side_effect = fake_get

        out = StringIO()
        call_command("import_vehicles", "group", stdout=out)

        self.assertTrue(Vehicle.objects.filter(pk=vehicle.pk).exists())
        self.assertIn("0 removed", out.getvalue())

    @mock.patch("vehicles.management.commands.import_vehicles.requests.get")
    def test_merges_preexisting_duplicate_sharing_fleet_number_no_reg(self, get):
        """A vehicle that's ended up duplicated across two operators in
        the group - sharing a fleet number but with no reg recorded on
        either row (as these dead-running/SVCT-attributed entries are) -
        should get merged down to one by the time a group import finishes,
        rather than being left as two separate records.
        """
        group = OperatorGroup.objects.create(name="Group", slug="group")
        tfcn = Operator.objects.create(noc="TFCN", group=group)
        svct = Operator.objects.create(noc="SVCT", group=group)
        real_vehicle = Vehicle.objects.create(
            operator=tfcn, code="42", fleet_number=42
        )
        ghost_vehicle = Vehicle.objects.create(
            operator=svct, code="42", fleet_number=42, withdrawn=True
        )

        get.return_value = response_with_json({"results": [], "next": None})

        out = StringIO()
        call_command("import_vehicles", "group", stdout=out)

        self.assertEqual(Vehicle.objects.count(), 1)
        self.assertFalse(Vehicle.objects.filter(pk=ghost_vehicle.pk).exists())
        self.assertTrue(Vehicle.objects.filter(pk=real_vehicle.pk).exists())
        self.assertIn("Merged 1 duplicate vehicle(s)", out.getvalue())


class MergeDuplicateVehiclesByRegTest(TestCase):
    def test_merges_across_different_operators(self):
        old_operator = Operator.objects.create(noc="OLD")
        new_operator = Operator.objects.create(noc="NEW")
        old_vehicle = Vehicle.objects.create(
            operator=old_operator, code="1", reg="AB12CDE", withdrawn=True
        )
        new_vehicle = Vehicle.objects.create(
            operator=new_operator, code="2", reg="ab12cde"
        )

        merged = merge_duplicate_vehicles_by_reg()

        self.assertEqual(merged, 1)
        self.assertEqual(Vehicle.objects.count(), 1)
        self.assertFalse(Vehicle.objects.filter(pk=old_vehicle.pk).exists())
        self.assertTrue(Vehicle.objects.filter(pk=new_vehicle.pk).exists())

    def test_leaves_same_operator_duplicates_alone(self):
        operator = Operator.objects.create(noc="TEST")
        Vehicle.objects.create(operator=operator, code="1", reg="AB12CDE")
        Vehicle.objects.create(operator=operator, code="2", reg="AB12CDE")

        merged = merge_duplicate_vehicles_by_reg()

        self.assertEqual(merged, 0)
        self.assertEqual(Vehicle.objects.count(), 2)

    def test_scoped_to_given_operators(self):
        operator_a = Operator.objects.create(noc="A")
        operator_b = Operator.objects.create(noc="B")
        operator_c = Operator.objects.create(noc="C")
        Vehicle.objects.create(operator=operator_a, code="1", reg="AB12CDE")
        Vehicle.objects.create(operator=operator_b, code="2", reg="AB12CDE")
        Vehicle.objects.create(operator=operator_c, code="3", reg="AB12CDE")

        merged = merge_duplicate_vehicles_by_reg(
            Operator.objects.filter(noc__in=["A", "B"])
        )

        self.assertEqual(merged, 1)
        self.assertEqual(Vehicle.objects.count(), 2)
        self.assertTrue(Vehicle.objects.filter(operator=operator_c).exists())


class MergeDuplicatesByRegAdminViewTest(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="admin", email="admin@example.com", password="password"
        )
        self.client.force_login(self.user)

    def test_get(self):
        response = self.client.get(
            reverse("admin:vehicles_vehicle_merge_duplicates_by_reg")
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Merge")

    def test_post_merges_and_redirects(self):
        old_operator = Operator.objects.create(noc="TFCN")
        new_operator = Operator.objects.create(noc="PLYC")
        Vehicle.objects.create(operator=old_operator, code="1", reg="AB12CDE")
        Vehicle.objects.create(operator=new_operator, code="2", reg="AB12CDE")

        response = self.client.post(
            reverse("admin:vehicles_vehicle_merge_duplicates_by_reg"),
            {"operators": ["TFCN", "PLYC"]},
        )

        self.assertRedirects(
            response, reverse("admin:vehicles_vehicle_changelist")
        )
        self.assertEqual(Vehicle.objects.count(), 1)


class MergeDuplicateVehiclesInGroupTest(TestCase):
    def test_merges_within_group(self):
        group = OperatorGroup.objects.create(name="Group", slug="group")
        old_operator = Operator.objects.create(noc="TFCN", group=group)
        new_operator = Operator.objects.create(noc="PLYC", group=group)
        other_operator = Operator.objects.create(noc="OTHER")
        old_vehicle = Vehicle.objects.create(
            operator=old_operator, code="1", fleet_number=42, withdrawn=True
        )
        new_vehicle = Vehicle.objects.create(
            operator=new_operator, code="2", fleet_number=42
        )
        Vehicle.objects.create(operator=other_operator, code="3", fleet_number=42)

        merged = merge_duplicate_vehicles_in_group(group)

        self.assertEqual(merged, 1)
        self.assertFalse(Vehicle.objects.filter(pk=old_vehicle.pk).exists())
        self.assertTrue(Vehicle.objects.filter(pk=new_vehicle.pk).exists())
        # the vehicle under the unrelated operator (not in the group) is untouched
        self.assertEqual(Vehicle.objects.filter(operator=other_operator).count(), 1)


class MergeDuplicatesByGroupAdminViewTest(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="admin", email="admin@example.com", password="password"
        )
        self.client.force_login(self.user)

    def test_get(self):
        response = self.client.get(
            reverse("admin:vehicles_vehicle_merge_duplicates_by_group")
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Merge")

    def test_post_merges_and_redirects(self):
        group = OperatorGroup.objects.create(name="Group", slug="group")
        old_operator = Operator.objects.create(noc="TFCN", group=group)
        new_operator = Operator.objects.create(noc="PLYC", group=group)
        Vehicle.objects.create(operator=old_operator, code="1", fleet_number=42)
        Vehicle.objects.create(operator=new_operator, code="2", fleet_number=42)

        response = self.client.post(
            reverse("admin:vehicles_vehicle_merge_duplicates_by_group"),
            {"group": group.pk},
        )

        self.assertRedirects(
            response, reverse("admin:vehicles_vehicle_changelist")
        )
        self.assertEqual(Vehicle.objects.count(), 1)

    def test_post_rejects_group_without_shared_fleet_numbering(self):
        group = OperatorGroup.objects.create(
            name="Group", slug="group", group_fleet_numbering=False
        )

        response = self.client.post(
            reverse("admin:vehicles_vehicle_merge_duplicates_by_group"),
            {"group": group.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "group_fleet_numbering is off")
