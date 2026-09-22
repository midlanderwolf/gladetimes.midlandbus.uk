from django.db import migrations


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("bustimes", "0019_remove_stoptime_trip_index"),
    ]

    operations = [
        migrations.RunSQL(
            "SET lock_timeout = '2s'", reverse_sql=migrations.RunSQL.noop
        ),
        # ANALYZE only samples 30,000 of 130 million rows, and stop times for a trip
        # are all in the same few blocks, so it reckons there are 20,000 distinct
        # trips when there are 3 million. -0.025 = 40 stop times per trip
        migrations.RunSQL(
            'ALTER TABLE "bustimes_stoptime" ALTER COLUMN "trip_id" SET (n_distinct = -0.025);',
            reverse_sql='ALTER TABLE "bustimes_stoptime" ALTER COLUMN "trip_id" RESET (n_distinct);'
            'ANALYZE "bustimes_stoptime";',
        ),
        migrations.RunSQL("RESET lock_timeout", reverse_sql=migrations.RunSQL.noop),
        # the override is only applied at the next ANALYZE
        migrations.RunSQL(
            'ANALYZE "bustimes_stoptime";', reverse_sql=migrations.RunSQL.noop
        ),
    ]
