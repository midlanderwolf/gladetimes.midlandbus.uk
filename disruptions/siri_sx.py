import io
import logging
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime

import requests
from django.db.backends.postgresql.psycopg_any import DateTimeTZRange
from django.db.models import Q

from busstops.models import DataSource, Operator, Service, StopPoint

from .models import Consequence, Link, Situation, ValidityPeriod

logger = logging.getLogger(__name__)


def get_period(element):
    start = element.find("StartTime").text
    end = element.findtext("EndTime")
    return DateTimeTZRange(start, end, "[]")


def get_operators(operator_ref):
    return Operator.objects.filter(
        operatorcode__code=operator_ref,
        operatorcode__source__name="National Operator Codes",
    )


def handle_item(item: ET.Element, sources: dict, current_situations: dict):
    situation_number = item.findtext("SituationNumber")

    # the bulk feed is itself an aggregate of several different local
    # authorities' own SIRI-SX feeds - ParticipantRef identifies which one
    # a given situation actually came from (e.g. "TfGM", "WestofEngland"),
    # so it can get its own DataSource instead of everything being lumped
    # under one generic "Bus Open Data" source
    participant_ref = item.findtext("ParticipantRef")
    source = sources[participant_ref]

    item.find("Source/TimeOfCommunication").text = None

    xml = ET.tostring(item, encoding="unicode")

    situation = current_situations.get(situation_number)

    if situation:
        # migrate a situation created before each ParticipantRef got its
        # own source (or one whose ParticipantRef has genuinely changed)
        # onto the right one now, rather than leaving it stuck on
        # whatever source it was first seen under
        if situation.source_id != source.id:
            situation.source = source
            situation.save(update_fields=["source"])
        if situation.data == xml:
            return situation.id  # hasn't changed
        created = False
    else:
        situation = Situation(source=source, situation_number=situation_number)
        created = True

    situation.current = True

    situation.data = xml
    situation.created_at = datetime.fromisoformat(item.find("CreationTime").text)
    if modified_at := item.findtext("VersionedAtTime"):
        situation.modified_at = datetime.fromisoformat(modified_at)
    situation.publication_window = get_period(item.find("PublicationWindow"))

    assert item.findtext("Progress") == "open"

    reason = item.findtext("MiscellaneousReason")
    if reason:
        situation.reason = reason

    situation.participant_ref = item.find("ParticipantRef").text
    situation.summary = item.find("Summary").text
    situation.text = item.find("Description").text
    situation.save()

    for i, link_element in enumerate(item.findall("InfoLinks/InfoLink/Uri")):
        link = Link(situation=situation)
        if not created and i == 0:
            try:
                link = situation.link_set.get()
            except Link.MultipleObjectsReturned:
                situation.link_set.all().delete()
            except Link.DoesNotExist:
                pass
        if link_element.text:
            link.url = link_element.text
            link.save()

    # Delete existing validity periods and recreate
    ValidityPeriod.objects.filter(situation=situation).delete()

    for period_element in item.findall("ValidityPeriod"):
        ValidityPeriod.objects.create(
            situation=situation, period=get_period(period_element)
        )

    for i, consequence_element in enumerate(item.find("Consequences")):
        consequence = Consequence(situation=situation)
        if not created and i == 0:
            try:
                consequence = situation.consequence_set.get()
            except Consequence.MultipleObjectsReturned:
                situation.consequence_set.all().delete()
            except Consequence.DoesNotExist:
                pass

        consequence.text = consequence_element.find("Advice/Details").text
        consequence.data = ET.tostring(consequence_element, encoding="unicode")
        consequence.save()

        stops = consequence_element.findall("Affects/StopPoints/AffectedStopPoint")
        stops = [stop.find("StopPointRef").text for stop in stops]
        stops = StopPoint.objects.filter(atco_code__in=stops)
        consequence.stops.set(stops)

        consequence.services.clear()

        services = Service.objects.filter(current=True)
        stops_filter = Q(stops__in=stops)

        for line in consequence_element.findall(
            "Affects/Networks/AffectedNetwork/AffectedLine"
        ):
            line_name = line.findtext("PublishedLineName") or line.findtext("LineRef")
            line_name = line_name.replace("_", " ")
            line_filter = Q(route__line_name__iexact=line_name) | Q(
                line_name__iexact=line_name
            )
            for operator_ref in line.findall("AffectedOperator/OperatorRef"):
                operator_ref = operator_ref.text

                matching_services = services.filter(
                    line_filter, operator__in=get_operators(operator_ref)
                ).distinct()
                if len(matching_services) > 1:
                    matching_services = matching_services.filter(stops_filter)

                if matching_services:
                    consequence.services.add(*matching_services)
                else:
                    logger.info(f"{situation_number=} {operator_ref=} {line_name=}")

        for operator in consequence_element.findall(
            "Affects/Operators/AffectedOperator"
        ):
            operator_ref = operator.findtext("OperatorRef")
            try:
                consequence.operators.add(*get_operators(operator_ref))
            except Operator.DoesNotExist:
                logger.exception("operator %s does not exist", operator_ref)

    return situation.id


def get_situation_elements(open_file):
    for _, element in ET.iterparse(open_file):
        if element.tag[:29] == "{http://www.siri.org.uk/siri}":
            element.tag = element.tag[29:]

        if element.tag.endswith("PtSituationElement"):
            yield element


def bods_disruptions():
    url = "https://data.bus-data.dft.gov.uk/disruptions/download/bulk_archive"

    situations = []

    response = requests.get(url, timeout=61)
    response.raise_for_status()
    archive = zipfile.ZipFile(io.BytesIO(response.content))

    namelist = archive.namelist()
    assert len(namelist) == 1
    open_file = archive.open(namelist[0])

    elements = list(get_situation_elements(open_file))

    participant_refs = {element.findtext("ParticipantRef") for element in elements}
    sources = {
        participant_ref: DataSource.objects.get_or_create(
            name=f"Bus Open Data ({participant_ref})"
        )[0]
        for participant_ref in participant_refs
    }

    situation_numbers = (element.findtext("SituationNumber") for element in elements)

    # not scoped to this run's sources: a situation seen before each
    # ParticipantRef got its own source is still out there under the old
    # shared "Bus Open Data" source (or whatever source it was last
    # migrated to), and needs to be found so it gets migrated onto the
    # right source rather than duplicated under it
    current_situations = {
        s.situation_number: s
        for s in Situation.objects.filter(situation_number__in=situation_numbers)
    }

    for element in elements:
        situations.append(handle_item(element, sources, current_situations))

    stale_sources = set(sources.values())
    if legacy_source := DataSource.objects.filter(name="Bus Open Data").first():
        stale_sources.add(legacy_source)

    for source in stale_sources:
        source.situation_set.filter(current=True).exclude(id__in=situations).update(
            current=False
        )
