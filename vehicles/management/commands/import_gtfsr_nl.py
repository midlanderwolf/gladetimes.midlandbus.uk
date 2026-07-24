from zoneinfo import ZoneInfo

from .import_gtfsr_generic import Command as GenericCommand


class Command(GenericCommand):
    source_name = "OVAPI"
    vehicle_code_scheme = "OVAPI"
    url = "https://gtfs.openov.nl/gtfs-rt/vehiclePositions.pb"

    def do_source(self):
        self.session.headers.update({"User-Agent": "bustimes.org"})
        super().do_source()
        self.url = self.url
        return self

    def get_timezone(self):
        tz = super().get_timezone()
        if tz is None:
            return ZoneInfo("Europe/Amsterdam")
        return tz
