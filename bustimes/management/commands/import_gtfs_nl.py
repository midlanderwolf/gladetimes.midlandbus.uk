from .import_gtfs_generic import Command as GenericCommand


class Command(GenericCommand):
    source_name = "OVAPI"
    region_id = "NL"
    noc = "OVAPI"
    url = "https://gtfs.ovapi.nl/nl/gtfs-nl.zip"

    def add_arguments(self, parser):
        pass

    def handle(self, *args, **options):
        options["source_name"] = self.source_name
        options["region"] = self.region_id
        options["url"] = self.url
        super().handle(*args, **options)
