import contextlib

from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, transaction
from django.db.models import Exists, OuterRef

from accounts.models import InvitationOperator, OperatorUser
from bustimes.models import Garage, Trip, TimetableDataSourceOperator
from disruptions.models import ConsequenceOperator
from fares.models import DataSetOperator, TariffOperator
from vehicles.models import Vehicle, VehicleCode

from ...models import (
    DataSource,
    Operator,
    OperatorCode,
    OperatorLicence,
    OperatorPaymentMethod,
    OperatorRegion,
    OperatorSibling,
    ServiceOperator,
)

# (model, other fields) for each through model linking something to an Operator,
# all with unique_together on ("operator",) + other fields
THROUGH_MODELS = [
    (OperatorRegion, ("region",)),
    (OperatorLicence, ("licence",)),
    (OperatorPaymentMethod, ("paymentmethod",)),
    (OperatorCode, ("code", "source")),
    (ServiceOperator, ("service",)),
    (TimetableDataSourceOperator, ("timetabledatasource",)),
    (DataSetOperator, ("dataset",)),
    (TariffOperator, ("tariff",)),
    (ConsequenceOperator, ("consequence",)),
    (InvitationOperator, ("invitation",)),
    (OperatorUser, ("user",)),
]


class Command(BaseCommand):
    """Merges one or more operators into another operator,
    moving across their services, vehicles, trips, codes etc,
    then deleting the merged-away operators
    (preserving their old slugs as redirects to the surviving operator,
    the same way ServiceAdmin.merge preserves old service slugs)"""

    help = __doc__

    def add_arguments(self, parser):
        parser.add_argument(
            "sources", nargs="+", help="NOC(s) of the operator(s) to merge away"
        )
        parser.add_argument("--into", required=True, help="NOC of the surviving operator")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="show what would happen, then roll back",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help=(
                "if a source operator has a vehicle with the same code as one "
                "the target operator already has (vehicle codes must be unique "
                "per operator), merge the two vehicles (combining their "
                "journeys/history, the same way VehicleAdmin.merge_all_selected "
                "does) instead of aborting"
            ),
        )

    def merge_vehicles(self, keep, duplicate):
        """Merge two Vehicle rows that turn out to represent the same
        physical vehicle (same code, once under the same operator),
        the same way VehicleAdmin.merge_all_selected does"""
        duplicate.vehiclejourney_set.update(vehicle=keep)
        duplicate.vehiclecode_set.update(vehicle=keep)
        duplicate.vehiclerevision_set.update(vehicle=keep)

        with contextlib.suppress(IntegrityError):
            VehicleCode.objects.create(vehicle=keep, scheme="slug", code=duplicate.slug)

        if not keep.latest_journey_id or (
            duplicate.latest_journey_id
            and duplicate.latest_journey_id > keep.latest_journey_id
        ):
            keep.latest_journey_id = duplicate.latest_journey_id

        if keep.withdrawn and not duplicate.withdrawn:
            keep.fleet_code = duplicate.fleet_code
            keep.fleet_number = duplicate.fleet_number
            keep.withdrawn = False

        duplicate.delete()
        keep.save(
            update_fields=["fleet_code", "fleet_number", "withdrawn", "latest_journey"]
        )

    def merge_through(self, model, other_fields, source, target):
        """Move rows of a through model from one operator to another,
        skipping (and so leaving to cascade-delete along with the source
        operator) any that would duplicate a row the target already has"""
        dup_exists = Exists(
            model.objects.filter(
                operator=target,
                **{field: OuterRef(field) for field in other_fields},
            )
        )
        model.objects.filter(operator=source).filter(~dup_exists).update(
            operator=target
        )

    @transaction.atomic
    def handle(self, sources, into, dry_run, force, **options):
        try:
            target = Operator.objects.get(noc=into)
        except Operator.DoesNotExist:
            raise CommandError(f"no operator with noc {into!r}")

        if into in sources:
            raise CommandError("--into operator cannot also be a source operator")

        source_nocs = sources
        sources = list(Operator.objects.filter(noc__in=source_nocs))
        missing = set(source_nocs) - {operator.noc for operator in sources}
        if missing:
            raise CommandError(f"no operator(s) with noc {missing!r}")

        slug_source, _ = DataSource.objects.get_or_create(name="slug")
        source_descriptions = [repr(source) for source in sources]

        for source in sources:
            self.stdout.write(f"merging {source!r} into {target!r}")

            for model, other_fields in THROUGH_MODELS:
                self.merge_through(model, other_fields, source, target)

            # self-referential siblings, excluding any that would become self-links
            OperatorSibling.objects.filter(from_operator=source).exclude(
                to_operator=target
            ).filter(
                ~Exists(
                    OperatorSibling.objects.filter(
                        from_operator=target, to_operator=OuterRef("to_operator")
                    )
                )
            ).update(from_operator=target)
            OperatorSibling.objects.filter(to_operator=source).exclude(
                from_operator=target
            ).filter(
                ~Exists(
                    OperatorSibling.objects.filter(
                        to_operator=target, from_operator=OuterRef("from_operator")
                    )
                )
            ).update(to_operator=target)

            # vehicle codes must be unique per operator, so a source vehicle
            # might collide with one the target already has
            target_codes = {
                code.upper()
                for code in Vehicle.objects.filter(operator=target).values_list(
                    "code", flat=True
                )
            }
            conflicting = [
                vehicle
                for vehicle in Vehicle.objects.filter(operator=source)
                if vehicle.code.upper() in target_codes
            ]
            if conflicting and not force:
                codes = ", ".join(vehicle.code for vehicle in conflicting)
                raise CommandError(
                    f"{target!r} already has vehicle(s) with the same code as "
                    f"{source!r}'s vehicle(s) {codes!r} - pass --force to merge "
                    "the conflicting vehicles instead of aborting"
                )
            Vehicle.objects.filter(operator=source).exclude(
                id__in=[vehicle.id for vehicle in conflicting]
            ).update(operator=target)
            for vehicle in conflicting:
                existing = Vehicle.objects.get(operator=target, code__iexact=vehicle.code)
                self.merge_vehicles(existing, vehicle)

            # straightforward foreign keys - no uniqueness constraints to worry about
            Trip.objects.filter(operator=source).update(operator=target)
            Garage.objects.filter(operator=source).update(operator=target)

            # preserve the old operator's slug as a redirect to the target
            if source.slug and not OperatorCode.objects.filter(
                source=slug_source, code=source.slug, operator=target
            ).exists():
                OperatorCode.objects.create(
                    source=slug_source, code=source.slug, operator=target
                )

            if dry_run:
                self.stdout.write(
                    f"  (dry run) would delete {source!r}, "
                    "cascading to any remaining duplicate links"
                )
            else:
                source.delete()

        if dry_run:
            self.stdout.write("dry run - rolling back")
            transaction.set_rollback(True)
        else:
            self.stdout.write(
                self.style.SUCCESS(f"merged {source_descriptions} into {target!r}")
            )
