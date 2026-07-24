from .import_gtfs_generic import Command as GenericCommand


class Command(GenericCommand):
    source_name = "PT"
    region_id = "PL"
    noc = "PT"
    url = "https://mkuran.pl/gtfs/polish_trains.zip"

    def handle(self, *args, **options):
        options["source_name"] = self.source_name
        options["region"] = self.region_id
        options["url"] = self.url
        super().handle(*args, **options)
