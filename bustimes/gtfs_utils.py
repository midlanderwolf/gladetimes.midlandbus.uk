from itertools import pairwise

import gtfs_kit
import shapely.ops as so

from .models import Calendar, CalendarDate, RouteLink


MODES = {
    # Basic GTFS route types
    0: "tram",
    1: "subway",
    2: "rail",
    3: "bus",
    4: "ferry",
    6: "cable car",
    7: "funicular",

    # Rail services
    100: "rail",  # Railway Service
    101: "rail",  # High Speed Rail Service
    102: "rail",  # Long Distance Trains
    103: "rail",  # Inter Regional Rail Service
    104: "rail",  # Car Transport Rail Service
    105: "rail",  # Sleeper Rail Service
    106: "rail",  # Regional Rail Service
    107: "rail",  # Tourist Railway Service
    108: "rail",  # Rail Shuttle
    109: "rail",  # Suburban / commuter rail
    110: "rail",  # Replacement Rail Service
    111: "rail",  # Special Rail Service
    112: "rail",  # Lorry Transport Rail Service
    113: "rail",  # All Rail Services
    114: "rail",  # Cross-Country Rail Service
    115: "rail",  # Vehicle Transport Rail Service
    116: "rail",  # Rack and Pinion Railway
    117: "rail",  # Additional Rail Service

    # Coach services
    200: "coach",  # Coach Service
    201: "coach",  # International Coach Service
    202: "coach",  # National Coach Service
    203: "coach",  # Shuttle Coach Service
    204: "coach",  # Regional Coach Service
    205: "coach",  # Special Coach Service
    206: "coach",  # Sightseeing Coach Service
    207: "coach",  # Tourist Coach Service
    208: "coach",  # Commuter Coach Service
    209: "coach",  # All Coach Services

    # Urban rail / metro
    400: "subway",  # Urban Railway Service
    401: "subway",  # Metro Service
    402: "subway",  # Underground Service
    403: "subway",  # Urban Railway Service
    404: "subway",  # All Urban Railway Services
    405: "subway",  # Monorail

    # Bus services
    700: "bus",  # Bus Service
    701: "bus",  # Regional Bus Service
    702: "bus",  # Express Bus Service
    703: "bus",  # Stopping Bus Service
    704: "bus",  # Local Bus Service
    705: "bus",  # Night Bus Service
    706: "bus",  # Post Bus Service
    707: "bus",  # Special Needs Bus
    708: "bus",  # Mobility Bus Service
    709: "bus",  # Mobility Bus for Registered Disabled
    710: "bus",  # Sightseeing Bus
    711: "bus",  # Shuttle Bus
    712: "bus",  # School Bus
    713: "bus",  # School and Public Service Bus
    714: "bus",  # Rail Replacement Bus Service
    715: "bus",  # Demand and Response Bus Service
    716: "bus",  # All Bus Services

    # Trolleybus
    800: "bus",  # Trolleybus Service

    # Tram services
    900: "tram",  # Tram Service
    901: "tram",  # City Tram Service
    902: "tram",  # Local Tram Service
    903: "tram",  # Regional Tram Service
    904: "tram",  # Sightseeing Tram Service
    905: "tram",  # Shuttle Tram Service
    906: "tram",  # All Tram Services

    # Water transport
    1000: "ferry",  # Water Transport Service

    # Air
    1100: "air",  # Air Service

    # Ferry
    1200: "ferry",  # Ferry Service

    # Aerial lift / cable transport
    1300: "cable car",  # Aerial Lift Service
    1301: "cable car",  # Telecabin Service
    1302: "cable car",  # Cable Car Service
    1303: "cable car",  # Elevator Service
    1304: "cable car",  # Chair Lift Service
    1305: "cable car",  # Drag Lift Service
    1306: "cable car",  # Small Telecabin Service
    1307: "cable car",  # All Telecabin Services

    # Funicular
    1400: "funicular",  # Funicular Service

    # Taxi services
    1500: "taxi",  # Taxi Service
    1501: "taxi",  # Communal Taxi Service
    1502: "taxi",  # Water Taxi Service
    1503: "taxi",  # Rail Taxi Service
    1504: "taxi",  # Bike Taxi Service
    1505: "taxi",  # Licensed Taxi Service
    1506: "taxi",  # Private Hire Service Vehicle
    1507: "taxi",  # All Taxi Services

    # Miscellaneous
    1700: "misc",  # Miscellaneous Service
    1702: "horse-drawn carriage",  # Horse-drawn Carriage
}


def get_calendars(feed, source) -> dict:
    calendars = {}

    if feed.calendar is not None:
        calendars = {
            row.service_id: Calendar(
                mon=row.monday,
                tue=row.tuesday,
                wed=row.wednesday,
                thu=row.thursday,
                fri=row.friday,
                sat=row.saturday,
                sun=row.sunday,
                start_date=row.start_date,
                end_date=row.end_date,
                source=source,
            )
            for row in feed.calendar.itertuples()
        }

    calendar_dates = []

    if feed.calendar_dates is not None:
        for row in feed.calendar_dates.itertuples():
            operation = row.exception_type == 1
            # 1: operates, 2: does not operate

            if (calendar := calendars.get(row.service_id)) is None:
                calendar = Calendar(
                    start_date=row.date,  # dummy date
                )
                calendars[row.service_id] = calendar
            calendar_dates.append(
                CalendarDate(
                    calendar=calendar,
                    start_date=row.date,
                    end_date=row.date,
                    operation=operation,
                    special=operation,  # additional date of operation
                )
            )

    Calendar.objects.bulk_create(calendars.values())
    CalendarDate.objects.bulk_create(calendar_dates)

    return calendars


def do_route_links(
    feed: gtfs_kit.feed.Feed, source, routes: dict, stops: dict, stop_codes: dict = None
):
    try:
        trips = feed.get_trips(as_gdf=True).drop_duplicates("shape_id")
    except ValueError:
        return

    existing_route_links = {
        (rl.service_id, rl.from_stop_id, rl.to_stop_id): rl
        for rl in RouteLink.objects.filter(service__source=source)
    }
    route_links = {}

    stop_times_by_trip = dict(tuple(feed.stop_times.groupby("trip_id", sort=False)))

    for trip in trips.itertuples():
        if trip.geometry is None:
            continue

        service = routes[trip.route_id].service_id

        trip_stop_times = stop_times_by_trip.get(trip.trip_id)
        if trip_stop_times is None:
            continue

        start_dist = None

        for a, b in pairwise(trip_stop_times.itertuples()):
            from_stop_id = (
                stop_codes.get(a.stop_id, a.stop_id) if stop_codes else a.stop_id
            )
            to_stop_id = (
                stop_codes.get(b.stop_id, b.stop_id) if stop_codes else b.stop_id
            )
            key = (service, from_stop_id, to_stop_id)

            if key in route_links:
                start_dist = None
                continue

            stop_a = stops[a.stop_id]
            point_a = so.Point(stop_a.stop_lon, stop_a.stop_lat)
            if not start_dist:
                start_dist = trip.geometry.project(point_a)
            stop_b = stops[b.stop_id]
            point_b = so.Point(stop_b.stop_lon, stop_b.stop_lat)
            end_dist = trip.geometry.project(point_b)

            # skip if either stop is too far from the route geometry (~1km at UK latitudes)
            projected_a = trip.geometry.interpolate(start_dist)
            projected_b = trip.geometry.interpolate(end_dist)
            if (
                point_a.distance(projected_a) > 0.01
                or point_b.distance(projected_b) > 0.01
            ):
                start_dist = None
                continue

            geom = so.substring(trip.geometry, start_dist, end_dist)
            if type(geom) is so.LineString:
                if key in existing_route_links:
                    rl = existing_route_links[key]
                else:
                    rl = RouteLink(
                        service_id=key[0],
                        from_stop_id=key[1],
                        to_stop_id=key[2],
                    )
                rl.geometry = geom.wkt
                route_links[key] = rl

            start_dist = end_dist

    RouteLink.objects.bulk_update(
        [rl for rl in route_links.values() if rl.id], fields=["geometry"]
    )
    RouteLink.objects.bulk_create([rl for rl in route_links.values() if not rl.id])