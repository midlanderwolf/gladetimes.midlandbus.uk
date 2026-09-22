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
        f" REFERENCES {references} %s DEFERRABLE INITIALLY DEFERRED NOT VALID"
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
        ("bustimes", "0021_alter_bankholidaydate_bank_holiday_and_more"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AlterField(
                    model_name="stoptime",
                    name="trip",
                    field=models.ForeignKey(
                        db_index=False,
                        on_delete=django.db.models.deletion.DB_CASCADE,
                        to="bustimes.trip",
                    ),
                ),
                migrations.AlterField(
                    model_name="trip",
                    name="calendar",
                    field=models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.DB_CASCADE,
                        to="bustimes.calendar",
                    ),
                ),
                migrations.AlterField(
                    model_name="trip",
                    name="garage",
                    field=models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.DB_SET_NULL,
                        to="bustimes.garage",
                    ),
                ),
                migrations.AlterField(
                    model_name="trip",
                    name="next_trip",
                    field=models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.DB_SET_NULL,
                        to="bustimes.trip",
                    ),
                ),
                migrations.AlterField(
                    model_name="trip",
                    name="operator",
                    field=models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.DB_SET_NULL,
                        to="busstops.operator",
                    ),
                ),
                migrations.AlterField(
                    model_name="trip",
                    name="route",
                    field=models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.DB_CASCADE,
                        to="bustimes.route",
                    ),
                ),
                migrations.AlterField(
                    model_name="trip",
                    name="vehicle_type",
                    field=models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.DB_SET_NULL,
                        to="bustimes.vehicletype",
                    ),
                ),
            ],
            database_operations=[
                alter_fk(
                    "bustimes_stoptime",
                    "trip_id",
                    "bustimes_stoptime_trip_id_0be81724_fk_bustimes_trip_id",
                    '"bustimes_trip" ("id")',
                    "ON DELETE CASCADE",
                ),
                alter_fk(
                    "bustimes_trip",
                    "route_id",
                    "bustimes_trip_route_id_84041d75_fk_bustimes_route_id",
                    '"bustimes_route" ("id")',
                    "ON DELETE CASCADE",
                ),
                alter_fk(
                    "bustimes_trip",
                    "calendar_id",
                    "bustimes_trip_calendar_id_f0cc19bc_fk_bustimes_calendar_id",
                    '"bustimes_calendar" ("id")',
                    "ON DELETE CASCADE",
                ),
                alter_fk(
                    "bustimes_trip",
                    "garage_id",
                    "bustimes_trip_garage_id_1ba92bdd_fk_bustimes_garage_id",
                    '"bustimes_garage" ("id")',
                    "ON DELETE SET NULL",
                ),
                alter_fk(
                    "bustimes_trip",
                    "next_trip_id",
                    "bustimes_trip_next_trip_id_18c64f28_fk_bustimes_trip_id",
                    '"bustimes_trip" ("id")',
                    "ON DELETE SET NULL",
                ),
                alter_fk(
                    "bustimes_trip",
                    "operator_id",
                    "bustimes_trip_operator_id_192aec42_fk_busstops_operator_noc",
                    '"busstops_operator" ("noc")',
                    "ON DELETE SET NULL",
                ),
                alter_fk(
                    "bustimes_trip",
                    "vehicle_type_id",
                    "bustimes_trip_vehicle_type_id_8cc46351_fk_bustimes_",
                    '"bustimes_vehicletype" ("id")',
                    "ON DELETE SET NULL",
                ),
            ],
        ),
    ]
