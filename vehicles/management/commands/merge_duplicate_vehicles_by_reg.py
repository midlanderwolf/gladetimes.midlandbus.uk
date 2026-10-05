from django.core.management.base import BaseCommand

from busstops.models import Operator
from .import_vehicles import merge_duplicate_vehicles_by_reg


class Command(BaseCommand):
    help = (
        "Merge vehicles that share a registration across different operators "
        "(e.g. one reallocated between two otherwise-unrelated companies, "
        "leaving a stale duplicate behind under its old operator)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "nocs",
            nargs="*",
            help="Only consider vehicles belonging to these operators "
            "(by NOC). If omitted, considers every operator.",
        )

    def handle(self, *args, **options):
        operators = None
        if options["nocs"]:
            operators = Operator.objects.filter(noc__in=options["nocs"])

        merged = merge_duplicate_vehicles_by_reg(operators)

        self.stdout.write(f"Merged {merged} duplicate vehicle(s)")
