from datetime import datetime, timedelta

from django.core.management.base import BaseCommand
from django.db.models import Q
from sql_util.utils import Exists

from bustimes.models import Trip

from ...models import Vehicle


class Command(BaseCommand):
    @staticmethod
    def add_arguments(parser):
        parser.add_argument("operator_code", type=str)
        parser.add_argument("start_date", type=str)
        parser.add_argument("days", type=int, default=7)

    def handle(self, operator_code, start_date, days, **options):
        vehicles = Vehicle.objects.filter(operator=operator_code)

        block_number = 1

        Trip.objects.filter(operator=operator_code).update(block="")

        start = datetime.strptime(start_date, "%Y-%m-%d").date()
        dates = [start + timedelta(days=i) for i in range(days)]

        print(f"Processing {days} days from {start}")

        for date in dates[1:]:
            previous_dates = [d for d in dates if d < date]

            current_journeys = Vehicle.objects.filter(
                Exists("vehiclejourney", filter=Q(datetime__date=date)),
                operator=operator_code,
            )

            print(f"{date}: {current_journeys.count()} vehicles")

            for vehicle in current_journeys:
                journeys = vehicle.vehiclejourney_set.filter(date=date)
                trip_ids = tuple(sorted(j.trip_id for j in journeys if j.trip_id))
                if not trip_ids:
                    continue

                matched = False
                for previous_date in previous_dates:
                    previous_day_journeys = Vehicle.objects.filter(
                        Exists(
                            "vehiclejourney",
                            filter=Q(datetime__date=previous_date, trip__in=trip_ids),
                        ),
                        operator=operator_code,
                    )

                    for previous_vehicle in previous_day_journeys:
                        prev_journeys = previous_vehicle.vehiclejourney_set.filter(
                            date=previous_date
                        )
                        previous_trip_ids = tuple(
                            sorted(j.trip_id for j in prev_journeys if j.trip_id)
                        )

                        if trip_ids == previous_trip_ids:
                            trips = Trip.objects.filter(id__in=trip_ids)
                            routes = ", ".join(
                                t.route.line_name for t in trips if t.route
                            )
                            print(
                                f"  Block {block_number}: {len(trip_ids)} trips, "
                                f"routes: {routes}"
                            )
                            trips.update(block=block_number)
                            block_number += 1
                            matched = True
                            break

                    if matched:
                        break

                if not matched:
                    trips = Trip.objects.filter(id__in=trip_ids)
                    routes = ", ".join(t.route.line_name for t in trips[:3] if t.route)
                    print(f"  No match: {len(trip_ids)} trips, routes: {routes}...")

        print(f"Total blocks created: {block_number - 1}")
