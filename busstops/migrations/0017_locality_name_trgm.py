import django.contrib.postgres.indexes
from django.contrib.postgres.operations import TrigramExtension
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("busstops", "0016_operator_timezone"),
    ]

    operations = [
        TrigramExtension(),
        migrations.AddIndex(
            model_name="locality",
            index=django.contrib.postgres.indexes.GinIndex(
                fields=["name"],
                opclasses=["gin_trgm_ops"],
                name="locality_name_trgm",
            ),
        ),
    ]
