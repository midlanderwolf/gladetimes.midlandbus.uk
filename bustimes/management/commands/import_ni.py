import pprint
from datetime import UTC, datetime
from pathlib import Path

import requests
from django.conf import settings
from django.core.management.base import BaseCommand

from busstops.models import DataSource

from ...download_utils import download
from .import_bod_timetables import get_command, handle_file


class Command(BaseCommand):
    """
    Check the Open Data NI (Northern Ireland) website CKAN API for any new
    Translink Metro and Ulsterbus data,
    and download and import it if necessary
    """

    def handle(self, *args, **options):
        base_url = "https://admin.opendatani.gov.uk/api/3/action/package_show"
        ids = [
            "ulsterbus-and-goldline-timetable-data-from-08-11-2023",
            "metro-timetable-data-valid-from-18-june-until-31-august-2016",
        ]

        for _id in ids:
            response = requests.get(base_url, params={"id": _id})
            data = response.json()

            source = DataSource.objects.get(url__endswith=_id)

            for resource in data["result"]["resources"]:
                dt = resource["last_modified"] or resource["created"]
                dt = datetime.fromisoformat(dt).replace(tzinfo=UTC)

                if source.datetime is None or dt > source.datetime:
                    # new data, lorks a lordy!

                    pprint.pprint(resource)

                    url = resource["url"]
                    path = Path(settings.DATA_DIR) / f"{source.name}"
                    download(path, url)

                    command = get_command()
                    command.source = source
                    command.source.datetime = dt
                    command.region_id = "NI"
                    command.service_ids = set()
                    command.route_ids = set()

                    handle_file(command, path)

                    command.mark_old_services_as_not_current()

                    command.finish_services()

                    command.source.save()
