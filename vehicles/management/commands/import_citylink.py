import json
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import bs4
import requests
from django.contrib.gis.geos import Point

from busstops.models import Service

from ...models import VehicleJourney, VehicleLocation
from ..import_live_vehicles import ImportLiveVehiclesCommand
from .import_vehicles import normalize_registration

logger = logging.getLogger(__name__)

TZ = ZoneInfo("Europe/London")

# RouteID -> used only to ask GetJourney for that route's current journeys;
# the response itself carries the service number (RouteNo), so there's no
# need to also hardcode those. Scraped from the "Search by Service Number"
# dropdown on https://citylink.tmpanel.co.uk/ - there's no API to list
# these, so if Citylink adds a new service this list needs updating
ROUTE_IDS = (
    "56",  # 618
    "1",  # 900
    "3",  # 902
    "2",  # 909
    "5",  # 913
    "6",  # 915
    "10",  # 917
    "9",  # 919
    "4",  # 923
    "11",  # 926
    "12",  # 961
    "14",  # 976
    "15",  # 978
    "18",  # AIR
    "57",  # FALC
    "21",  # M10
    "17",  # M8
    "22",  # M9
    "28",  # M90
    "24",  # M92
    "30",  # PM8
    "31",  # PM9
    "32",  # PM90
    "33",  # PM91
)

HEADERS = {
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://citylink.tmpanel.co.uk/",
}

# the Bristol-Plymouth "Falcon" service is run under contract by Stagecoach
# Devon, using Stagecoach's own fleet - those vehicles belong under SDVN,
# not SCLK, even though they're tracked through Citylink's tracker
FALCON_OPERATOR = "SDVN"

# some of the Highland network services tracked through Citylink's tracker
# are actually run under contract by Park's of Hamilton, using PKOH's own
# fleet, not Citylink's:
# - "M91" has no SCLK-operated service at all - it's entirely PKOH's, so a
#   lookup for Service(line_name="M91", operator="SCLK") always misses
# - "M9"/"PM9" is worse: there happen to be two unrelated services both
#   named "M9" - Citylink's own Glasgow-Dundee-Forfar service, and PKOH's
#   Glasgow-Aberdeen service that Citylink's tracker actually reports under
#   this route - so looking up operator="SCLK" doesn't miss, it matches the
#   wrong service, and journeys get attached to the wrong trips
OPERATOR_REMAP = {
    "M9": "PKOH",
    "PM9": "PKOH",
    "M91": "PKOH",
    "PM91": "PKOH",
}

# the tracker calls it "FALC", but bustimes.org's own SDVN service for the
# same route (Plymouth - Bristol) is published under the line name "FAL" -
# without this remap, Service lookups for Falcon journeys always miss
#
# the tracker reports one direction of M8, M9, M90 and M91 with a "P"
# prefix (perhaps for the return/"Plymouth-style" working - unclear), but
# bustimes.org's services are published under the line names M8, M9, M90
# and M91 - without this remap, Service lookups for these journeys always
# miss
LINE_NAME_REMAP = {
    "FALC": "FAL",
    "PM8": "M8",
    "PM9": "M9",
    "PM90": "M90",
    "PM91": "M91",
}


class Command(ImportLiveVehiclesCommand):
    source_name = vehicle_code_scheme = "citylink"
    operator = "SCLK"
    url = "https://citylink.tmpanel.co.uk"
    tzinfo = TZ

    def get_live_journeys(self, route_id):
        try:
            response = self.session.post(
                f"{self.url}/Tracker/GetJourney",
                headers=HEADERS,
                data={
                    "FromStage": "",
                    "ToStage": "",
                    "RouteID": route_id,
                    "TicketNumber": "",
                    "IsStageSelection": "false",
                },
                timeout=10,
            )
            response.raise_for_status()
            data = response.json()
        except (requests.RequestException, ValueError):
            logger.exception("citylink: GetJourney %s failed", route_id)
            return []

        if data.get("OpStatus") != "SUCCESS":
            return []

        soup = bs4.BeautifulSoup(data.get("JourneyList") or "", "html.parser")
        return [
            div
            for div in soup.find_all("div", class_="cls-jrny")
            # "live" journeys that haven't actually started yet still get
            # data-islive="1" with no journey id to track GPS against
            if div.get("data-islive") == "1" and div.get("data-journeyid") != "0"
        ]

    def get_journey_stage(self, jrny_id, journey_id):
        try:
            response = self.session.post(
                f"{self.url}/Tracker/GetJourneyStage",
                headers=HEADERS,
                data={"JrnyID": jrny_id, "JourneyID": journey_id},
                timeout=10,
            )
            response.raise_for_status()
            data = response.json()
        except (requests.RequestException, ValueError):
            logger.exception("citylink: GetJourneyStage %s failed", jrny_id)
            return None

        if data.get("OpStatus") != "SUCCESS":
            return None

        # "entity" is itself a JSON string, not a nested object
        entity = json.loads(data["entity"])
        positions = entity.get("Table2")
        return positions[0] if positions else None

    def get_items(self):
        items = []
        for route_id in ROUTE_IDS:
            for div in self.get_live_journeys(route_id):
                jrny_id = div["data-jrnyid"]
                journey_id = div["data-journeyid"]

                position = self.get_journey_stage(jrny_id, journey_id)
                if not position or not position.get("Lat") or not position.get("Lng"):
                    continue

                reg = normalize_registration(
                    position.get("BusReg") or div.get("data-busreg")
                )
                if not reg:
                    continue

                # the tracker only gives a start *date* and separate start/end
                # *times* - combine them into full datetimes (stored as
                # isoformat strings, not datetime objects, since this dict
                # ends up saved verbatim into Vehicle.latest_journey_data, a
                # JSONField that can't serialize a raw datetime)
                start = datetime.strptime(
                    div["data-startdate"], "%d/%m/%Y %H:%M:%S"
                ).replace(tzinfo=TZ)
                end_time = datetime.strptime(div["data-endtime"], "%H:%M").time()
                end = datetime.combine(start.date(), end_time, tzinfo=TZ)
                if end < start:
                    end += timedelta(days=1)  # journey runs past midnight

                geo_dt_time = datetime.fromisoformat(position["GeoDtTime"]).replace(
                    tzinfo=TZ
                )

                items.append(
                    {
                        "BusReg": reg,
                        "JrnyID": jrny_id,
                        "JourneyID": journey_id,
                        "RouteID": route_id,
                        "RouteNo": div.get("data-routeno", ""),
                        "StartStage": div.get("data-startstage", ""),
                        "EndStage": div.get("data-endstage", ""),
                        "Start": start.isoformat(),
                        "End": end.isoformat(),
                        "Lat": float(position["Lat"]),
                        "Lng": float(position["Lng"]),
                        "GeoDtTime": geo_dt_time.isoformat(),
                        "BusStatus": position.get("BusStatus", ""),
                        "Deviation": position.get("Deviation", ""),
                        "Occupancy": position.get("Occupancy", ""),
                    }
                )
        return items

    @staticmethod
    def get_datetime(item):
        return datetime.fromisoformat(item["GeoDtTime"])

    @staticmethod
    def get_vehicle_identity(item):
        return item["BusReg"]

    @staticmethod
    def get_journey_identity(item):
        return (item["JrnyID"], item["Start"])

    @staticmethod
    def get_item_identity(item):
        return item["GeoDtTime"]

    def get_operator(self, item):
        if item["RouteNo"] == "FALC":
            return FALCON_OPERATOR
        return OPERATOR_REMAP.get(item["RouteNo"], self.operator)

    @staticmethod
    def get_line_name(item):
        return LINE_NAME_REMAP.get(item["RouteNo"], item["RouteNo"])

    def get_vehicle(self, item):
        operator = self.get_operator(item)
        reg = item["BusReg"]
        vehicle = self.vehicles.filter(operator_id=operator, reg=reg).first()
        if vehicle:
            return vehicle, False
        return self.vehicles.get_or_create(
            {"source": self.source, "reg": reg},
            operator_id=operator,
            code=reg,
        )

    def get_journey(self, item, vehicle):
        journey = VehicleJourney()
        journey.code = item["JrnyID"]
        journey.route_name = self.get_line_name(item)
        journey.destination = item["EndStage"]
        journey.datetime = datetime.fromisoformat(item["Start"])
        journey.service = Service.objects.filter(
            line_name=journey.route_name,
            operator=self.get_operator(item),
            current=True,
        ).first()
        if journey.service:
            journey.trip = journey.get_trip(departure_time=journey.datetime)
        return journey

    def create_vehicle_location(self, item):
        return VehicleLocation(latlong=Point(item["Lng"], item["Lat"]))
