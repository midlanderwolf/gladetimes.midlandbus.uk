from django.core.management.base import BaseCommand
from django.db import connection
from bustimes.models import StopTime
from busstops.models import DataSource, StopPoint


class Command(BaseCommand):
    help = "Delete all StopPoints added via Traveline (TNDS)"

    def handle(self, *args, **options):
        tnds_codes = ["Y", "SW", "SE", "EM", "EA", "WM", "S", "NE", "NW", "W", "IM"]

        sources = DataSource.objects.filter(name__in=tnds_codes)
        if not sources.exists():
            self.stdout.write(self.style.WARNING("No TNDS DataSource objects found"))
            return

        self.stdout.write("TNDS DataSources found:")
        for source in sources:
            count = StopPoint.objects.filter(source=source).count()
            self.stdout.write(f"  {source.name} (id={source.id}): {count} stoppoints")

        total = StopPoint.objects.filter(source__name__in=tnds_codes).count()
        self.stdout.write(f"\nTotal stoppoints to delete: {total}")

        if total == 0:
            self.stdout.write(self.style.SUCCESS("No stoppoints to delete"))
            return

        self.stdout.write("\nSetting StopTime.stop to NULL for TNDS stops using raw SQL...")
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE bustimes_stoptime st
                SET stop_id = NULL
                FROM busstops_stoppoint sp
                JOIN busstops_datasource ds ON sp.source_id = ds.id
                WHERE st.stop_id = sp.atco_code
                AND ds.name IN %s
                """,
                [tuple(tnds_codes)]
            )
            stoptime_count = cursor.rowcount
        self.stdout.write(f"  Updated {stoptime_count} StopTime records")

        self.stdout.write("Deleting TNDS StopPoints...")
        deleted_count, _ = StopPoint.objects.filter(
            source__name__in=tnds_codes
        ).delete()
        self.stdout.write(
            self.style.SUCCESS(f"Successfully deleted {deleted_count} stoppoints")
        )
