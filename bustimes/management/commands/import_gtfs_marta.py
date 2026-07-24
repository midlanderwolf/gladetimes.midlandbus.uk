from .import_gtfs_generic import Command as GenericCommand


class Command(GenericCommand):
    source_name = "MARTA"
    region_id = "US"
    noc = "MARTA"
    url = "https://gtfs-rt.itsmarta.com/TMGTFSRealTimeWebService/GTFS/GTFS.zip"

    def handle(self, *args, **options):
        options["source_name"] = self.source_name
        options["region"] = self.region_id
        options["url"] = self.url
        super().handle(*args, **options)
