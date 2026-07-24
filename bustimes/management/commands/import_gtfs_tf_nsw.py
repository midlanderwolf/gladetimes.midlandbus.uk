from .import_gtfs_generic import Command as GenericCommand


class Command(GenericCommand):
    source_name = "TFNSW"
    region_id = "NSW"
    noc = "TFNSW"
    url = "https://transportnsw.info/opendata/gtfs/gtfs-routes/nsw-trains.zip"

    def handle(self, *args, **options):
        options["source_name"] = self.source_name
        options["region"] = self.region_id
        options["url"] = self.url
        super().handle(*args, **options)
