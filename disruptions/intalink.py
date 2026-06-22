import logging

import requests
from django.db.backends.postgresql.psycopg_any import DateTimeTZRange
from django.db.models import Q

from busstops.models import DataSource, Operator, Service, StopPoint

from .models import Consequence, Situation, ValidityPeriod

logger = logging.getLogger(__name__)


def handle_alert(alert: dict, source: DataSource, operators: dict, services):
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

    # Delete existing validity periods and recreate
    ValidityPeriod.objects.filter(situation=situation).delete()

    # Deduplicate periods by start/end time
    seen_periods = set()
    for period_data in periods:
        start = period_data["start"]
        end = period_data.get("end")
        period_key = (start, end)
        if period_key not in seen_periods:
            seen_periods.add(period_key)
            ValidityPeriod.objects.create(
                situation=situation, period=DateTimeTZRange(start, end, "[]")
            )

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
        operator_id = line.get("_embedded", {}).get("transmodel:operator", {}).get("id")
        operator = operators.get(operator_id)
        if not operator:
            continue
        matching = services.filter(
            Q(route__line_name__iexact=line_name) | Q(line_name__iexact=line_name),
            operator=operator,
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


def intalink_disruptions():
    session = requests.Session()
    session.headers.update({"User-Agent": "bustimes.org"})

    source = DataSource.objects.get_or_create(name="Intalink")[0]

    response = session.get(
        "https://intalink.arcticapi.com/network/disruptions", timeout=30
    )
    response.raise_for_status()
    data = response.json()

    alerts = data["_embedded"]["alert"]

    operator_ids = set()
    for alert in alerts:
        for line in alert.get("_embedded", {}).get("line", []):
            operator_id = (
                line.get("_embedded", {}).get("transmodel:operator", {}).get("id")
            )
            if operator_id:
                operator_ids.add(operator_id)

    operators = {o.noc: o for o in Operator.objects.filter(noc__in=operator_ids)}

    services = Service.objects.filter(current=True).only("id", "line_name")

    ids = set()
    for alert in alerts:
        if situation_id := handle_alert(alert, source, operators, services):
            ids.add(situation_id)

    source.situation_set.filter(current=True).exclude(id__in=ids).update(current=False)
