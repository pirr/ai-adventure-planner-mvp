from __future__ import annotations

from typing import Any

import httpx

from app.config import settings
from app.schemas import PlaceCandidate
from app.services.sample_data import fallback_places


INTEREST_OSM_FILTERS = {
    "nature": ["tourism=viewpoint", "leisure=park", "natural=beach", "natural=water", "waterway=waterfall"],
    "viewpoints": ["tourism=viewpoint"],
    "history": ["historic", "tourism=museum", "tourism=attraction"],
    "fortresses": ["historic=fort", "historic=castle", "castle_type"],
    "water": ["natural=beach", "natural=water", "waterway=waterfall"],
    "food": ["amenity=cafe", "amenity=restaurant"],
    "surprise me": ["tourism=viewpoint", "historic", "leisure=park", "tourism=attraction"],
}


def radius_for_request(available_minutes: int, transport_mode: str) -> float:
    if transport_mode == "walk":
        if available_minutes <= 60:
            return 2.5
        if available_minutes <= 180:
            return 5
        return 8
    if transport_mode == "bike":
        if available_minutes <= 60:
            return 5
        if available_minutes <= 180:
            return 15
        return 30
    if available_minutes <= 60:
        return 8
    if available_minutes <= 180:
        return 25
    if available_minutes <= 300:
        return 55
    return 90


def _build_overpass_query(lat: float, lon: float, radius_m: int, interests: list[str]) -> str:
    # Broad query first; ranking happens later.
    return f"""
[out:json][timeout:10];
(
  node(around:{radius_m},{lat},{lon})["tourism"~"viewpoint|attraction|museum|gallery|zoo"];
  way(around:{radius_m},{lat},{lon})["tourism"~"viewpoint|attraction|museum|gallery|zoo"];
  relation(around:{radius_m},{lat},{lon})["tourism"~"viewpoint|attraction|museum|gallery|zoo"];
  node(around:{radius_m},{lat},{lon})["historic"];
  way(around:{radius_m},{lat},{lon})["historic"];
  relation(around:{radius_m},{lat},{lon})["historic"];
  node(around:{radius_m},{lat},{lon})["leisure"="park"];
  way(around:{radius_m},{lat},{lon})["leisure"="park"];
  node(around:{radius_m},{lat},{lon})["natural"~"beach|water|peak|cave_entrance"];
  way(around:{radius_m},{lat},{lon})["natural"~"beach|water|peak|cave_entrance"];
  node(around:{radius_m},{lat},{lon})["waterway"="waterfall"];
);
out center tags 80;
"""


def _place_type_from_tags(tags: dict[str, Any]) -> str:
    historic = tags.get("historic")
    tourism = tags.get("tourism")
    leisure = tags.get("leisure")
    natural = tags.get("natural")
    amenity = tags.get("amenity")
    if historic in {"fort", "castle", "citywalls", "archaeological_site"}:
        return "fortress" if historic in {"fort", "castle", "citywalls"} else "historic_site"
    if historic:
        return "historic_site"
    if tourism == "viewpoint":
        return "viewpoint"
    if tourism == "museum":
        return "museum"
    if tourism:
        return "attraction"
    if natural in {"beach", "water"}:
        return "water"
    if natural == "peak":
        return "viewpoint"
    if leisure == "park":
        return "park"
    if amenity in {"cafe", "restaurant"}:
        return "food"
    return "place"


def _quality_from_tags(tags: dict[str, Any], has_name: bool) -> int:
    score = 50
    if has_name:
        score += 15
    if tags.get("wikipedia") or tags.get("wikidata"):
        score += 15
    if tags.get("tourism") in {"viewpoint", "museum", "attraction"}:
        score += 8
    if tags.get("historic"):
        score += 10
    if tags.get("opening_hours"):
        score += 4
    return max(0, min(100, score))


def _estimate_activity(place_type: str) -> int:
    return {
        "viewpoint": 35,
        "park": 60,
        "water": 60,
        "museum": 75,
        "historic_site": 70,
        "fortress": 80,
        "attraction": 60,
        "food": 45,
    }.get(place_type, 45)


def _estimate_walking(place_type: str) -> float:
    return {
        "viewpoint": 1.2,
        "park": 2.0,
        "water": 1.6,
        "museum": 0.8,
        "historic_site": 1.8,
        "fortress": 2.0,
        "attraction": 1.3,
        "food": 0.4,
    }.get(place_type, 1.0)


async def fetch_osm_places(lat: float, lon: float, radius_km: float, interests: list[str]) -> list[PlaceCandidate]:
    radius_m = int(radius_km * 1000)
    query = _build_overpass_query(lat, lon, radius_m, interests)
    async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
        response = await client.post(settings.overpass_url, data={"data": query})
        response.raise_for_status()
        payload = response.json()

    candidates: list[PlaceCandidate] = []
    seen: set[str] = set()
    for element in payload.get("elements", []):
        tags = element.get("tags") or {}
        name = tags.get("name") or tags.get("name:en")
        if not name:
            continue
        center = element.get("center") or {}
        place_lat = element.get("lat") or center.get("lat")
        place_lon = element.get("lon") or center.get("lon")
        if place_lat is None or place_lon is None:
            continue
        source_id = f"osm:{element.get('type')}:{element.get('id')}"
        if source_id in seen:
            continue
        seen.add(source_id)
        place_type = _place_type_from_tags(tags)
        candidates.append(
            PlaceCandidate(
                source="openstreetmap",
                source_id=source_id,
                name=name,
                type=place_type,
                lat=float(place_lat),
                lon=float(place_lon),
                tags=tags,
                estimated_activity_minutes=_estimate_activity(place_type),
                estimated_walking_km=_estimate_walking(place_type),
                difficulty="easy" if place_type != "viewpoint" else "medium",
                quality_score=_quality_from_tags(tags, True),
            )
        )
    return candidates


async def get_candidate_places(lat: float, lon: float, available_minutes: int, transport_mode: str, interests: list[str], use_live_data: bool) -> tuple[list[PlaceCandidate], list[str]]:
    radius_km = radius_for_request(available_minutes, transport_mode)
    warnings: list[str] = []
    candidates: list[PlaceCandidate] = []

    if use_live_data:
        try:
            candidates = await fetch_osm_places(lat, lon, radius_km, interests)
        except Exception as exc:  # noqa: BLE001 - MVP should degrade gracefully
            warnings.append(f"OpenStreetMap/Overpass unavailable, using fallback places: {exc.__class__.__name__}")

    fallback = fallback_places(lat, lon, radius_km)
    if len(candidates) < 8:
        existing = {item.source_id for item in candidates}
        candidates.extend([item for item in fallback if item.source_id not in existing])
        if not use_live_data:
            warnings.append("Live place search disabled, using fallback/sample places.")
        elif candidates:
            warnings.append("Live place search returned limited results, supplemented with fallback/sample places.")

    return candidates, warnings
