from django.core.management.base import BaseCommand

from busstops.models import Service
from bustimes.utils import generate_route_links_for_service_valhalla


class Command(BaseCommand):
    help = "Generate route links for services using Valhalla routing engine"

    def add_arguments(self, parser):
        parser.add_argument(
            "service_ids",
            nargs="*",
            type=int,
            help="Service IDs to generate route links for",
        )
        parser.add_argument(
            "--url",
            default="https://valhalla.midlandbus.uk",
            help="Valhalla server URL (default: https://valhalla.midlandbus.uk)",
        )
        parser.add_argument(
            "--costing",
            default="bus",
            help="Valhalla costing model (default: bus)",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            help="Generate route links for all services with create_route_links=True",
        )

    def handle(self, *args, **options):
        service_ids = options["service_ids"]
        url = options["url"].rstrip("/")
        costing = options["costing"]
        all_flag = options["all"]

        if not service_ids and not all_flag:
            self.stderr.write(self.style.ERROR("Provide service IDs or use --all"))
            return

        if all_flag:
            services = Service.objects.filter(create_route_links=True, current=True)
        else:
            services = Service.objects.filter(id__in=service_ids)

        if not services.exists():
            self.stderr.write(self.style.ERROR("No services found"))
            return

        self.stdout.write(f"Generating route links for {services.count()} service(s) using Valhalla ({costing} costing)")

        success = 0
        failed = 0

        for service in services:
            self.stdout.write(f"  Service {service.id}: {service}", ending="")
            try:
                result = generate_route_links_for_service_valhalla(
                    service, url=url, costing=costing
                )
                if result:
                    self.stdout.write(self.style.SUCCESS(" OK"))
                    success += 1
                else:
                    self.stdout.write(self.style.WARNING(" no route links generated"))
                    failed += 1
            except Exception as e:
                self.stdout.write(self.style.ERROR(f" ERROR: {e}"))
                failed += 1

        self.stdout.write(
            self.style.SUCCESS(f"\nDone: {success} succeeded, {failed} failed")
        )
