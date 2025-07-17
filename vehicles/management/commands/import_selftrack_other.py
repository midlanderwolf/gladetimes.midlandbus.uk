from datetime import datetime, timezone

from django.contrib.gis.geos import GEOSGeometry
from django.db.models import Exists, OuterRef, Q

from busstops.models import Operator, Service, StopPoint

from ...models import Vehicle, VehicleJourney, VehicleLocation
from ..import_live_vehicles import ImportLiveVehiclesCommand

def parse_timestamp(timestamp):
    if timestamp:
        return datetime.fromtimestamp(int(timestamp) / 1000, timezone.utc)

def has_stop(stop):
    return Exists(
        StopPoint.objects.filter(service=OuterRef("pk"), locality__stoppoint=stop)
    )

class Command(ImportLiveVehiclesCommand):
    source_name = "selftrack_other"
    previous_locations = {}

    def do_source(self):
        # Updated: Keyed by NOC code (case-insensitive)
        self.operators = {
            op.noc.upper(): op for op in Operator.objects.all()
        }
        return super().do_source()

    @staticmethod
    def get_datetime(item):
        return parse_timestamp(item["ut"])

    def prefetch_vehicles(self, vehicle_codes):
        vehicles = self.vehicles.filter(
            operator__in=self.operators.values(), code__in=vehicle_codes
        )
        self.vehicle_cache = {vehicle.code: vehicle for vehicle in vehicles}

    def get_items(self):
        items = []
        vehicle_codes = []

        for item in super().get_items()["services"]:
            key = item["fn"]
            value = (item["ut"],)
            if self.previous_locations.get(key) != value:
                items.append(item)
                vehicle_codes.append(key)
                self.previous_locations[key] = value

        self.prefetch_vehicles(vehicle_codes)

        return items

    def get_vehicle(self, item) -> tuple[Vehicle, bool]:
        vehicle_code = item["fn"]

        operator_id = item.get("oc", "").upper()

        service_operator = item.get("so")
        if service_operator == "SMA" and operator_id == "SCLK":
            operator_id = "SCMN"

        if vehicle_code in self.vehicle_cache:
            vehicle = self.vehicle_cache[vehicle_code]

            if (
                vehicle.operator_id == "SCLK"
                and operator_id != "SCLK"
                or operator_id == "SCLK"
                and vehicle.operator_id != "SCLK"
            ):
                vehicle = (
                    self.vehicles.filter(
                        Q(code__iexact=vehicle_code)
                        | Q(fleet_code__iexact=vehicle_code),
                        operator__in=self.operators.values(),
                    )
                    .exclude(id=vehicle.id)
                    .first()
                )

                if vehicle:
                    self.vehicle_cache[vehicle_code] = vehicle

            if vehicle:
                return vehicle, False

        operator = self.operators.get(operator_id)

        vehicle = Vehicle.objects.filter(
            operator=None, code__iexact=vehicle_code
        ).first()
        if vehicle or item.get("hg") == "0":
            return vehicle, False

        vehicle = Vehicle.objects.create(
            operator=operator,
            source=self.source,
            code=vehicle_code,
            fleet_code=vehicle_code,
        )
        return vehicle, True

    def get_journey(self, item, vehicle):
        latest_journey = getattr(vehicle, "latest_journey", None)

        if item.get("ao"):
            departure_time = parse_timestamp(item["ao"])
        else:
            departure_time = None

        if departure_time:
            if (
                vehicle.latest_journey
                and abs(vehicle.latest_journey.datetime - departure_time).total_seconds() < 60
            ):
                return vehicle.latest_journey
            try:
                return vehicle.vehiclejourney_set.get(datetime=departure_time)
            except VehicleJourney.DoesNotExist:
                pass
        elif item.get("eo"):
            departure_time = parse_timestamp(item["eo"])

        journey = VehicleJourney(
            datetime=departure_time,
            destination=item.get("dd", "") or item.get("fs", ""),
            route_name=item.get("sn", ""),
        )

        if code := item.get("td", ""):
            journey.code = code
        elif (
            not departure_time
            and latest_journey
            and journey.route_name == latest_journey.route_name
            and latest_journey.datetime.date() == self.source.datetime.date()
        ):
            journey = latest_journey

        if not journey.service_id and journey.route_name:
            services = Service.objects.filter(current=True, operator__in=self.operators.values())

            stop = item.get("or") or item.get("pr") or item.get("nr")

            if stop:
                services = services.filter(has_stop(stop))

            if item.get("fr"):
                services = services.filter(has_stop(item["fr"]))

            journey.service = services.filter(
                Q(route__line_name__iexact=journey.route_name)
                | Q(line_name__iexact=journey.route_name)
            ).first()

            if not journey.service:
                print(journey.route_name, item.get("or"), vehicle.get_absolute_url())

        if departure_time and journey.service and not journey.id:
            journey.trip = journey.get_trip(
                destination_ref=item.get("fr"), departure_time=departure_time
            )

            if trip := journey.trip:
                if trip.garage_id != vehicle.garage_id:
                    vehicle.garage_id = trip.garage_id
                    vehicle.save(update_fields=["garage"])

        return journey

    def create_vehicle_location(self, item):
        return VehicleLocation(
            latlong=GEOSGeometry(f"POINT({item['lo']} {item['la']})"),
            heading=item.get("hg"),
        )
