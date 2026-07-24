from .import_gtfsr_generic import Command as GenericCommand


class Command(GenericCommand):
    source_name = "Transport for New South Wales"
    vehicle_code_scheme = "TFNSW"
    url = "https://api.transport.nsw.gov.au/v1/gtfs/vehiclepositions"

    def do_source(self):
        self.session.headers.update({"User-Agent": "bustimes.org"})
        super().do_source()
        self.url = self.url
        return self
