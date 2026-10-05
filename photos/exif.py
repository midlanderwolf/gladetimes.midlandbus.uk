from datetime import UTC, datetime
from math import isfinite

from django.contrib.gis.geos import Point
from PIL import ExifTags, Image
from PIL.TiffImagePlugin import IFDRational

# tags whose values are big/binary and not worth keeping, or that are just an
# offset to a sub-IFD, which is merged in separately
IGNORED_TAGS = {
    "MakerNote",
    "UserComment",
    "PrintImageMatching",
    "GPSInfo",
    "ExifOffset",
}


def jsonable(value):
    if isinstance(value, IFDRational):
        value = float(value)
        return value if isfinite(value) else None
    if isinstance(value, bytes):
        return None
    if isinstance(value, tuple):
        return [jsonable(item) for item in value]
    if isinstance(value, str):
        value = value.replace("\x00", "").strip()
        return value or None
    return value


def get_gps_location(gps_ifd):
    lat = gps_ifd.get(2)
    lat_ref = gps_ifd.get(1)
    lon = gps_ifd.get(4)
    lon_ref = gps_ifd.get(3)
    if not (lat and lon and lat_ref and lon_ref):
        return None

    degrees, minutes, seconds = (float(part) for part in lat)
    lat = degrees + minutes / 60 + seconds / 3600
    if lat_ref == "S":
        lat = -lat

    degrees, minutes, seconds = (float(part) for part in lon)
    lon = degrees + minutes / 60 + seconds / 3600
    if lon_ref == "W":
        lon = -lon

    return Point(lon, lat, srid=4326)


def get_date_taken(value):
    """EXIF datetimes have no timezone - treat as UTC, for want of anything better"""
    try:
        return datetime.strptime(value, "%Y:%m:%d %H:%M:%S").replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return None


def get_exif(image: Image.Image):
    """Given a PIL Image, return (metadata dict, location or None, date taken or None)"""
    exif = image.getexif()

    metadata = {}
    for ifd in (exif, exif.get_ifd(ExifTags.IFD.Exif)):
        for tag_id, value in ifd.items():
            tag = ExifTags.TAGS.get(tag_id, tag_id)
            if tag in IGNORED_TAGS:
                continue
            value = jsonable(value)
            if value is not None:
                metadata[tag] = value

    location = None
    gps_ifd = exif.get_ifd(ExifTags.IFD.GPSInfo)
    if gps_ifd:
        location = get_gps_location(gps_ifd)

    date_taken = get_date_taken(metadata.get("DateTimeOriginal"))

    return metadata, location, date_taken
