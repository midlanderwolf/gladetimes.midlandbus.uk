import logging

import requests
from django.db.backends.postgresql.psycopg_any import DateTimeTZRange
from django.db.models import Q

from busstops.models import DataSource, Service, StopPoint

from .models import Consequence, Situation, ValidityPeriod

logger = logging.getLogger(__name__)


def handle_alert(alert: dict, source: DataSource, services):
    situation_number = alert["id"]

    situation = Situation.objects.filter(
        source=source, situation_number=situation_number
    ).first()

    if situation:
        created = False
    else:
        situation = Situation(
            source=source,
            situation_number=situation_number,
            created_at=alert["created"],
        )
        created = True

    situation.current = not alert["resolved"]
    situation.summary = alert["header"]
    situation.text = alert["description"].replace("\r\n", "\n").strip()
    situation.text = situation.text.replace(". ", ".\n\n")
    situation.reason = alert["cause"]
    situation.data = alert.get("effect", "")

    periods = alert.get("activePeriods", [])
    if periods:
        start = periods[0]["start"]
        end = periods[0].get("end")
        situation.publication_window = DateTimeTZRange(start, end, "[]")

    situation.save()

    for i, period_data in enumerate(periods):
        period = ValidityPeriod(situation=situation)
        if created and i == 0:
            try:
                period = situation.validityperiod_set.get()
            except ValidityPeriod.DoesNotExist:
                pass
        start = period_data["start"]
        end = period_data.get("end")
        period.period = DateTimeTZRange(start, end, "[]")
        period.save()

    consequence = Consequence(situation=situation)
    if not created:
        try:
            consequence = situation.consequence_set.get()
        except Consequence.MultipleObjectsReturned:
            situation.consequence_set.all().delete()
        except Consequence.DoesNotExist:
            pass

    if consequence.id:
        consequence.services.clear()

    consequence.save()

    lines = alert.get("_embedded", {}).get("line", [])
    for line in lines:
        line_name = line["name"]
        matching = services.filter(
            Q(route__line_name__iexact=line_name) | Q(line_name__iexact=line_name),
        )
        matching = matching.distinct()
        if matching:
            consequence.services.add(*matching)

    stops = alert.get("_embedded", {}).get("stop", [])
    if stops:
        atco_codes = [stop["atcoCode"] for stop in stops if "atcoCode" in stop]
        if atco_codes:
            matching_stops = StopPoint.objects.filter(
                Q(atco_code__in=atco_codes) | Q(stop_area__in=atco_codes)
            ).only("atco_code")
            if matching_stops:
                consequence.stops.add(*matching_stops)

    return situation.id


def mcgills_disruptions():
    session = requests.Session()
    session.headers.update({"User-Agent": "bustimes.org"})

    source = DataSource.objects.get_or_create(name="McGill's")[0]

    response = session.get(
        "https://mcgills.arcticapi.com/network/disruptions", timeout=30
    )
    response.raise_for_status()
    data = response.json()

    alerts = data["_embedded"]["alert"]

    services = Service.objects.filter(operator__noc="MCGL", current=True).only(
        "id", "line_name"
    )

    ids = set()
    for alert in alerts:
        if situation_id := handle_alert(alert, source, services):
            ids.add(situation_id)

    source.situation_set.filter(current=True).exclude(id__in=ids).update(current=False)
