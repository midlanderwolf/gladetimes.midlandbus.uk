from .import_gtfsr_generic import Command as GenericCommand


class Command(GenericCommand):
    source_name = "Go Metro"
    vehicle_code_scheme = "GM"
    url = "https://tmgtfsprd.sorttrpcloud.com/TMGTFSRealTimeWebService/vehicle/vehiclepositions.pb"

    def do_source(self):
        self.session.headers.update({"User-Agent": "bustimes.org"})
        super().do_source()
        self.url = self.url
        return self
