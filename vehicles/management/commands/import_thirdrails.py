import logging
import re
import json
from time import sleep

import requests
from django.contrib.gis.geos import Point
from django.core.management.base import BaseCommand
from django.utils import timezone

from busstops.models import Operator, Region, DataSource

from vehicles.models import Vehicle, VehicleJourney

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Import vehicle locations from ThirdRails API"

    source_name = "ThirdRails"
    url = "https://www.rentor.nl/api/radar/GetRadarData"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "bustimes.org ThirdRails importer"})
        self.source = None

    def add_arguments(self, parser):
        parser.add_argument(
            "--continuous",
            action="store_true",
            help="Run continuously, polling the API every 60 seconds",
        )
        parser.add_argument(
            "--interval",
            type=int,
            default=60,
            help="Polling interval in seconds (default: 60)",
        )

    def get_or_create_source(self):
        """Get or create the data source"""
        self.source, created = DataSource.objects.get_or_create(
            {"name": self.source_name, "url": self.url}, name=self.source_name
        )
        if created:
            self.stdout.write(f"Created new data source: {self.source_name}")
        return self.source

    def get_or_create_operator(self, simulator):
        """Get or create TSW or TSC operator"""
        if simulator == "TSW":
            operator_name = "Train Sim World"
            operator_slug = "train-sim-world"
        elif simulator == "TSC":
            operator_name = "Train Sim Classic"
            operator_slug = "train-sim-classic"
        else:
            # Default fallback
            operator_name = simulator
            operator_slug = simulator.lower().replace(" ", "-")

        # Get GB region
        try:
            gb_region = Region.objects.get(pk="GB")
        except Region.DoesNotExist:
            self.stdout.write(self.style.WARNING("GB region not found, using None"))
            gb_region = None

        operator, created = Operator.objects.get_or_create(
            name=operator_name,
            defaults={
                "slug": operator_slug,
                "region": gb_region,
                "vehicle_mode": "train",  # These are train simulators
            },
        )

        if created:
            self.stdout.write(f"Created new operator: {operator_name}")

        return operator

    def parse_coordinates(self, points_str):
        """Parse coordinate string into Point object"""
        if not points_str:
            return None

        try:
            # ThirdRails appears to use longitude,latitude format
            coords = points_str.split(",")
            if len(coords) != 2:
                return None

            lng = float(coords[0].strip())
            lat = float(coords[1].strip())

            # Validate coordinate ranges
            if not (-180 <= lng <= 180 and -90 <= lat <= 90):
                return None

            return Point(lng, lat)
        except (ValueError, IndexError):
            self.stdout.write(self.style.WARNING(f"Invalid coordinates: {points_str}"))
            return None

    def parse_speed(self, speed_text):
        """Parse speed from text like '103 km/h'"""
        if not speed_text:
            return 0

        speed_match = re.search(r"(\d+)", speed_text)
        return int(speed_match.group(1)) if speed_match else 0

    def get_or_create_vehicle(self, item):
        """Get or create vehicle from ThirdRails data"""
        unique_name = item.get("UniqueName", "")
        if not unique_name:
            return None

        # Try to find existing vehicle by data field
        vehicle = Vehicle.objects.filter(data__thirdrails_id=unique_name).first()

        if vehicle:
            return vehicle

        # Get operator based on simulator
        simulator = item.get("Simulator", "")
        operator = self.get_or_create_operator(simulator)

        # Extract vehicle info
        loco = item.get("Loco", "")
        name = item.get("Name", "")

        # Create new vehicle
        vehicle = Vehicle.objects.create(
            code=unique_name[:50],  # Limit length
            name=loco[:255] if loco else name[:255],
            operator=operator,
            data={
                "thirdrails_id": unique_name,
                "loco": loco,
                "simulator": simulator,
                "route_name": name,
            },
        )

        self.stdout.write(f"Created vehicle: {vehicle.code} - {vehicle.name}")
        return vehicle

    def create_vehicle_journey(self, vehicle, item):
        """Create vehicle journey from ThirdRails data"""
        # Parse location
        points = item.get("Points", "")
        latlong = self.parse_coordinates(points)
        if not latlong:
            return None

        # Parse speed
        speed = self.parse_speed(item.get("Speed", ""))

        # Extract other data
        route_name = item.get("Name", "")
        simulator = item.get("Simulator", "")
        loco = item.get("Loco", "")

        # Create journey
        journey = VehicleJourney.objects.create(
            datetime=timezone.now(),
            date=timezone.localtime().date(),
            route_name=route_name[:64] if route_name else "",
            source=self.source,
            vehicle=vehicle,
            destination=route_name[:255] if route_name else "",
            data={
                "simulator": simulator,
                "loco": loco,
                "speed": item.get("Speed", ""),
                "drive_type": item.get("DriveType", ""),
                "announcement": item.get("Announcement", ""),
                "announcement_type": item.get("AnnouncementType", ""),
                "coordinates": [latlong.x, latlong.y],
                "speed_kmh": speed,
            },
        )

        # Update vehicle with latest journey
        vehicle.latest_journey = journey
        vehicle.latest_journey_data = item
        vehicle.save(update_fields=["latest_journey", "latest_journey_data"])

        return journey

    def fetch_data(self):
        """Fetch data from ThirdRails API"""
        try:
            response = self.session.post(self.url, json={}, timeout=20)
            response.raise_for_status()

            # Parse JSON response
            routes = response.json()

            # Ensure we have a list (API might return single item)
            if isinstance(routes, dict):
                routes = [routes]

            return routes

        except requests.RequestException as e:
            self.stdout.write(self.style.ERROR(f"Error fetching data: {e}"))
            return None
        except json.JSONDecodeError as e:
            self.stdout.write(self.style.ERROR(f"Error parsing JSON data: {e}"))
            return None

    def import_data(self):
        """Import vehicle locations from ThirdRails API"""
        self.stdout.write("Fetching vehicle locations from ThirdRails...")

        routes = self.fetch_data()
        if not routes:
            self.stdout.write(self.style.WARNING("No data received from API"))
            return

        self.stdout.write(f"Processing {len(routes)} vehicle records...")

        processed = 0
        updated = 0

        for item in routes:
            try:
                unique_name = item.get("UniqueName", "")
                if not unique_name:
                    continue

                # Get or create vehicle
                vehicle = self.get_or_create_vehicle(item)
                if not vehicle:
                    continue

                # Check if this is new data
                latest_data = vehicle.latest_journey_data
                is_new = True

                if latest_data and isinstance(latest_data, dict):
                    latest_points = latest_data.get("Points", "")
                    current_points = item.get("Points", "")
                    if latest_points == current_points:
                        is_new = False

                if is_new:
                    # Create new journey
                    journey = self.create_vehicle_journey(vehicle, item)
                    if journey:
                        updated += 1
                        self.stdout.write(
                            f"Updated {vehicle.code}: {item.get('Name', 'Unknown')}"
                        )
                else:
                    self.stdout.write(f"No changes for {vehicle.code}, skipping")

                processed += 1

            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Error processing item: {e}"))
                logger.error(f"Error processing ThirdRails item: {e}", exc_info=True)
                continue

        self.stdout.write(
            self.style.SUCCESS(
                f"Import complete: {processed} processed, {updated} updated"
            )
        )

    def handle(self, *args, **options):
        """Main command handler"""
        # Set up source
        self.get_or_create_source()

        if options["continuous"]:
            interval = options["interval"]
            self.stdout.write(f"Starting continuous import (interval: {interval}s)")

            while True:
                try:
                    self.import_data()
                    self.stdout.write(f"Waiting {interval} seconds...")
                    sleep(interval)
                except KeyboardInterrupt:
                    self.stdout.write(self.style.WARNING("Stopping continuous import"))
                    break
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"Unexpected error: {e}"))
                    logger.error(
                        f"Unexpected error in ThirdRails import: {e}", exc_info=True
                    )
                    sleep(interval)
        else:
            # One-time import
            self.import_data()
