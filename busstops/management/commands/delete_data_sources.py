from django.core.management.base import BaseCommand
from django.db import transaction
from busstops.models import DataSource, StopPoint, Service, Route, Operator, OperatorCode


class Command(BaseCommand):
    help = "Delete DataSource entries by name"

    def add_arguments(self, parser):
        parser.add_argument("names", nargs="+", type=str, help="DataSource names to delete")
        parser.add_argument("--dry-run", action="store_true", help="Show what would be deleted without actually deleting")

    def handle(self, *args, **options):
        names = options["names"]
        dry_run = options["dry_run"]

        sources = DataSource.objects.filter(name__in=names)

        if not sources.exists():
            self.stdout.write(self.style.WARNING(f"No DataSources found with names: {', '.join(names)}"))
            return

        self.stdout.write(f"Found {sources.count()} DataSource(s) to delete:")
        for source in sources:
            self.stdout.write(f"  - {source.name} (ID: {source.id})")

            related = {
                "StopPoints": source.stoppoint_set.count(),
                "Services": source.service_set.count(),
                "Routes": source.route_set.count(),
                "Operators": source.operator_set.count(),
                "OperatorCodes": source.operatorcode_set.count(),
            }

            for model_name, count in related.items():
                if count > 0:
                    self.stdout.write(f"    {model_name}: {count}")

        if dry_run:
            self.stdout.write(self.style.WARNING("\nDry run - no changes made"))
        else:
            self.stdout.write(self.style.WARNING("\nDeleting..."))
            with transaction.atomic():
                for source in sources:
                    source.stoppoint_set.all().delete()
                    source.service_set.all().delete()
                    source.route_set.all().delete()
                    source.operatorcode_set.all().delete()
                    source.operator_set.all().delete()
                    source.delete()
            self.stdout.write(self.style.SUCCESS("Done"))
