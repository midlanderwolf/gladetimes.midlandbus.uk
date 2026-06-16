from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("busstops", "0014_service_create_route_links"),
    ]

    operations = [
        migrations.RunSQL(
            sql="ALTER TABLE busstops_operator DROP COLUMN IF EXISTS timezone;",
        ),
    ]
