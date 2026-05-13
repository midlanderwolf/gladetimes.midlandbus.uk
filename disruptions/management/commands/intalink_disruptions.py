from django.core.management.base import BaseCommand

from ...intalink import intalink_disruptions


class Command(BaseCommand):
    def handle(self, *args, **options):
        intalink_disruptions()
