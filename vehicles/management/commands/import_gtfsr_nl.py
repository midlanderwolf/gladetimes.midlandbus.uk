from zoneinfo import ZoneInfo

from .import_gtfsr_generic import Command as GenericCommand


class Command(GenericCommand):
    source_name = "OVAPI"
    vehicle_code_scheme = "OVAPI"
    url = "https://gtfs.ovapi.nl/nl/vehiclePositions.pb"

    def add_arguments(self, parser):
        pass

    def do_source(self):
        self.session.headers.update({"User-Agent": "bustimes.org"})
        super().do_source()
        return self

    def get_timezone(self):
        tz = super().get_timezone()
        if tz is None:
            return ZoneInfo("Europe/Amsterdam")
        return tz

    @staticmethod
    def get_vehicle_identity(item):
        vehicle_id = item.vehicle.vehicle.id.strip() if item.vehicle.vehicle.id else ""
        label = item.vehicle.vehicle.label.strip() if item.vehicle.vehicle.label else ""
        trip_id = item.vehicle.trip.trip_id if item.vehicle.trip.trip_id else ""
        if vehicle_id:
            return vehicle_id
        if label:
            return f"OV{label}"
        if trip_id:
            return f"trip-{trip_id}"
        return ""

    @staticmethod
    def get_journey_identity(item):
        return (
            item.vehicle.trip.route_id if item.vehicle.trip.route_id else "",
            item.vehicle.trip.trip_id if item.vehicle.trip.trip_id else "",
            item.vehicle.trip.start_date if item.vehicle.trip.start_date else "",
        )

    def get_vehicle(self, item):
        from ...models import Vehicle
        vehicle_id = item.vehicle.vehicle.id.strip() if item.vehicle.vehicle.id else ""
        label = item.vehicle.vehicle.label.strip() if item.vehicle.vehicle.label else ""

        if vehicle_id:
            code = vehicle_id
        elif label:
            code = f"OV{label}"
        else:
            return None, False

        defaults = {"fleet_code": code[:24]}
        if label and label.isdigit():
            defaults["fleet_number"] = int(label)

        return Vehicle.objects.get_or_create(
            code=code,
            source=self.source,
            defaults=defaults,
        )
