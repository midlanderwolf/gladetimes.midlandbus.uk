from django.core.management.base import BaseCommand
from ...models import Vehicle, VehicleCode


class Command(BaseCommand):
    help = "Recover OVAPI vehicles that were incorrectly merged with other sources"

    def handle(self, *args, **options):
        ovapi_codes = VehicleCode.objects.filter(scheme="OVAPI").select_related("vehicle")
        self.stdout.write(f"Found {ovapi_codes.count()} OVAPI VehicleCodes")

        recovered = 0
        already_correct = 0

        for vc in ovapi_codes:
            if vc.vehicle.source and vc.vehicle.source.name == "OVAPI":
                already_correct += 1
                continue

            self.stdout.write(
                f"  VehicleCode {vc.code} points to vehicle {vc.vehicle.id} "
                f"with source={vc.vehicle.source}, needs recovery"
            )

            new_code = vc.code
            if new_code.startswith("LABEL-"):
                new_code = new_code[6:]

            existing = Vehicle.objects.filter(code=new_code, source__name="OVAPI").first()
            if existing:
                self.stdout.write(f"    Reassigning to existing OVAPI vehicle {existing.id}")
                vc.vehicle = existing
                vc.save(update_fields=["vehicle"])
            else:
                self.stdout.write(f"    Creating new OVAPI vehicle with code={new_code}")
                from busstops.models import DataSource
                source = DataSource.objects.get(name="OVAPI")
                defaults = {"fleet_code": new_code[:24]}
                if new_code.isdigit():
                    defaults["fleet_number"] = int(new_code)
                new_vehicle = Vehicle.objects.create(
                    code=new_code,
                    source=source,
                    **defaults,
                )
                vc.vehicle = new_vehicle
                vc.save(update_fields=["vehicle"])
            recovered += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"\nRecovery complete:\n"
                f"  Already correct: {already_correct}\n"
                f"  Recovered: {recovered}"
            )
        )
