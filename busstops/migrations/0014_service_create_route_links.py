from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("busstops", "0013_stoppoint_description_stoppoint_notes_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="service",
            name="create_route_links",
            field=models.BooleanField(default=False),
        ),
    ]
