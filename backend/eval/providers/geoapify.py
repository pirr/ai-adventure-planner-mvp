"""Geoapify Places adapter (hosted OSM-derived Places API).

Geoapify exposes a curated category tree rather than raw OSM tags, so this maps
its categories both ways: requested interests -> `categories=` filter, and each
result's categories -> the app's place `type`. No ratings (those only arrive in
the +Google arm). Response shape assumed: GeoJSON FeatureCollection, each
feature `properties` carrying name/categories/place_id/lat/lon and (often)
`datasource.raw` OSM tags.
"""
from __future__ import annotations

from typing import Any

from app.config import settings
from app.schemas import PlaceCandidate
from app.services.net import http_client
from app.services.places import _estimate_activity, _estimate_walking
from app.services.scoring import PLACE_INTERESTS

# Geoapify circle filter is bounded; keep the request inside a sane radius.
_MAX_RADIUS_M = 50_000

# Requested interest -> Geoapify category prefixes to ask for.
_INTEREST_CATEGORIES = {
    "nature": ["leisure.park", "natural.water", "beach", "natural.mountain.peak", "tourism.attraction.viewpoint"],
    "viewpoints": ["tourism.attraction.viewpoint", "natural.mountain.peak"],
    "history": ["tourism.sights", "entertainment.museum", "tourism.attraction"],
    "fortresses": ["tourism.sights.castle", "tourism.sights"],
    "water": ["natural.water", "beach"],
    "food": ["catering.restaurant", "catering.cafe", "catering.fast_food", "catering.ice_cream"],
    "drinks": ["catering.bar", "catering.pub", "catering.biergarten"],
    "surprise me": ["tourism.sights", "tourism.attraction", "leisure.park", "entertainment.museum"],
}
_DEFAULT_CATEGORIES = ["tourism.sights", "tourism.attraction", "leisure.park", "catering.restaurant"]


def _categories_for_interests(interests: list[str]) -> list[str]:
    wanted: list[str] = []
    for interest in interests:
        for category in _INTEREST_CATEGORIES.get(str(interest).strip().lower(), []):
            if category not in wanted:
                wanted.append(category)
    return wanted or list(_DEFAULT_CATEGORIES)


def _place_type_from_categories(categories: list[str]) -> str:
    cats = [str(c) for c in categories]

    def has(prefix: str) -> bool:
        return any(c == prefix or c.startswith(prefix + ".") for c in cats)

    if has("catering.bar") or has("catering.pub") or has("catering.biergarten"):
        return "drinks"
    if has("catering"):
        return "food"
    if has("entertainment.museum") or has("entertainment.culture.gallery"):
        return "museum"
    if has("tourism.sights.castle") or any("castle" in c or "fort" in c or "city_walls" in c for c in cats):
        return "fortress"
    if has("tourism.attraction.viewpoint") or has("natural.mountain.peak"):
        return "viewpoint"
    if has("natural.water") or has("beach"):
        return "water"
    if has("leisure.park") or has("national_park"):
        return "park"
    if has("tourism.sights"):
        return "historic_site"
    if has("tourism"):
        return "attraction"
    return "place"


def _quality(props: dict[str, Any], place_type: str, has_name: bool) -> int:
    raw = (props.get("datasource") or {}).get("raw") or {}
    score = 50
    if has_name:
        score += 15
    if raw.get("wikidata") or raw.get("wikipedia"):
        score += 15
    if place_type in {"viewpoint", "museum", "attraction"}:
        score += 8
    if place_type in {"historic_site", "fortress"}:
        score += 10
    if raw.get("opening_hours") or props.get("opening_hours"):
        score += 4
    return max(0, min(100, score))


def _candidate_from_feature(feature: dict[str, Any]) -> PlaceCandidate | None:
    props = feature.get("properties") or {}
    name = props.get("name")
    place_id = props.get("place_id")
    lat, lon = props.get("lat"), props.get("lon")
    if lat is None or lon is None:
        coords = (feature.get("geometry") or {}).get("coordinates") or []
        if len(coords) == 2:
            lon, lat = coords[0], coords[1]
    if not name or not place_id or lat is None or lon is None:
        return None

    categories = props.get("categories") or []
    place_type = _place_type_from_categories(categories)
    raw = (props.get("datasource") or {}).get("raw") or {}
    tags: dict[str, Any] = {
        "geoapify_categories": categories,
        "interests": sorted(PLACE_INTERESTS.get(place_type, set())),
    }
    opening_hours = raw.get("opening_hours") or props.get("opening_hours")
    if opening_hours:
        tags["opening_hours"] = opening_hours

    return PlaceCandidate(
        source="geoapify",
        source_id=f"geoapify:{place_id}",
        name=str(name),  # Geoapify occasionally returns a numeric name
        type=place_type,
        lat=float(lat),
        lon=float(lon),
        tags=tags,
        estimated_activity_minutes=_estimate_activity(place_type),
        estimated_walking_km=_estimate_walking(place_type),
        difficulty="medium" if place_type == "viewpoint" else "easy",
        quality_score=_quality(props, place_type, True),
    )


class GeoapifyProvider:
    name = "geoapify"

    async def fetch(
        self,
        lat: float,
        lon: float,
        radius_km: float,
        interests: list[str],
        lang: str = "en",
        anonymous_id: str | None = None,
    ) -> list[PlaceCandidate]:
        if not settings.geoapify_api_key:
            return []
        radius_m = min(_MAX_RADIUS_M, max(1000, int(radius_km * 1000)))
        params = {
            "categories": ",".join(_categories_for_interests(interests)),
            "filter": f"circle:{lon},{lat},{radius_m}",
            "bias": f"proximity:{lon},{lat}",
            "limit": 60,
            "lang": "ru" if lang == "ru" else "en",
            "apiKey": settings.geoapify_api_key,
        }
        async with http_client(settings.http_timeout_seconds) as client:
            response = await client.get(f"{settings.geoapify_url}/places", params=params)
            response.raise_for_status()
            data = response.json()

        candidates: list[PlaceCandidate] = []
        seen: set[str] = set()
        for feature in data.get("features") or []:
            candidate = _candidate_from_feature(feature)
            if candidate is None or candidate.source_id in seen:
                continue
            seen.add(candidate.source_id)
            candidates.append(candidate)
        return candidates
