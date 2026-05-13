from django.core.management.base import BaseCommand

from ...nctx import nctx_disruptions


class Command(BaseCommand):
    def handle(self, *args, **options):
        nctx_disruptions()
