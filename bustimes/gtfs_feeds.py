from dataclasses import dataclass


@dataclass(frozen=True)
class GTFSFeed:
    source_name: str
    noc: str
    timezone: str
    operator_name: str = ""  # defaults to agency.txt's agency_name if blank
    static_url: str = ""
    # filename of a GTFS zip already sitting in DATA_DIR, for feeds with no
    # static_url to download from (e.g. licensed data that can't leave this
    # server) - DATA_DIR is gitignored, so files placed there are never
    # committed. Used instead of static_url.
    static_path: str = ""
    vehicle_positions_url: str = ""
    trip_updates_url: str = ""
    alerts_url: str = ""
    stop_id_prefix: str = ""
    vehicle_code_scheme: str = ""  # defaults to source_name if blank
    multi_operator: bool = False  # import/track one Operator per agency in
    # agency.txt (keyed by agency_noc, or agency_id if blank), instead of a
    # single Operator keyed by `noc`
    alert_ids_change: bool = False  # entity ids are different in every response
    skip_alert_headers: tuple = ()
    town_in_stop_names: bool = False  # e.g. "VALOGNES Le Gravier"
    stop_ids_are_uuids: bool = False  # strip dashes so atco_code fits in 36 chars
    # ignore the feed's route_long_name and always derive it from the first
    # and last stop names of a representative trip (e.g. for feeds whose
    # route_long_name is unhelpful, like headcodes)
    overwrite_route_long_name: bool = False
    # extra intermediate certificate(s) (paths relative to bustimes/certs/)
    # to trust, for servers that fail to send their full certificate chain
    extra_ca_certs: tuple = ()


FEEDS = {
    "rtcsnv": GTFSFeed(
        source_name="RTCSNV",
        noc="RTCSNV",
        timezone="America/Los_Angeles",
        static_url="https://developer.rtcsnv.com/transitData/google_transit.zip",
        vehicle_positions_url="https://developer.rtcsnv.com/transitData/vehiclePositions.pb",
        trip_updates_url="https://developer.rtcsnv.com/transitData/tripUpdates.pb",
        stop_id_prefix="rtcsnv-",
    ),
    "zditm": GTFSFeed(
        source_name="ZDiTM Szczecin",
        noc="ZDITM",
        timezone="Europe/Warsaw",
        static_url="https://www.zditm.szczecin.pl/storage/gtfs/gtfs.zip",
        vehicle_positions_url="https://www.zditm.szczecin.pl/storage/gtfs/gtfs-rt-vehicles.pb",
        trip_updates_url="https://www.zditm.szczecin.pl/storage/gtfs/gtfs-rt-trips.pb",
        alerts_url="https://www.zditm.szczecin.pl/storage/gtfs/gtfs-rt-alerts.pb",
        stop_id_prefix="zditm-",
    ),
    "capcotentin": GTFSFeed(
        source_name="Cap Cotentin",
        noc="CAPCOT",
        timezone="Europe/Paris",
        static_url="https://www.data.gouv.fr/api/1/datasets/r/2e97c9b3-a59f-42dd-9b9e-a232fa771f21",
        vehicle_positions_url="https://app.pysae.com/api/v2/groups/transdev-cotentin/gtfs-rt",
        trip_updates_url="https://app.pysae.com/api/v2/groups/transdev-cotentin/gtfs-rt",
        stop_id_prefix="capcotentin-",
        town_in_stop_names=True,
    ),
    "marta": GTFSFeed(
        source_name="MARTA",
        noc="MARTA",
        timezone="America/New_York",
        static_url="https://itsmarta.com/google_transit_feed/google_transit.zip",
        vehicle_positions_url="https://gtfs-rt.itsmarta.com/TMGTFSRealTimeWebService/vehicle/vehiclepositions.pb",
        trip_updates_url="https://gtfs-rt.itsmarta.com/TMGTFSRealTimeWebService/tripupdate/tripupdates.pb",
        stop_id_prefix="marta-",
    ),
    "go_metro": GTFSFeed(
        source_name="Go Metro",
        noc="GM",
        timezone="America/New_York",
        static_url="https://www.go-metro.com/wp-content/uploads/2024/12/google_transit-1.zip",
        vehicle_positions_url="https://tmgtfsprd.sorttrpcloud.com/TMGTFSRealTimeWebService/vehicle/vehiclepositions.pb",
        trip_updates_url="https://tmgtfsprd.sorttrpcloud.com/TMGTFSRealTimeWebService/tripupdate/tripupdates.pb",
        alerts_url="https://tmgtfsprd.sorttrpcloud.com/TMGTFSRealTimeWebService/alert/alerts.pb",
        stop_id_prefix="go metro-",
        vehicle_code_scheme="GM",
    ),
    "hsl": GTFSFeed(
        source_name="HSL",
        noc="HSL",
        timezone="Europe/Helsinki",
        static_url="https://infopalvelut.storage.hsldev.com/gtfs/hsl.zip",
        vehicle_positions_url="https://realtime.hsl.fi/realtime/vehicle-positions/v2/hsl",
        trip_updates_url="https://realtime.hsl.fi/realtime/trip-updates/v2/hsl",
        alerts_url="https://realtime.hsl.fi/realtime/service-alerts/v2/hsl",
        stop_id_prefix="hsl-",
    ),
    "ztmwarszawa": GTFSFeed(
        source_name="ZTM Warszawa",
        noc="ZTM",
        timezone="Europe/Warsaw",
        static_url="https://mkuran.pl/gtfs/warsaw.zip",
        vehicle_positions_url="https://mkuran.pl/gtfs/warsaw/vehicles.pb",
        alerts_url="https://mkuran.pl/gtfs/warsaw/alerts.pb",
        stop_id_prefix="ztm-",
    ),
    "emtmadrid": GTFSFeed(
        source_name="EMT Madrid",
        noc="EMTMADRID",
        timezone="Europe/Madrid",
        static_url="https://servicios.emtmadrid.es:8443/gtfs/transitemt.zip",
        vehicle_positions_url="",
        alerts_url="https://openapi.emtmadrid.es/v1/bus/servicealerts/proto",
        stop_id_prefix="emtmadrid-",
    ),
    "renfe": GTFSFeed(
        source_name="Renfe",
        noc="RENFE",
        timezone="Europe/Madrid",
        static_url="https://www.data.gouv.fr/api/1/datasets/r/eae0fa46-087a-4018-ada9-d8add124e635",
        vehicle_positions_url="https://www.data.gouv.fr/api/1/datasets/r/cb62a16d-f879-44e6-8e8b-8eafded361b7",
        trip_updates_url="https://www.data.gouv.fr/api/1/datasets/r/db9c89fb-c174-4c38-b861-5c51cc1d8856",
        stop_id_prefix="renfe-",
    ),
    "flixbus-us": GTFSFeed(
        source_name="FlixBus US",
        noc="FLIXBUS",
        timezone="UTC",
        static_url="http://gtfs.gis.flix.tech/gtfs_generic_us.zip",
        stop_id_prefix="fxb-",
        stop_ids_are_uuids=True,
        multi_operator=True,
    ),
    "titsa": GTFSFeed(
        source_name="titsa",
        noc="TITSA",
        timezone="Atlantic/Canary",
        static_url="http://www.titsa.com/Google_transit.zip",
        stop_id_prefix="titsa-",
        # titsa.com's server doesn't send its intermediate certificate
        extra_ca_certs=("titsa_intermediate.pem",),
    ),
    # static data and vehicle positions have their own importers
    "ember": GTFSFeed(
        source_name="Ember",
        noc="EMBR",
        timezone="Europe/London",
        trip_updates_url="https://api.ember.to/v1/gtfs/realtime/",
        alerts_url="https://api.ember.to/v1/gtfs/realtime/",
        stop_id_prefix="ember-",
        alert_ids_change=True,
        skip_alert_headers=("Pre-booking",),  # imported as timetable notes
    ),
}
