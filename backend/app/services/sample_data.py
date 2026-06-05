from __future__ import annotations

from math import cos, radians
from typing import Any

from app.schemas import PlaceCandidate
from app.services.geo import haversine_km


MONTENEGRO_PLACES: list[dict[str, Any]] = [
    {
        "source_id": "sample:porto-naval-heritage",
        "name": "Naval Heritage Collection, Porto Montenegro",
        "type": "museum",
        "lat": 42.4358,
        "lon": 18.6946,
        "tags": {
            "tourism": "museum",
            "historic": "naval",
            "interests": ["history", "family"],
            "wikimedia_commons": "File:Naval Heritage Collection 01.jpg",
        },
        "estimated_activity_minutes": 55,
        "estimated_walking_km": 0.8,
        "difficulty": "easy",
        "quality_score": 75,
    },
    {
        "source_id": "sample:gornja-lastva",
        "name": "Gornja Lastva Viewpoint",
        "type": "viewpoint",
        "lat": 42.4520,
        "lon": 18.7023,
        "tags": {
            "tourism": "viewpoint",
            "interests": ["viewpoints", "history", "nature"],
            "wikimedia_commons": "File:View of Tivat from the road to Gornja Lastva 1.jpg",
        },
        "estimated_activity_minutes": 45,
        "estimated_walking_km": 1.4,
        "difficulty": "easy",
        "quality_score": 78,
    },
    {
        "source_id": "sample:budva-old-town",
        "name": "Budva Old Town and Citadel",
        "type": "fortress",
        "lat": 42.2789,
        "lon": 18.8370,
        "tags": {
            "historic": "city_gate",
            "tourism": "attraction",
            "interests": ["history", "fortresses", "viewpoints"],
            "wikimedia_commons": "File:Budva Old Town and Citadel.jpg",
        },
        "estimated_activity_minutes": 90,
        "estimated_walking_km": 2.2,
        "difficulty": "easy",
        "quality_score": 85,
    },
    {
        "source_id": "sample:mogren-fortress",
        "name": "Mogren Fortress",
        "type": "fortress",
        "lat": 42.2800,
        "lon": 18.8158,
        "tags": {
            "historic": "fort",
            "interests": ["history", "fortresses", "viewpoints"],
            "wikimedia_commons": "File:Mogren Fortress - View up from the Sea.jpg",
        },
        "estimated_activity_minutes": 60,
        "estimated_walking_km": 2.6,
        "difficulty": "medium",
        "quality_score": 80,
    },
    {
        "source_id": "sample:kotor-old-town",
        "name": "Kotor Old Town",
        "type": "historic_site",
        "lat": 42.4256,
        "lon": 18.7712,
        "tags": {
            "historic": "old_town",
            "interests": ["history", "fortresses", "family"],
            "wikimedia_commons": "File:Kotor Old Town.JPG",
        },
        "estimated_activity_minutes": 100,
        "estimated_walking_km": 2.5,
        "difficulty": "easy",
        "quality_score": 90,
    },
    {
        "source_id": "sample:kotor-fortress-walls",
        "name": "Kotor Fortress Walls",
        "type": "fortress",
        "lat": 42.4250,
        "lon": 18.7755,
        "tags": {
            "historic": "fort",
            "interests": ["history", "fortresses", "viewpoints"],
            "steep": True,
            "wikimedia_commons": "File:San Giovanni Fortress, Montenegro.jpg",
        },
        "estimated_activity_minutes": 140,
        "estimated_walking_km": 4.2,
        "difficulty": "hard",
        "quality_score": 88,
    },
    {
        "source_id": "sample:fort-gorazda",
        "name": "Fort Gorazda",
        "type": "fortress",
        "lat": 42.3995,
        "lon": 18.7734,
        "tags": {
            "historic": "fort",
            "interests": ["history", "fortresses", "viewpoints"],
            "wikimedia_commons": "File:Fort Gorazda aerial view.jpg",
        },
        "estimated_activity_minutes": 55,
        "estimated_walking_km": 1.0,
        "difficulty": "easy",
        "quality_score": 76,
    },
    {
        "source_id": "sample:kanli-kula",
        "name": "Kanli Kula Fortress",
        "type": "fortress",
        "lat": 42.4533,
        "lon": 18.5381,
        "tags": {
            "historic": "fort",
            "interests": ["history", "fortresses", "viewpoints"],
            "wikimedia_commons": "File:2024-02-04 Kanli Kula Fortress 1.jpg",
        },
        "estimated_activity_minutes": 60,
        "estimated_walking_km": 1.4,
        "difficulty": "easy",
        "quality_score": 82,
    },
]


def _generic_places(lat: float, lon: float) -> list[dict[str, Any]]:
    # Small synthetic fallback around any user location. Useful when live APIs fail.
    km_lat = 1 / 111.0
    km_lon = 1 / (111.0 * max(0.2, cos(radians(lat))))
    offsets = [
        ("Nearby Scenic Viewpoint", "viewpoint", 2.0, 1.0, ["viewpoints", "nature"], 50, 1.2, "easy", 65),
        ("Local Historic Walk", "historic_site", -1.2, 1.8, ["history"], 60, 1.8, "easy", 62),
        ("Quiet Park Loop", "park", 0.8, -1.5, ["nature", "family"], 45, 1.6, "easy", 60),
        ("Waterfront Break", "water", -2.3, -0.7, ["water", "views"], 50, 1.1, "easy", 59),
        ("Long Hill Trail", "trail", 4.4, 3.2, ["nature", "active"], 130, 5.2, "hard", 57),
    ]
    items = []
    for idx, (name, place_type, north_km, east_km, interests, minutes, walk_km, difficulty, quality) in enumerate(offsets):
        items.append(
            {
                "source_id": f"sample:generic:{idx}",
                "name": name,
                "type": place_type,
                "lat": lat + north_km * km_lat,
                "lon": lon + east_km * km_lon,
                "tags": {"interests": interests},
                "estimated_activity_minutes": minutes,
                "estimated_walking_km": walk_km,
                "difficulty": difficulty,
                "quality_score": quality,
            }
        )
    return items


def fallback_places(lat: float, lon: float, radius_km: float) -> list[PlaceCandidate]:
    curated = []
    for place in MONTENEGRO_PLACES:
        if haversine_km(lat, lon, place["lat"], place["lon"]) <= max(radius_km * 1.5, 25):
            curated.append(place)
    raw_places = curated or _generic_places(lat, lon)
    return [PlaceCandidate(source="fallback", **place) for place in raw_places]
