from busstops.models import DataSource

from ...models import Operator, Vehicle
from ..import_live_vehicles import ImportLiveVehiclesCommand
from .import_gtfsr_generic import Command as GenericCommand


class Command(GenericCommand):
    source_name = "FlixBus"
    vehicle_code_scheme = "FLIX"

    @staticmethod
    def get_vehicle_identity(item):
        vehicle = item.vehicle.vehicle
        if vehicle.label and vehicle.label.strip():
            return vehicle.label.strip()
        return vehicle.id

    def get_vehicle(self, item):
        vehicle_id = item.vehicle.vehicle.id.strip()
        vehicle_label = item.vehicle.vehicle.label.strip() if item.vehicle.vehicle.label else None
        fleet_code = vehicle_label[:24] if vehicle_label else vehicle_id[:24]
        return Vehicle.objects.get_or_create(
            {"fleet_code": fleet_code},
            operator=self.operator,
            code=vehicle_id,
        )

    def do_source(self):
        self.url = "https://flixbus.midlandbus.uk/feeds/vehicle_positions.pb"
        self.source, _ = DataSource.objects.get_or_create(name=self.source_name)
        self.operator = Operator.objects.get(noc="FLIX")

        self._tzinfo = self.get_timezone()
        if not self._tzinfo:
            raise ValueError(
                "No timezone found for operator FLIX. "
                "Ensure the operator has a timezone set from the GTFS import."
            )

        return self

    def add_arguments(self, parser):
        ImportLiveVehiclesCommand.add_arguments(parser)

    def handle(self, *args, **options):
        ImportLiveVehiclesCommand.handle(self, *args, **options)
