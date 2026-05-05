"""Utilities for GTFS imports"""

import logging
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

# Mapping from timezone to 2-letter country/region code
TIMEZONE_TO_REGION = {
    "Europe/London": "GB",
    "Europe/Dublin": "IE",
    "Europe/Helsinki": "FI",
    "Europe/Warsaw": "PL",
    "Europe/Berlin": "DE",
    "Europe/Paris": "FR",
    "Europe/Madrid": "ES",
    "Atlantic/Canary": "ES",
    "Europe/Rome": "IT",
    "Europe/Amsterdam": "NL",
    "Europe/Brussels": "BE",
    "Europe/Vienna": "AT",
    "Europe/Zurich": "CH",
    "Europe/Stockholm": "SE",
    "Europe/Oslo": "NO",
    "Europe/Copenhagen": "DK",
    "Europe/Prague": "CZ",
    "Europe/Budapest": "HU",
    "Europe/Bucharest": "RO",
    "Europe/Sofia": "BG",
    "Europe/Zagreb": "HR",
    "Europe/Belgrade": "RS",
    "Europe/Ljubljana": "SI",
    "Europe/Bratislava": "SK",
    "Europe/Vilnius": "LT",
    "Europe/Riga": "LV",
    "Europe/Tallinn": "EE",
    "Europe/Athens": "GR",
    "Europe/Lisbon": "PT",
    "America/New_York": "US",
    "America/Chicago": "US",
    "America/Denver": "US",
    "America/Los_Angeles": "US",
    "America/Toronto": "CA",
    "America/Vancouver": "CA",
    "Australia/Sydney": "AU",
    "Australia/Melbourne": "AU",
    "Pacific/Auckland": "NZ",
    "Asia/Tokyo": "JP",
    "Asia/Shanghai": "CN",
    "Asia/Singapore": "SG",
    "Asia/Dubai": "AE",
}

# Mapping from country name to 2-letter code
COUNTRY_TO_REGION = {
    "United Kingdom": "GB",
    "Ireland": "IE",
    "Finland": "FI",
    "Poland": "PL",
    "Germany": "DE",
    "France": "FR",
    "Spain": "ES",
    "Italy": "IT",
    "Netherlands": "NL",
    "Belgium": "BE",
    "Austria": "AT",
    "Switzerland": "CH",
    "Sweden": "SE",
    "Norway": "NO",
    "Denmark": "DK",
    "Czech Republic": "CZ",
    "Hungary": "HU",
    "Romania": "RO",
    "Bulgaria": "BG",
    "Croatia": "HR",
    "Serbia": "RS",
    "Slovenia": "SI",
    "Slovakia": "SK",
    "Lithuania": "LT",
    "Latvia": "LV",
    "Estonia": "EE",
    "Greece": "GR",
    "Portugal": "PT",
    "United States": "US",
    "Canada": "CA",
    "Australia": "AU",
    "New Zealand": "NZ",
    "Japan": "JP",
    "China": "CN",
    "Singapore": "SG",
    "United Arab Emirates": "AE",
}

# Reverse mapping from region code to country name
REGION_TO_COUNTRY = {code: country for country, code in COUNTRY_TO_REGION.items()}


def get_region_from_timezone(tz_str):
    """Get 2-letter region code from timezone string"""
    if not tz_str:
        return None
    return TIMEZONE_TO_REGION.get(tz_str)


def get_region_from_country(country_str):
    """Get 2-letter region code from country name"""
    if not country_str:
        return None
    return COUNTRY_TO_REGION.get(country_str)


def detect_region_from_feed(feed):
    """Detect region from GTFS feed agency data"""
    if not hasattr(feed, "agency") or feed.agency.empty:
        return None

    # Try timezone first
    if "agency_timezone" in feed.agency.columns:
        for _, agency in feed.agency.iterrows():
            region = get_region_from_timezone(agency.agency_timezone)
            if region:
                logger.info(
                    f"Detected region {region} from timezone {agency.agency_timezone}"
                )
                return region

    # Try country if available
    if "agency_country" in feed.agency.columns:
        for _, agency in feed.agency.iterrows():
            region = get_region_from_country(agency.agency_country)
            if region:
                logger.info(
                    f"Detected region {region} from country {agency.agency_country}"
                )
                return region

    return None


def get_region_name(region_id):
    """Get the country name for a region code"""
    return REGION_TO_COUNTRY.get(region_id, region_id)


def ensure_region_exists(region_id, region_name=None):
    """Create region if it doesn't exist"""
    from busstops.models import Region

    if not region_id:
        return None

    # Look up country name from region code if not provided
    if not region_name:
        region_name = REGION_TO_COUNTRY.get(region_id, region_id)

    region, created = Region.objects.get_or_create(
        id=region_id, defaults={"name": region_name}
    )
    if created:
        logger.info(f"Created new region: {region_id} ({region_name})")
    return region
