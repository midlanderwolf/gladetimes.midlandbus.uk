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
    5: "subway",
    6: "cable car",
    7: "funicular",
    # Rail services
    100: "rail",
    101: "rail",
    102: "rail",
    103: "rail",
    104: "rail",
    105: "rail",
    106: "rail",
    107: "rail",
    108: "rail",
    109: "rail",
    110: "rail",
    111: "rail",
    112: "rail",
    113: "rail",
    114: "rail",
    115: "rail",
    116: "rail",
    117: "rail",
    # Coach services
    200: "coach",
    201: "coach",
    202: "coach",
    203: "coach",
    204: "coach",
    205: "coach",
    206: "coach",
    207: "coach",
    208: "coach",
    209: "coach",
    # Suburban Railway
    300: "rail",
    # Urban rail / metro
    400: "subway",
    401: "subway",
    402: "subway",
    403: "subway",
    404: "subway",
    405: "subway",
    # Monorail
    500: "monorail",
    501: "monorail",
    502: "monorail",
    503: "monorail",
    504: "monorail",
    505: "monorail",
    506: "monorail",
    507: "monorail",
    # Underground / metro
    600: "subway",
    601: "subway",
    602: "subway",
    603: "subway",
    604: "subway",
    605: "subway",
    606: "subway",
    607: "subway",
    # Bus services
    700: "bus",
    701: "bus",
    702: "bus",
    703: "bus",
    704: "bus",
    705: "bus",
    706: "bus",
    707: "bus",
    708: "bus",
    709: "bus",
    710: "bus",
    711: "bus",
    712: "bus",
    713: "bus",
    714: "bus",
    715: "bus",
    716: "bus",
    # Trolleybus
    800: "bus",
    801: "bus",
    802: "bus",
    803: "bus",
    804: "bus",
    805: "bus",
    806: "bus",
    # Tram services
    900: "tram",
    901: "tram",
    902: "tram",
    903: "tram",
    904: "tram",
    905: "tram",
    906: "tram",
    # Water transport
    1000: "ferry",
    1001: "ferry",
    1002: "ferry",
    1003: "ferry",
    1004: "ferry",
    1005: "ferry",
    1006: "ferry",
    1007: "ferry",
    1008: "ferry",
    1009: "ferry",
    1010: "ferry",
    1011: "ferry",
    1012: "ferry",
    1013: "ferry",
    1014: "ferry",
    1015: "ferry",
    1016: "ferry",
    1017: "ferry",
    1018: "ferry",
    1019: "ferry",
    1020: "ferry",
    1021: "ferry",
    # Air
    1100: "air",
    1101: "air",
    1102: "air",
    1103: "air",
    1104: "air",
    1105: "air",
    1106: "air",
    1107: "air",
    1108: "air",
    1109: "air",
    1110: "air",
    1111: "air",
    1112: "air",
    1113: "air",
    1114: "air",
    # Ferry
    1200: "ferry",
    1201: "ferry",
    1202: "ferry",
    1203: "ferry",
    1204: "ferry",
    1205: "ferry",
    1206: "ferry",
    1207: "ferry",
    1208: "ferry",
    1209: "ferry",
    1210: "ferry",
    1211: "ferry",
    1212: "ferry",
    1213: "ferry",
    1214: "ferry",
    # Aerial lift / cable transport
    1300: "cable car",
    1301: "cable car",
    1302: "cable car",
    1303: "cable car",
    1304: "cable car",
    1305: "cable car",
    1306: "cable car",
    1307: "cable car",
    1308: "cable car",
    1309: "cable car",
    1310: "cable car",
    # Funicular
    1400: "funicular",
    1401: "funicular",
    1402: "funicular",
    1403: "funicular",
    1404: "funicular",
    1405: "funicular",
    1406: "funicular",
    1407: "funicular",
    # Taxi services
    1500: "taxi",
    1501: "taxi",
    1502: "taxi",
    1503: "taxi",
    1504: "taxi",
    1505: "taxi",
    1506: "taxi",
    1507: "taxi",
    # Other
    1600: "misc",
    1601: "misc",
    1602: "misc",
    1603: "misc",
    1604: "misc",
    1605: "misc",
    # Miscellaneous
    1700: "misc",
    1702: "horse-drawn carriage",
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
    RouteLink.objects.bulk_create(
        [rl for rl in route_links.values() if not rl.id], ignore_conflicts=True
    )
