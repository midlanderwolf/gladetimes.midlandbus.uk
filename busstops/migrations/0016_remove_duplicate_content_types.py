from django.db import migrations


def remove_duplicate_content_types(apps, schema_editor):
    ContentType = apps.get_model("contenttypes", "ContentType")
    seen = {}
    for ct in ContentType.objects.order_by("id").iterator():
        key = (ct.app_label, ct.model)
        if key in seen:
            schema_editor.execute(f"DELETE FROM django_content_type WHERE id={ct.id}")
        else:
            seen[key] = ct


class Migration(migrations.Migration):
    dependencies = [
        ("busstops", "0014_service_create_route_links"),
        ("contenttypes", "__latest__"),
    ]

    operations = [
        migrations.RunPython(remove_duplicate_content_types, migrations.RunPython.noop),
    ]
