from .import_gtfs_generic import Command as GenericCommand


class Command(GenericCommand):
    source_name = "Go Metro"
    region_id = "OH"
    noc = "GM"
    url = "https://www.go-metro.com/gtfs/gtfs.zip"

    def handle(self, *args, **options):
        options["source_name"] = self.source_name
        options["region"] = self.region_id
        options["url"] = self.url
        super().handle(*args, **options)
