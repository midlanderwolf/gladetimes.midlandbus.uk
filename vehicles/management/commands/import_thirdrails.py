from django.contrib.gis.geos import Point

from busstops.models import Operator

from ...models import VehicleJourney, VehicleLocation
from ..import_live_vehicles import ImportLiveVehiclesCommand

OPERATORS = {
    "TSW": "Train Sim World",
    "TSC": "Train Sim Classic",
}


class Command(ImportLiveVehiclesCommand):
    source_name = vehicle_code_scheme = "ThirdRails"
    url = "https://www.rentor.nl/api/radar/GetRadarData"

    def do_source(self):
        self.operators = {
            noc: Operator.objects.get_or_create(
                noc=noc, defaults={"name": name, "vehicle_mode": "train"}
            )[0]
            for noc, name in OPERATORS.items()
        }
        return super().do_source()

    def get_items(self):
        response = self.session.post(self.url, json={}, timeout=20)
        response.raise_for_status()
        return [
            item
            for item in response.json()
            if item.get("UniqueName") and item.get("Points")
        ]

    @staticmethod
    def get_datetime(item):
        return None

    @staticmethod
    def get_vehicle_identity(item):
        return item["UniqueName"]

    @staticmethod
    def get_journey_identity(item):
        return item.get("Loco")

    @staticmethod
    def get_item_identity(item):
        return (item.get("Points"), item.get("Speed"))

    def get_vehicle(self, item):
        operator = self.operators.get(item.get("Simulator"))
        loco = item.get("Loco") or ""

        return self.vehicles.get_or_create(
            {
                "operator": operator,
                "name": loco[:255],
                "data": {"driver": item.get("Name") or ""},
            },
            source=self.source,
            code=item["UniqueName"],
        )

    def get_journey(self, item, vehicle):
        journey = VehicleJourney()
        journey.route_name = (item.get("Loco") or "")[:64]
        journey.destination = (item.get("Name") or "")[:255]
        journey.datetime = self.source.datetime
        return journey

    def create_vehicle_location(self, item):
        lng, lat = item["Points"].split(",")
        return VehicleLocation(latlong=Point(float(lng), float(lat)))
