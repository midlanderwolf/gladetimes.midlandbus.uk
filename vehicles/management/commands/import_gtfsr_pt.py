from .import_gtfsr_generic import Command as GenericCommand


class Command(GenericCommand):
    source_name = "PT"
    vehicle_code_scheme = "PT"
    url = "https://mkuran.pl/gtfs/polish_trains/updates.pb"

    def do_source(self):
        self.session.headers.update({"User-Agent": "bustimes.org"})
        super().do_source()
        self.url = self.url
        return self
