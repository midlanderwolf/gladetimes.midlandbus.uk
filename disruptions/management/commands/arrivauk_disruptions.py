from django.core.management.base import BaseCommand

from ...arrivauk import arrivauk_disruptions


class Command(BaseCommand):
    def handle(self, *args, **options):
        arrivauk_disruptions()
