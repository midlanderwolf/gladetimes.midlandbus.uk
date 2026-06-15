from django.core.management.base import BaseCommand

from ...mcgills import mcgills_disruptions


class Command(BaseCommand):
    def handle(self, *args, **options):
        mcgills_disruptions()
