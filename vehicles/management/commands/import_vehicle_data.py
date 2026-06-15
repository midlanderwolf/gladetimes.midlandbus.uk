import requests
from django.core.management.base import BaseCommand
from django.db import transaction
from busstops.models import Operator
from bustimes.models import Garage
from ...models import VehicleType, Livery, VehicleFeature, Vehicle


class Command(BaseCommand):
    help = 'Import vehicle types and liveries from bustimes.org API'

    def handle(self, *args, **options):
        self.import_vehicle_types()
        self.import_liveries()
        self.stdout.write(self.style.SUCCESS('Successfully imported vehicle types and liveries'))

    def import_vehicle_types(self):
        """Import vehicle types from the API"""
        url = "https://bustimes.org/api/vehicletypes/?format=json&limit=9999"
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()

        created_count = 0
        updated_count = 0

        for vehicle_type_data in data['results']:
            vehicle_type, created = VehicleType.objects.update_or_create(
                id=vehicle_type_data['id'],
                defaults={
                    'name': vehicle_type_data['name'],
                    'style': vehicle_type_data['style'],
                    'fuel': vehicle_type_data['fuel'],
                }
            )
            if created:
                created_count += 1
            else:
                updated_count += 1

        self.stdout.write(
            f'Vehicle types: {created_count} created, {updated_count} updated'
        )

    def import_liveries(self):
        """Import liveries from the API"""
        url = "https://bustimes.org/api/liveries/?format=json"
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()

        created_count = 0
        updated_count = 0

        # Handle pagination
        while url:
            response = requests.get(url)
            response.raise_for_status()
            data = response.json()

            for livery_data in data['results']:
                livery, created = Livery.objects.update_or_create(
                    id=livery_data['id'],
                    defaults={
                        'name': livery_data['name'],
                        'left_css': livery_data['left_css'],
                        'right_css': livery_data['right_css'],
                        'white_text': livery_data['white_text'],
                        'text_colour': livery_data['text_colour'],
                        'stroke_colour': livery_data['stroke_colour'],
                        'published': True,  # Mark as published since it's from the API
                    }
                )
                if created:
                    created_count += 1
                else:
                    updated_count += 1

            url = data.get('next')

        self.stdout.write(
            f'Liveries: {created_count} created, {updated_count} updated'
        )