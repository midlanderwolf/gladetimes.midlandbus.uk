import re

from lxml import etree
from django.contrib.gis.geos import Point
from django.utils import timezone

from busstops.models import DataSource, Operator

from ...models import VehicleJourney, VehicleLocation
from ..import_live_vehicles import ImportLiveVehiclesCommand

KML_NS = "http://www.opengis.net/kml/2.2"
NSMAP = {"kml": KML_NS}

BASE_URL = "https://metro-rti.nexus.org.uk/api/geo"


class Command(ImportLiveVehiclesCommand):
    source_name = vehicle_code_scheme = "Tyne and Wear Metro"
    wait = 60

    def do_source(self):
        self.operator_obj, _ = Operator.objects.get_or_create(
            noc="NXMT",
            defaults={"name": "Tyne and Wear Metro", "slug": "nexus"},
        )
        d = timezone.localtime().strftime("%Y%m%d%H%M")
        self.url = f"{BASE_URL}/trainstatuses.kml?d={d}"
        self.directions_url = f"{BASE_URL}/traindirections.kml?d={d}"
        self.source, _ = DataSource.objects.get_or_create(
            name=self.source_name, defaults={"url": self.url}
        )
        return self

    def fetch_kml(self, url):
        response = self.session.get(url, timeout=20)
        response.raise_for_status()
        return etree.fromstring(response.content)

    def parse_directions(self, root):
        headings = {}
        for placemark in root.findall(".//kml:Placemark", NSMAP):
            train_id = placemark.get("id")
            rotation = placemark.findtext("kml:Rotation", namespaces=NSMAP)
            if train_id and rotation:
                headings[train_id] = round(float(rotation))
        return headings

    def get_items(self):
        statuses_root = self.fetch_kml(self.url)
        directions_root = self.fetch_kml(self.directions_url)
        headings = self.parse_directions(directions_root)

        items = []
        for placemark in statuses_root.findall(".//kml:Placemark", NSMAP):
            train_id = placemark.get("id")
            if not train_id:
                continue

            coords_text = placemark.findtext(
                ".//kml:Point/kml:coordinates", namespaces=NSMAP
            )
            if not coords_text:
                continue

            coords = coords_text.strip().split(",")
            lon, lat = float(coords[0]), float(coords[1])

            details_text = placemark.findtext(
                ".//kml:ExtendedData/kml:Data[@name='details']/kml:value",
                namespaces=NSMAP,
            )
            destination = ""
            if details_text:
                dest_match = re.search(
                    r'data-title="Destination">([^<]+)<', details_text
                )
                if dest_match:
                    destination = dest_match.group(1).strip()

            items.append(
                {
                    "id": train_id,
                    "lon": lon,
                    "lat": lat,
                    "heading": headings.get(train_id),
                    "destination": destination,
                }
            )

        return items

    @staticmethod
    def get_datetime(item):
        return None

    @staticmethod
    def get_vehicle_identity(item):
        return item["id"]

    @staticmethod
    def get_journey_identity(item):
        return item["destination"]

    @staticmethod
    def get_item_identity(item):
        return (item["id"], item["heading"])

    def get_vehicle(self, item):
        return self.vehicles.get_or_create(
            {"operator_id": self.operator_obj.pk},
            source=self.source,
            code=item["id"],
        )

    def get_journey(self, item, _):
        journey = VehicleJourney()
        journey.destination = item["destination"]
        journey.route_name = item["destination"]
        journey.datetime = self.source.datetime
        return journey

    def create_vehicle_location(self, item):
        return VehicleLocation(
            latlong=Point(item["lon"], item["lat"]),
            heading=item["heading"],
        )
