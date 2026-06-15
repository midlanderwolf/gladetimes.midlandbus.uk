from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("busstops", "0016_operator_timezone"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="stopusage",
            index=models.Index(fields=["stop_id"], name="idx_stopusage_stop_id"),
        ),
        migrations.AddIndex(
            model_name="stopusage",
            index=models.Index(fields=["service_id"], name="idx_stopusage_service_id"),
        ),
        migrations.AddIndex(
            model_name="service",
            index=models.Index(fields=["current"], name="idx_service_current"),
        ),
        migrations.RunSQL(
            "CREATE INDEX IF NOT EXISTS idx_stoppoint_latlong ON busstops_stoppoint USING GIST (latlong);",
            reverse_sql="DROP INDEX IF EXISTS idx_stoppoint_latlong;",
        ),
    ]
