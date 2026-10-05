import django.db.models.deletion
from django.db import migrations, models


def alter_fk(table, column, constraint, references, action):
    """Swap a foreign key's referential action without a validating lock.

    Adding a foreign key normally scans the whole referencing table while
    holding a lock that blocks writes. NOT VALID skips the scan, and the
    separate VALIDATE only takes a SHARE UPDATE EXCLUSIVE lock.
    """
    add = (
        f'ALTER TABLE "{table}" ADD CONSTRAINT "{constraint}" FOREIGN KEY ("{column}")'
        f' REFERENCES {references} %s DEFERRABLE INITIALLY DEFERRED NOT VALID'
    )
    drop = f'ALTER TABLE "{table}" DROP CONSTRAINT IF EXISTS "{constraint}"'
    validate = f'ALTER TABLE "{table}" VALIDATE CONSTRAINT "{constraint}"'
    return migrations.RunSQL(
        [drop, add % action, validate],
        [drop, add % "", validate],
    )


class Migration(migrations.Migration):
    # not atomic, so that each VALIDATE commits on its own
    atomic = False

    dependencies = [
        ('bustimes', '0022_db_on_delete_trip_stoptime'),
        ('vehicles', '0025_alter_sirisubscription_source_alter_vehicle_garage_and_more'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
            migrations.AlterField(
                model_name='vehiclejourney',
                name='service',
                field=models.ForeignKey(blank=True, db_index=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, to='busstops.service'),
            ),
            migrations.AlterField(
                model_name='vehiclejourney',
                name='source',
                field=models.ForeignKey(on_delete=django.db.models.deletion.DB_CASCADE, to='busstops.datasource'),
            ),
            migrations.AlterField(
                model_name='vehiclejourney',
                name='trip',
                field=models.ForeignKey(blank=True, db_index=False, null=True, on_delete=django.db.models.deletion.DB_SET_NULL, to='bustimes.trip'),
            ),
            migrations.AlterField(
                model_name='vehiclejourney',
                name='vehicle',
                field=models.ForeignKey(blank=True, db_index=False, null=True, on_delete=django.db.models.deletion.DB_CASCADE, to='vehicles.vehicle'),
            ),
            ],
            database_operations=[
                alter_fk(
                    "vehicles_vehiclejourney",
                    "trip_id",
                    "vehicles_vehiclejourney_trip_id_f6cf407e_fk_bustimes_trip_id",
                    '"bustimes_trip" ("id")',
                    "ON DELETE SET NULL",
                ),
                alter_fk(
                    "vehicles_vehiclejourney",
                    "source_id",
                    "vehicles_vehiclejour_source_id_9c99347e_fk_busstops_",
                    '"busstops_datasource" ("id")',
                    "ON DELETE CASCADE",
                ),
                alter_fk(
                    "vehicles_vehiclejourney",
                    "vehicle_id",
                    "vehicles_vehiclejour_vehicle_id_41154c8a_fk_vehicles_",
                    '"vehicles_vehicle" ("id")',
                    "ON DELETE CASCADE",
                ),
                alter_fk(
                    "vehicles_vehiclejourney",
                    "service_id",
                    "vehicles_vehiclejour_service_id_b5ad5fe9_fk_busstops_",
                    '"busstops_service" ("id")',
                    "",
                ),
            ],
        ),
    ]
