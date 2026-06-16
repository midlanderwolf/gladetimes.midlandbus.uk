import logging

import requests
from django.core.management.base import BaseCommand
from django.db import transaction

from busstops.models import Operator
from bustimes.models import Garage
from vehicles.models import Livery, Vehicle, VehicleFeature, VehicleType

logger = logging.getLogger(__name__)

API_BASE = "https://bustimes.org/api"

session = requests.Session()
session.headers.update({"User-Agent": "bustimes.org import/1.0"})


def fetch_all(url):
    while url:
        response = session.get(url, timeout=30)
        response.raise_for_status()
        data = response.json()
        yield from data["results"]
        url = data.get("next")


class Command(BaseCommand):
    def handle(self, **options):
        self.import_vehicle_types()
        self.import_liveries()
        self.import_vehicles()

    def import_vehicle_types(self):
        count = 0
        for item in fetch_all(f"{API_BASE}/vehicletypes/"):
            VehicleType.objects.update_or_create(
                name=item["name"],
                defaults={"style": item["style"], "fuel": item["fuel"]},
            )
            count += 1
        self.stdout.write(f"Imported {count} vehicle types")

    def import_liveries(self):
        count = 0
        for item in fetch_all(f"{API_BASE}/liveries/"):
            Livery.objects.update_or_create(
                id=item["id"],
                defaults={
                    "name": item["name"],
                    "left_css": item.get("left_css", ""),
                    "right_css": item.get("right_css", ""),
                    "white_text": item.get("white_text", False),
                    "text_colour": item.get("text_colour", ""),
                    "stroke_colour": item.get("stroke_colour", ""),
                    "published": True,
                },
            )
            count += 1
        self.stdout.write(f"Imported {count} liveries")

    def import_vehicles(self):
        self.stdout.write("Loading reference data...")
        type_by_name = {t.name: t for t in VehicleType.objects.all()}
        livery_by_id = {l.id: l for l in Livery.objects.all()}
        operator_by_noc = {o.noc: o for o in Operator.objects.all()}
        garage_index = self._build_garage_index()
        feature_by_name = {f.name: f for f in VehicleFeature.objects.all()}

        created = 0
        updated = 0
        errors = 0
        batch = []
        features_batch = []

        for item in fetch_all(f"{API_BASE}/vehicles/"):
            try:
                vehicle, features = self._build_vehicle(
                    item, type_by_name, livery_by_id, operator_by_noc, garage_index, feature_by_name
                )
                batch.append(vehicle)
                if features:
                    features_batch.append((item["slug"], features))
            except Exception:
                logger.exception(f"Error building vehicle {item.get('id')}")
                errors += 1

            if len(batch) >= 500:
                c, u = self._save_batch(batch, features_batch)
                created += c
                updated += u
                batch = []
                features_batch = []
                self.stdout.write(f"Progress: {created + updated} vehicles")

        if batch:
            c, u = self._save_batch(batch, features_batch)
            created += c
            updated += u

        self.stdout.write(
            f"Imported {created + updated} vehicles ({created} created, {updated} updated, {errors} errors)"
        )

    @staticmethod
    def _build_garage_index():
        index = {}
        for garage in Garage.objects.select_related("operator").all():
            key = (garage.code, garage.operator_id)
            if key not in index:
                index[key] = garage
        return index

    @staticmethod
    def _build_vehicle(item, type_by_name, livery_by_id, operator_by_noc, garage_index, feature_by_name):
        slug = item["slug"]

        vehicle_type = None
        vt_data = item.get("vehicle_type")
        if vt_data and vt_data.get("name"):
            vehicle_type = type_by_name.get(vt_data["name"])

        livery = None
        livery_data = item.get("livery")
        if livery_data and livery_data.get("id"):
            livery = livery_by_id.get(livery_data["id"])

        operator = None
        op_data = item.get("operator")
        if op_data and op_data.get("id"):
            operator = operator_by_noc.get(op_data["id"])

        garage = None
        garage_data = item.get("garage")
        if garage_data and garage_data.get("code"):
            code = garage_data["code"]
            op_noc = operator.noc if operator else None
            garage = garage_index.get((code, op_noc))

        data = {}
        if item.get("previous_reg"):
            data["Previous reg"] = item["previous_reg"]

        features = []
        for feature_name in item.get("special_features") or []:
            if feature_name not in feature_by_name:
                feature_by_name[feature_name] = VehicleFeature.objects.create(name=feature_name)
            features.append(feature_by_name[feature_name])

        return Vehicle(
            slug=slug,
            code=item.get("fleet_code") or item.get("reg", "").upper().replace(" ", "") or (slug.split("-", 1)[-1] if "-" in slug else slug),
            fleet_number=item.get("fleet_number"),
            fleet_code=item.get("fleet_code") or "",
            reg=item.get("reg") or "",
            vehicle_type=vehicle_type,
            livery=livery,
            operator=operator,
            garage=garage,
            branding=item.get("branding", ""),
            name=item.get("name", ""),
            notes=item.get("notes", ""),
            withdrawn=item.get("withdrawn", False),
            data=data or None,
        ), features

    @staticmethod
    @transaction.atomic
    def _save_batch(batch, features_batch):
        slugs = [v.slug for v in batch]
        existing = {v.slug: v for v in Vehicle.objects.filter(slug__in=slugs)}

        to_create = []
        to_update = []

        for vehicle in batch:
            if vehicle.slug in existing:
                existing_obj = existing[vehicle.slug]
                vehicle.id = existing_obj.id
                to_update.append(vehicle)
            else:
                to_create.append(vehicle)

        if to_create:
            Vehicle.objects.bulk_create(to_create, ignore_conflicts=True)
        if to_update:
            Vehicle.objects.bulk_update(
                to_update,
                [
                    "code",
                    "fleet_number",
                    "fleet_code",
                    "reg",
                    "vehicle_type",
                    "livery",
                    "operator",
                    "garage",
                    "branding",
                    "name",
                    "notes",
                    "withdrawn",
                    "data",
                ],
            )

        if features_batch:
            slug_to_vehicle = {v.slug: v for v in batch if v.id}
            for slug, features in features_batch:
                vehicle = slug_to_vehicle.get(slug)
                if vehicle:
                    vehicle.features.set(features)

        return len(to_create), len(to_update)
