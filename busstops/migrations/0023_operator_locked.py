from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('busstops', '0022_remove_service_idx_service_current_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='operator',
            name='locked',
            field=models.BooleanField(default=False),
        ),
    ]
