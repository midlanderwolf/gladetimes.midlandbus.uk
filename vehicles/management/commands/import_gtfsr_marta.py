from .import_gtfsr_generic import Command as GenericCommand


class Command(GenericCommand):
    source_name = "MARTA"
    vehicle_code_scheme = "MARTA"
    url = "https://gtfs-rt.itsmarta.com/TMGTFSRealTimeWebService/vehicle/vehiclepositions.pb"

    def do_source(self):
        self.session.headers.update({"User-Agent": "bustimes.org"})
        super().do_source()
        self.url = self.url
        return self
