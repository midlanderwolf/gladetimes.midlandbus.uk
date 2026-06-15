#!/usr/bin/env python3
"""
Test script for ThirdRails import command logic
"""

import requests
import json
import re
from django.contrib.gis.geos import Point


def parse_coordinates(points_str):
    """Parse coordinate string into Point object"""
    if not points_str:
        return None

    try:
        # ThirdRails appears to use longitude,latitude format
        coords = points_str.split(",")
        if len(coords) != 2:
            return None

        lng = float(coords[0].strip())
        lat = float(coords[1].strip())

        # Validate coordinate ranges
        if not (-180 <= lng <= 180 and -90 <= lat <= 90):
            return None

        return Point(lng, lat)
    except (ValueError, IndexError):
        print(f"Invalid coordinates: {points_str}")
        return None


def parse_speed(speed_text):
    """Parse speed from text like '103 km/h'"""
    if not speed_text:
        return 0

    speed_match = re.search(r"(\d+)", speed_text)
    return int(speed_match.group(1)) if speed_match else 0


def test_api():
    """Test the ThirdRails API"""
    print("Testing ThirdRails API...")

    session = requests.Session()
    session.headers.update({"User-Agent": "bustimes.org ThirdRails importer"})

    response = session.post(
        "https://www.rentor.nl/api/radar/GetRadarData", json={}, timeout=20
    )
    print(f"Status: {response.status_code}")

    if response.status_code != 200:
        print(f"Error: {response.text}")
        return

    routes = response.json()
    print(f"Found {len(routes)} routes")

    # Test coordinate parsing
    print("\nTesting coordinate parsing:")
    for i, route in enumerate(routes[:3]):  # Test first 3 routes
        points = route.get("Points", "")
        latlong = parse_coordinates(points)
        speed = parse_speed(route.get("Speed", ""))

        print(f"Route {i + 1}: {route.get('Name', 'Unknown')}")
        print(f"  Simulator: {route.get('Simulator', 'Unknown')}")
        print(f"  Points: {points}")
        print(f"  Parsed coordinates: {latlong.coords if latlong else 'None'}")
        print(f"  Speed: {route.get('Speed', 'Unknown')} -> {speed} km/h")
        print(f"  Loco: {route.get('Loco', 'Unknown')}")
        print(f"  UniqueName: {route.get('UniqueName', 'Unknown')}")
        print()

    # Count by simulator
    tsc_count = sum(1 for r in routes if r.get("Simulator") == "TSC")
    tsw_count = sum(1 for r in routes if r.get("Simulator") == "TSW")
    print(f"Summary:")
    print(f"  TSC (Train Sim Classic): {tsc_count}")
    print(f"  TSW (Train Sim World): {tsw_count}")
    print(f"  Total: {len(routes)}")


if __name__ == "__main__":
    test_api()
