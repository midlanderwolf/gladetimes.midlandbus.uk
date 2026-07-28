from .import_gtfs_generic import Command as GenericCommand


class Command(GenericCommand):
    source_name = "OVAPI"
    region_id = "NL"
    url = "https://gtfs.ovapi.nl/nl/gtfs-nl.zip"

    def add_arguments(self, parser):
        parser.add_argument(
            "--local",
            action="store_true",
            help="Skip download and use the existing local file",
        )

    def handle(self, *args, **options):
        options["source_name"] = self.source_name
        options["region"] = self.region_id
        options["url"] = self.url
        super().handle(*args, **options)
