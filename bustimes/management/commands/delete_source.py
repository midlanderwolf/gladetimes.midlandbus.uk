from django.core.management.base import BaseCommand
from django.db import transaction
from busstops.models import DataSource, StopPoint, Service
from bustimes.models import Route, Trip, StopTime, RouteLink


class Command(BaseCommand):
    help = 'Delete a data source and all associated data'

    def add_arguments(self, parser):
        parser.add_argument('source_name', type=str, nargs='?', help='Name of the DataSource to delete')
        parser.add_argument('--id', type=int, help='ID of the DataSource to delete')

    def handle(self, *args, **options):
        source_name = options['source_name']
        source_id = options['id']

        try:
            if source_id:
                source = DataSource.objects.get(id=source_id)
            elif source_name:
                source = DataSource.objects.get(name=source_name)
            else:
                self.stdout.write(self.style.ERROR('Must provide either source_name or --id'))
                return
        except DataSource.DoesNotExist:
            identifier = source_id if source_id else source_name
            self.stdout.write(self.style.ERROR(f'DataSource "{identifier}" not found'))
            return

        self.stdout.write(f'Deleting data for source "{source_name}" (ID: {source.id})')

        with transaction.atomic():
            # Delete in order to avoid foreign key issues
            # StopTimes first
            stop_times_count = StopTime.objects.filter(trip__route__source=source).delete()[0]
            self.stdout.write(f'Deleted {stop_times_count} StopTimes')

            # Nullify destination references to stops from this source before deleting trips
            trips_with_destination = Trip.objects.filter(destination__source=source)
            trips_with_destination.update(destination=None)
            self.stdout.write(f'Nullified destination for {trips_with_destination.count()} Trips')

            # Trips
            trips_count = Trip.objects.filter(route__source=source).delete()[0]
            self.stdout.write(f'Deleted {trips_count} Trips')

            # Routes
            routes_count = Route.objects.filter(source=source).delete()[0]
            self.stdout.write(f'Deleted {routes_count} Routes')

            # Services (may be shared, but delete those with this source)
            services_count = Service.objects.filter(source=source).delete()[0]
            self.stdout.write(f'Deleted {services_count} Services')

            # StopPoints (may be shared, delete only those with this source)
            stops_count = StopPoint.objects.filter(source=source).delete()[0]
            self.stdout.write(f'Deleted {stops_count} StopPoints')

            # RouteLinks
            route_links_count = RouteLink.objects.filter(service__source=source).delete()[0]
            self.stdout.write(f'Deleted {route_links_count} RouteLinks')

            # Finally, the DataSource
            source.delete()
            self.stdout.write(self.style.SUCCESS(f'Successfully deleted DataSource "{source_name}" and all associated data'))