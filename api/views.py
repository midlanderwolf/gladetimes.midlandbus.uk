import logging
from django_filters.rest_framework import DjangoFilterBackend
from datetime import datetime, timedelta
from rest_framework import pagination, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import APIException
from rest_framework.response import Response
from django.contrib.postgres.aggregates import ArrayAgg
from django.db.models import Q
from django.db.models.functions import Coalesce
from django.utils import timezone

import numpy as np

from vehicles.time_aware_polyline import encode_time_aware_polyline

from busstops.models import Operator, Service, StopPoint
from bustimes.models import StopTime, Trip
from bustimes.utils import contiguous_stoptimes_only
from vehicles.models import (
    Livery,
    Vehicle,
    VehicleJourney,
    VehicleLocation,
    VehicleType,
)
from vehicles.utils import redis_client
from vehicles.views import get_vehicle_locations

from sql_util.utils import Exists
from haversine import Unit, haversine_vector

from . import filters, serializers


class BadException(APIException):
    status_code = 400


class LimitOffsetPagination(pagination.LimitOffsetPagination):
    max_limit = 1000


class CursorPagination(pagination.CursorPagination):
    ordering = "-pk"
    page_size = 100


class CursorPaginationWithSmallerPageSize(CursorPagination):
    page_size = 10


class VehicleViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = (
        Vehicle.objects.select_related("vehicle_type", "livery", "operator", "garage")
        .annotate(
            special_features=ArrayAgg("features__name", filter=~Q(features=None)),
        )
        .order_by("id")
    )
    serializer_class = serializers.VehicleSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_class = filters.VehicleFilter
    pagination_class = LimitOffsetPagination


class LiveryViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Livery.objects.order_by("id")
    serializer_class = serializers.LiverySerializer
    filter_backends = [DjangoFilterBackend]
    filterset_class = filters.LiveryFilter


class VehicleTypeViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = VehicleType.objects.all()
    serializer_class = serializers.VehicleTypeSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_class = filters.VehicleTypeFilter


class OperatorViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = (
        Operator.objects.filter(
            Exists("vehicle") | Exists("service", filter=Q(service__current=True))
        )
        .order_by("noc")
        .defer("address", "email", "phone", "search_vector")
    )
    serializer_class = serializers.OperatorSerializer
    pagination_class = CursorPagination
    filter_backends = [DjangoFilterBackend]
    filterset_class = filters.OperatorFilter


class ServiceViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Service.objects.filter(current=True).prefetch_related("operator")
    serializer_class = serializers.ServiceSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_class = filters.ServiceFilter


class StopViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = (
        StopPoint.objects.order_by("atco_code")
        .select_related("locality")
        .annotate(
            line_names=ArrayAgg(
                "stopusage__line_name",
                filter=Q(stopusage__service__current=True),
                distinct=True,
                default=None,
            )
        )
    )
    serializer_class = serializers.StopSerializer
    pagination_class = CursorPagination
    filter_backends = [DjangoFilterBackend]
    filterset_class = filters.StopFilter


class TripViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = (
        Trip.objects.select_related("route__service", "operator")
        .prefetch_related("notes")
        .annotate(
            destination_name=Coalesce(
                "headsign", "destination__locality__name", "destination__common_name"
            )
        )
    )
    serializer_class = serializers.TripSerializer
    pagination_class = CursorPagination
    filter_backends = [DjangoFilterBackend]
    filterset_class = filters.TripFilter

    @staticmethod
    def get_stops(obj):
        trips = obj.get_trips()
        stops = (
            StopTime.objects.filter(trip__in=trips)
            .select_related("stop__locality")
            .defer(
                "stop__search_vector",
                "stop__locality__search_vector",
                "stop__locality__latlong",
            )
            .order_by("trip__start", "id")
            # .annotate(
            #     call_condition=Subquery(
            #         Call.objects.filter(
            #             stop_time=OuterRef("id"),
            #             journey__trip=OuterRef("trip"),
            #             journey__situation__current=True,
            #         ).values("condition")[:1]
            #     )
            # )
        )
        if obj.notes.all():
            stops = stops.annotate(note_codes=ArrayAgg("notes__code"))
        if len(trips) > 1:
            stops = contiguous_stoptimes_only(stops, obj.id)
        return stops

    def get_object(self):
        obj = super().get_object()
        obj.stops = self.get_stops(obj)
        return obj


class VehicleJourneyViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = VehicleJourney.objects.select_related("vehicle")
    serializer_class = serializers.VehicleJourneySerializer
    pagination_class = CursorPaginationWithSmallerPageSize
    filter_backends = [DjangoFilterBackend]
    filterset_class = filters.VehicleJourneyFilter

    def get_queryset(self):
        qs = super().get_queryset()
        if self.action == "details":
            qs = qs.select_related("service", "trip__route__service", "trip__operator")
        return qs

    @staticmethod
    def set_actual_departure_times(stop_times, locations):
        stops = [st for st in stop_times if st.stop and st.stop.latlong]
        if not stops:
            return

        stop_coords = [(st.stop.latlong.y, st.stop.latlong.x) for st in stops]
        vehicle_coords = [
            (loc["coordinates"][1], loc["coordinates"][0]) for loc in locations
        ]
        stop_headings = np.array(
            [
                st.stop.get_heading() if st.stop.get_heading() is not None else np.nan
                for st in stops
            ],
            dtype=float,
        )
        try:
            haversine_vector_results = haversine_vector(
                stop_coords, vehicle_coords, Unit.METERS, comb=True
            )
        except ValueError as e:
            logging.exception(e)
            return

        for distances, location in zip(haversine_vector_results, locations):
            vehicle_heading = location.get("direction")
            if vehicle_heading is not None:
                heading_diff = np.abs(
                    ((stop_headings - vehicle_heading) + 180) % 360 - 180
                )
                aligned = np.isnan(heading_diff) | (heading_diff < 90)
                if aligned.any():
                    idx = int(np.argmin(np.where(aligned, distances, np.inf)))
                else:
                    idx = int(np.argmin(distances))
            else:
                idx = int(np.argmin(distances))

            if distances[idx] < 100:
                stops[idx].actual_departure_time = location["datetime"]

    def trip_from_siri(self, instance, locations):
        try:
            mvj = instance.vehicle.latest_journey_data["MonitoredVehicleJourney"]
            origin_ref = mvj["OriginRef"].upper()
            dest_ref = mvj["DestinationRef"].upper()
            stops = {
                stop.atco_code.upper(): stop
                for stop in StopPoint.objects.filter(
                    Q(atco_code__iexact=origin_ref) | Q(atco_code__iexact=dest_ref)
                )
            }
        except (KeyError, ValueError, TypeError):
            return

        origin = stops.get(origin_ref) or StopPoint(common_name=mvj.get("OriginName"))
        dest = stops.get(dest_ref) or StopPoint(common_name=mvj.get("DestinationName"))

        if start := mvj.get("OriginAimedDepartureTime"):
            start = timezone.localtime(datetime.fromisoformat(start))
            start = timedelta(hours=start.hour, minutes=start.minute)
        if end := mvj.get("DestinationAimedArrivalTime"):
            end = timezone.localtime(datetime.fromisoformat(end))
            end = timedelta(hours=end.hour, minutes=end.minute)

        trip = Trip(start=start, end=end, operator=instance.vehicle.operator)
        trip.stops = [
            StopTime(stop=origin, departure=start, timing_point=True),
            StopTime(stop=dest, arrival=end, timing_point=True),
        ]
        return trip

    @action(detail=True)
    def details(self, request, pk=None, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance)

        extra_data = {}

        tzinfo = (
            instance.trip and instance.trip.route and instance.trip.route.timezone
        ) or None

        locations = []
        if redis_client:
            raw_locations = redis_client.lrange(instance.get_redis_key(), 0, -1)
            locations = [
                VehicleLocation.decode_appendage(loc, tzinfo) for loc in raw_locations
            ]
            locations.sort(key=lambda loc: loc["datetime"])

            filtered = []
            stationary = False
            previous = None
            previous_coords = None
            for location in locations:
                coords = location["coordinates"]
                if previous_coords:
                    dx = coords[0] - previous_coords[0]
                    dy = coords[1] - previous_coords[1]
                    if dx * dx + dy * dy < 2.5e-7:  # 0.0005 degrees squared
                        stationary = True
                    elif stationary:
                        filtered.append(previous)
                        stationary = False
                if not stationary:
                    filtered.append(location)
                    previous_coords = coords
                previous = location
            if stationary:
                filtered.append(location)
            locations = filtered

            polyline = encode_time_aware_polyline(
                [
                    [
                        loc["coordinates"][0],
                        loc["coordinates"][1],
                        int(loc["datetime"].timestamp()),
                    ]
                    for loc in locations
                ]
            )
            extra_data["time_aware_polyline"] = polyline

        if instance.service_id:
            extra_data["service"] = {
                "id": instance.service_id,
                "slug": instance.service.slug,
            }

        current_trip = (
            instance.vehicle_id and instance.id == instance.vehicle.latest_journey_id
        )
        if locations and current_trip:
            if not instance.trip:
                instance.trip = self.trip_from_siri(instance, locations)

        if instance.trip:
            instance.trip.destination_name = None
            if instance.trip.id:
                instance.trip.stops = list(TripViewSet.get_stops(instance.trip))
            if locations:
                self.set_actual_departure_times(instance.trip.stops, locations)
            trip_serializer = serializers.TripSerializer(
                instance.trip, context={"include_track": False}
            )
            extra_data["trip"] = trip_serializer.data

        if locations and (current_trip or not instance.vehicle_id):
            if instance.service_id:
                params = {
                    "service_ids": [instance.service_id],
                    "trip_id": instance.trip_id,
                }
            else:
                params = {"vehicle_ids": [instance.vehicle_id or instance.id]}
            if instance.trip:
                params["trip_id"] = instance.trip_id
                params["stop_times"] = instance.trip.stops
            live = get_vehicle_locations(**params, tzinfo=tzinfo)
            # check that this journey is actually tracking (not an old journey)
            if live and any(instance.id == item["journey_id"] for item in live):
                extra_data["live"] = live

        if not instance.trip and instance.vehicle.operator:
            extra_data["operator"] = {
                "noc": instance.vehicle.operator.noc,
                "slug": instance.vehicle.operator.slug,
                "name": instance.vehicle.operator.name,
            }

        if instance.vehicle_id:
            next_previous_filter = {
                "date": instance.date,
                "vehicle_id": instance.vehicle_id,
            }
            try:
                next_journey = instance.get_next_by_datetime(**next_previous_filter)
            except VehicleJourney.DoesNotExist:
                pass
            else:
                extra_data["next"] = {
                    "id": next_journey.id,
                    "datetime": timezone.localtime(next_journey.datetime, tzinfo),
                }
            try:
                previous_journey = instance.get_previous_by_datetime(
                    **next_previous_filter
                )
            except VehicleJourney.DoesNotExist:
                pass
            else:
                extra_data["previous"] = {
                    "id": previous_journey.id,
                    "datetime": timezone.localtime(previous_journey.datetime, tzinfo),
                }

        return Response(serializer.data | extra_data)
