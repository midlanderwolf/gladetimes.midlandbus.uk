from django.core.management.base import BaseCommand
from ...models import Vehicle, VehicleJourney
from busstops.models import DataSource


class Command(BaseCommand):
    help = "Remove OVAPI journeys from vehicles that aren't from OVAPI source"

    def handle(self, *args, **options):
        try:
            ovapi_source = DataSource.objects.get(name="OVAPI")
        except DataSource.DoesNotExist:
            self.stdout.write(self.style.WARNING("OVAPI source not found"))
            return

        journeys = VehicleJourney.objects.filter(
            source=ovapi_source
        ).exclude(
            vehicle__source=ovapi_source
        ).select_related("vehicle")

        count = journeys.count()
        self.stdout.write(f"Found {count} OVAPI journeys on non-OVAPI vehicles")

        if count == 0:
            self.stdout.write(self.style.SUCCESS("No cleanup needed"))
            return

        vehicles_to_update = set()

        for journey in journeys:
            if journey.vehicle and journey.vehicle.latest_journey_id == journey.id:
                vehicles_to_update.add(journey.vehicle)
                self.stdout.write(
                    f"  Journey {journey.id} is latest_journey for vehicle {journey.vehicle.id}"
                )

        deleted_count, _ = journeys.delete()
        self.stdout.write(f"Deleted {deleted_count} journeys")

        for vehicle in vehicles_to_update:
            vehicle.latest_journey = VehicleJourney.objects.filter(
                vehicle=vehicle
            ).order_by("-datetime").first()
            vehicle.save(update_fields=["latest_journey"])
            self.stdout.write(
                f"  Updated vehicle {vehicle.id} latest_journey to {vehicle.latest_journey_id}"
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"\nCleanup complete:\n"
                f"  Deleted {deleted_count} journeys\n"
                f"  Updated {len(vehicles_to_update)} vehicles' latest_journey"
            )
        )
