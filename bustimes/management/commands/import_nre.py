import zipfile
from datetime import date, timezone, timedelta, datetime
from functools import cache
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import Q
from busstops.models import DataSource, Operator, Service, StopPoint

from ...models import (
    Calendar,
    Route,
    StopTime,
    Trip,
)


def parse_cif_date(d):
    if d == b"999999" or d == "999999":
        return
    if isinstance(d, bytes):
        d = d.decode()
    return date(year=int(d[:2]) + 2000, month=int(d[2:4]), day=int(d[4:6]))


def parse_time(t):
    if isinstance(t, bytes):
        t = t.decode()
    t = t.strip()
    if not t:
        return
    return timedelta(hours=int(t[:2]), minutes=int(t[2:4]))


@cache
def get_operator(atoc_code):
    if not atoc_code or atoc_code == "ZZ":
        return
    try:
        return Operator.objects.get(noc=atoc_code)
    except Operator.DoesNotExist:
        pass
    try:
        return Operator.objects.get(
            operatorcode__code=atoc_code, operatorcode__source__name="National Operator Codes"
        )
    except (Operator.DoesNotExist, Operator.MultipleObjectsReturned):
        pass


class Command(BaseCommand):
    help = "Import railway timetable data from NRE CIF files"

    @staticmethod
    def add_arguments(parser):
        parser.add_argument("filenames", nargs="*", type=str)

    def handle(self, *args, **options):
        self.source, _ = DataSource.objects.get_or_create(name="NRE")

        filenames = options["filenames"] or [
            str(settings.DATA_DIR / "NRE" / "timetable.zip")
        ]

        for filename in filenames:
            self.handle_archive(filename)

    def handle_archive(self, archive_name):
        self.source.datetime = datetime.fromtimestamp(
            Path(archive_name).stat().st_mtime, timezone.utc
        )

        with zipfile.ZipFile(archive_name) as archive:
            self.stations = {}
            for name in archive.namelist():
                if name.upper().endswith(".MCA"):
                    with archive.open(name) as f:
                        self.parse_mca(f)

            for name in archive.namelist():
                if name.upper().endswith(".ZTR"):
                    with archive.open(name) as f:
                        self.parse_ztr(f)

        services = {
            route.service.id: route.service
            for route in self.routes.values()
            if route.service_id
        }.values()

        for service in services:
            service.do_stop_usages()
            service.update_geometry(save=False)

        Service.objects.bulk_update(
            services,
            fields=[
                "geometry",
                "description",
            ],
        )

        Route.objects.bulk_update(
            self.routes.values(),
            ["inbound_description", "revision_number", "start_date"],
        )

        for service in services:
            service.update_search_vector()

        self.clean_up()

    def parse_mca(self, open_file):
        for line in open_file:
            if len(line) >= 56 and line.startswith(b"TI"):
                crs = line[53:56].decode()
                if crs.strip() and crs.isalpha():
                    name = line[18:44].decode().strip()
                    self.stations[crs] = name

    def parse_ztr(self, open_file):
        self.routes = {}
        self.calendars = {}
        self.schedules = []
        self.schedule = None
        self.stops = {}

        content = open_file.read()
        lines = content.decode().splitlines()

        for line in lines:
            if not line.strip():
                continue
            identity = line[:2]

            if identity == "BS":
                self.handle_bs(line)
            elif identity == "BX":
                self.handle_bx(line)
            elif identity == "CR" or identity == "ZZ":
                pass
            elif identity == "LO":
                self.handle_lo(line)
            elif identity == "LI":
                self.handle_li(line)
            elif identity == "LT" and self.schedule:
                self.handle_lt(line)
                self.schedules.append(self.schedule)
                self.schedule = None

        self.finish_schedules()

    def handle_bs(self, line):
        transaction_type = line[2] if len(line) > 2 else ""
        if transaction_type == "D":
            return

        train_uid = line[3:9]
        date_from = parse_cif_date(line[9:15])
        date_to = parse_cif_date(line[15:21])

        days_run = line[21:28]
        days = {
            "mon": days_run[0] == "1",
            "tue": days_run[1] == "1",
            "wed": days_run[2] == "1",
            "thu": days_run[3] == "1",
            "fri": days_run[4] == "1",
            "sat": days_run[5] == "1",
            "sun": days_run[6] == "1",
        }

        self.schedule = {
            "train_uid": train_uid,
            "date_from": date_from,
            "date_to": date_to,
            "days": days,
            "stops": [],
            "atoc_code": None,
        }

    def handle_bx(self, line):
        if not self.schedule:
            return
        atoc_code = line[11:13].strip()
        if atoc_code:
            self.schedule["atoc_code"] = atoc_code

    @staticmethod
    def get_location_code(line):
        return line[2:10].replace("-", "").strip()

    def handle_lo(self, line):
        if not self.schedule:
            return
        loc = self.get_location_code(line)
        dep = parse_time(line[10:15])
        self.schedule["stops"].append({
            "location": loc,
            "sequence": 0,
            "departure": dep,
        })

    def handle_li(self, line):
        if not self.schedule:
            return
        loc = self.get_location_code(line)
        arr = parse_time(line[10:15])
        dep = parse_time(line[15:20])
        self.schedule["stops"].append({
            "location": loc,
            "arrival": arr,
            "departure": dep,
            "sequence": len(self.schedule["stops"]),
        })

    def handle_lt(self, line):
        loc = self.get_location_code(line)
        arr = parse_time(line[10:15])
        self.schedule["stops"].append({
            "location": loc,
            "arrival": arr,
            "sequence": len(self.schedule["stops"]),
        })

    def get_or_create_stop(self, code):
        if code in self.stops:
            return self.stops[code]

        naptan_atco = f"9100{code}"
        existing = StopPoint.objects.filter(
            Q(atco_code=naptan_atco) | Q(atco_code=code)
        ).first()
        if existing:
            self.stops[code] = existing
            return existing

        stop = StopPoint(
            atco_code=code,
            common_name=self.stations.get(code, code),
            active=True,
            source=self.source,
            stop_type="RLY",
        )
        stop.save()
        self.stops[code] = stop
        return stop

    def get_calendar(self, schedule):
        key = (
            f"{schedule['date_from']}_{schedule['date_to']}_"
            f"{schedule['days']['mon']}{schedule['days']['tue']}"
            f"{schedule['days']['wed']}{schedule['days']['thu']}"
            f"{schedule['days']['fri']}{schedule['days']['sat']}"
            f"{schedule['days']['sun']}"
        )
        if key in self.calendars:
            return self.calendars[key]

        calendar = Calendar(
            mon=schedule["days"]["mon"],
            tue=schedule["days"]["tue"],
            wed=schedule["days"]["wed"],
            thu=schedule["days"]["thu"],
            fri=schedule["days"]["fri"],
            sat=schedule["days"]["sat"],
            sun=schedule["days"]["sun"],
            start_date=schedule["date_from"],
            end_date=schedule["date_to"],
            source=self.source,
        )
        calendar.save()
        self.calendars[key] = calendar
        return calendar

    def get_route(self, schedule):
        uid = schedule["train_uid"]
        stops = schedule["stops"]
        if not stops:
            return

        first_stop = stops[0]
        last_stop = stops[-1]

        origin = self.stations.get(first_stop["location"], first_stop["location"])
        destination = self.stations.get(last_stop["location"], last_stop["location"])

        description = f"{origin} – {destination}"
        route_code = f"NRE_{uid}"

        if route_code in self.routes:
            return self.routes[route_code]

        line_name = uid

        defaults = {
            "line_name": line_name,
            "current": True,
            "source": self.source,
            "region_id": "GB",
        }
        route_defaults = {
            "line_name": line_name,
            "description": description,
            "service_code": uid,
            "origin": origin,
            "destination": destination,
        }

        service, _ = Service.objects.update_or_create(
            defaults,
            service_code=uid,
        )

        if atoc_code := schedule.get("atoc_code"):
            if operator := get_operator(atoc_code):
                service.operator.add(operator)

        route_defaults["service"] = service
        route, created = Route.objects.update_or_create(
            route_defaults,
            code=route_code,
            source=self.source,
        )
        if not created:
            route.trip_set.all().delete()
        self.routes[route_code] = route
        return route

    def finish_schedules(self):
        all_stop_times = []

        for schedule in self.schedules:
            route = self.get_route(schedule)
            if not route:
                continue

            stops = schedule["stops"]
            if not stops:
                continue

            calendar = self.get_calendar(schedule)

            first_stop = stops[0]
            last_stop = stops[-1]

            trip = Trip(
                route=route,
                calendar=calendar,
                start=first_stop["departure"],
                end=last_stop["arrival"],
                inbound=False,
            )
            trip.save()

            for i, stop_data in enumerate(stops):
                stop = self.get_or_create_stop(stop_data["location"])

                st = StopTime(
                    trip=trip,
                    sequence=i,
                    arrival=stop_data.get("arrival"),
                    departure=stop_data.get("departure"),
                    stop=stop,
                )
                all_stop_times.append(st)

        StopTime.objects.bulk_create(all_stop_times)

    def clean_up(self):
        valid_route_codes = set(self.routes.keys())
        print(
            self.source.route_set.exclude(
                code__in=valid_route_codes, trip__isnull=False
            ).delete()
        )
        print(
            self.source.service_set.filter(current=True, route__isnull=True).update(
                current=False
            )
        )
        self.source.save(update_fields=["datetime"])
