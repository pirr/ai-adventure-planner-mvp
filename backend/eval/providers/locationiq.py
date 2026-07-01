"""LocationIQ Nearby adapter (hosted OSM-derived POI search).

LocationIQ returns raw OSM `class`/`type`, so taxonomy mapping is free: rebuild an
OSM-tag dict and run it through the production `_place_type_from_tags` /
`_quality_from_tags`. The Nearby API takes a comma-separated `class:type` tag
filter (with `:*` wildcards) in a SINGLE request and caps radius at 30 km, so one
call per scenario covers all requested interests — no per-tag fan-out (which on
the free tier's ~2 req/s just got rate-limited and silently dropped most results).
"""
from __future__ import annotations

import logging
from typing import Any

from app.config import settings
from app.schemas import PlaceCandidate
from app.services.net import http_client
from app.services.places import _estimate_activity, _estimate_walking, _place_type_from_tags, _quality_from_tags
from app.services.scoring import PLACE_INTERESTS

logger = logging.getLogger(__name__)

_MAX_RADIUS_M = 30_000  # LocationIQ Nearby hard cap
_MAX_TAGS = 12

# Requested interest -> LocationIQ Nearby `tag` filters (OSM class:type, `:*` = wildcard).
_INTEREST_TAGS = {
    "nature": ["leisure:park", "natural:water", "natural:beach", "natural:peak", "tourism:viewpoint"],
    "viewpoints": ["tourism:viewpoint", "natural:peak"],
    "history": ["tourism:museum", "tourism:attraction", "historic:*"],
    "fortresses": ["historic:castle", "historic:fort", "historic:city_walls"],
    "water": ["natural:water", "natural:beach", "waterway:waterfall"],
    "food": ["amenity:restaurant", "amenity:cafe", "amenity:fast_food"],
    "drinks": ["amenity:bar", "amenity:pub", "amenity:biergarten"],
    "surprise me": ["tourism:museum", "tourism:attraction", "tourism:viewpoint", "historic:*", "leisure:park"],
}
_DEFAULT_TAGS = ["tourism:attraction", "tourism:viewpoint", "amenity:restaurant"]


def _tag_filter(interests: list[str]) -> str:
    wanted: list[str] = []
    for interest in interests:
        for tag in _INTEREST_TAGS.get(str(interest).strip().lower(), []):
            if tag not in wanted:
                wanted.append(tag)
    return ",".join((wanted or _DEFAULT_TAGS)[:_MAX_TAGS])


def _candidate_from_item(item: dict[str, Any]) -> PlaceCandidate | None:
    name = item.get("name") or (item.get("namedetails") or {}).get("name")
    if not name and item.get("display_name"):
        name = str(item["display_name"]).split(",")[0].strip()
    lat, lon = item.get("lat"), item.get("lon")
    osm_id, osm_type = item.get("osm_id"), item.get("osm_type")
    if not name or lat is None or lon is None:
        return None

    osm_class, osm_value = item.get("class"), item.get("type")
    osm_tags: dict[str, Any] = {}
    if osm_class and osm_value:
        osm_tags[str(osm_class)] = str(osm_value)
    place_type = _place_type_from_tags(osm_tags)

    source_id = (
        f"locationiq:{osm_type}:{osm_id}" if osm_id and osm_type else f"locationiq:{item.get('place_id')}"
    )
    tags = {**osm_tags, "interests": sorted(PLACE_INTERESTS.get(place_type, set()))}
    return PlaceCandidate(
        source="locationiq",
        source_id=source_id,
        name=str(name),
        type=place_type,
        lat=float(lat),
        lon=float(lon),
        tags=tags,
        estimated_activity_minutes=_estimate_activity(place_type),
        estimated_walking_km=_estimate_walking(place_type),
        difficulty="medium" if place_type == "viewpoint" else "easy",
        quality_score=_quality_from_tags(osm_tags, True),
    )


class LocationIQProvider:
    name = "locationiq"

    async def fetch(
        self,
        lat: float,
        lon: float,
        radius_km: float,
        interests: list[str],
        lang: str = "en",
        anonymous_id: str | None = None,
    ) -> list[PlaceCandidate]:
        if not settings.locationiq_api_key:
            return []
        radius_m = min(_MAX_RADIUS_M, max(1000, int(radius_km * 1000)))
        params = {
            "key": settings.locationiq_api_key,
            "lat": lat,
            "lon": lon,
            "tag": _tag_filter(interests),
            "radius": radius_m,
            "limit": 50,
            "format": "json",
        }
        try:
            async with http_client(settings.http_timeout_seconds) as client:
                response = await client.get(f"{settings.locationiq_url}/nearby", params=params)
                response.raise_for_status()
                data = response.json()
        except Exception as exc:  # noqa: BLE001 - surface, don't silently swallow
            logger.warning("locationiq nearby failed (%s)", exc.__class__.__name__)
            return []
        # On a query that matched nothing LocationIQ returns {"error": "Unable to geocode"}.
        if not isinstance(data, list):
            logger.info("locationiq nearby: no results (%s)", str(data)[:80])
            return []

        candidates: list[PlaceCandidate] = []
        seen: set[str] = set()
        for item in data:
            candidate = _candidate_from_item(item)
            if candidate is None or candidate.source_id in seen:
                continue
            seen.add(candidate.source_id)
            candidates.append(candidate)
        return candidates
