from .import_gtfsr_generic import Command as GenericCommand


class Command(GenericCommand):
    source_name = "HSL"
    vehicle_code_scheme = "HSL"
    url = "https://realtime.hsl.fi/realtime/vehicle-positions/v2/hsl"

    def add_arguments(self, parser):
        pass

    def do_source(self):
        self.session.headers.update({"User-Agent": "bustimes.org"})
        super().do_source()
        self.url = self.url
        return self
