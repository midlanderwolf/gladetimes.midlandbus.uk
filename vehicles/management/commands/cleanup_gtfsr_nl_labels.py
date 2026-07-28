from django.core.management.base import BaseCommand
from django.db.models import Q
from ...models import Vehicle, VehicleCode


class Command(BaseCommand):
    help = "Clean up vehicles with LABEL-/label- prefix codes from OVAPI feed, converting to OV prefix"

    def handle(self, *args, **options):
        label_vehicles = Vehicle.objects.filter(
            Q(code__startswith="LABEL-") | Q(code__startswith="label-")
        )
        count = label_vehicles.count()
        self.stdout.write(f"Found {count} vehicles with LABEL-/label- prefix")

        if count == 0:
            self.stdout.write(self.style.SUCCESS("No cleanup needed"))
            return

        updated_vehicles = 0
        updated_codes = 0
        deleted_vehicles = 0

        for vehicle in label_vehicles:
            label_part = vehicle.code[6:]

            if not label_part:
                self.stdout.write(f"  Skipping empty code for vehicle {vehicle.id}")
                continue

            new_code = f"OV{label_part}"

            existing = Vehicle.objects.filter(code=new_code, source=vehicle.source).exclude(id=vehicle.id).first()
            if existing:
                self.stdout.write(
                    f"  Vehicle {vehicle.id} ({vehicle.code}) conflicts with {existing.id} ({new_code}), deleting"
                )
                VehicleCode.objects.filter(vehicle=vehicle).update(vehicle=existing)
                vehicle.delete()
                deleted_vehicles += 1
                continue

            self.stdout.write(f"  Updating vehicle {vehicle.id}: {vehicle.code} -> {new_code}")
            vehicle.code = new_code
            vehicle.fleet_code = new_code[:24]
            vehicle.save(update_fields=["code", "fleet_code"])
            updated_vehicles += 1

        label_codes = VehicleCode.objects.filter(
            Q(code__startswith="LABEL-") | Q(code__startswith="label-"),
            scheme="OVAPI"
        )
        code_count = label_codes.count()
        self.stdout.write(f"\nFound {code_count} VehicleCode entries with LABEL-/label- prefix")

        for vc in label_codes:
            label_part = vc.code[6:]
            if not label_part:
                continue

            new_code = f"OV{label_part}"

            existing = VehicleCode.objects.filter(code=new_code, scheme="OVAPI").exclude(id=vc.id).first()
            if existing:
                self.stdout.write(f"  Deleting duplicate VehicleCode {vc.id}")
                vc.delete()
                continue

            self.stdout.write(f"  Updating VehicleCode {vc.id}: {vc.code} -> {new_code}")
            vc.code = new_code
            vc.save(update_fields=["code"])
            updated_codes += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"\nCleanup complete:\n"
                f"  Updated {updated_vehicles} vehicles\n"
                f"  Updated {updated_codes} vehicle codes\n"
                f"  Deleted {deleted_vehicles} duplicate vehicles"
            )
        )
