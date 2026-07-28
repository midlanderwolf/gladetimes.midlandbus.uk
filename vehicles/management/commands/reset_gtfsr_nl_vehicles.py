from django.core.management.base import BaseCommand
from ...models import Vehicle
from busstops.models import DataSource


class Command(BaseCommand):
    help = "Delete all vehicles from OVAPI source to start fresh"

    def add_arguments(self, parser):
        parser.add_argument(
            "--confirm",
            action="store_true",
            help="Confirm deletion (required)",
        )

    def handle(self, *args, **options):
        try:
            ovapi_source = DataSource.objects.get(name="OVAPI")
        except DataSource.DoesNotExist:
            self.stdout.write(self.style.WARNING("OVAPI source not found"))
            return

        vehicles = Vehicle.objects.filter(source=ovapi_source)
        count = vehicles.count()

        self.stdout.write(f"Found {count} OVAPI vehicles")

        if count == 0:
            self.stdout.write(self.style.SUCCESS("No vehicles to delete"))
            return

        if not options["confirm"]:
            self.stdout.write(
                self.style.WARNING(
                    f"This will delete {count} vehicles, their journeys, and vehicle codes. "
                    "Add --confirm to proceed."
                )
            )
            return

        self.stdout.write(f"Deleting {count} vehicles...")
        deleted_count, details = vehicles.delete()

        self.stdout.write(
            self.style.SUCCESS(
                f"\nDeletion complete:\n"
                f"  Total deleted: {deleted_count}\n"
                f"  Details: {details}"
            )
        )
