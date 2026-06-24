"""LocationIQ Nearby adapter (hosted OSM-derived POI search).

LocationIQ returns raw OSM `class`/`type` (and often `extratags`), so taxonomy
mapping is free: rebuild an OSM-tag dict and run it through the production
`_place_type_from_tags` / `_quality_from_tags`. Nearby restricts to one tag per
call, so a request fans out to a few tag buckets (concurrently, best-effort) and
merges — the "may need a call per interest bucket" cost flagged in the plan.
"""
from __future__ import annotations

import asyncio
from typing import Any

from app.config import settings
from app.schemas import PlaceCandidate
from app.services.net import http_client
from app.services.places import _estimate_activity, _estimate_walking, _place_type_from_tags, _quality_from_tags
from app.services.scoring import PLACE_INTERESTS

_MAX_RADIUS_M = 50_000
_MAX_TAGS = 6  # bound the fan-out (and the free-tier call count) per request

# Requested interest -> LocationIQ Nearby `tag` values (OSM values).
_INTEREST_TAGS = {
    "nature": ["park", "viewpoint", "beach"],
    "viewpoints": ["viewpoint"],
    "history": ["museum", "attraction", "castle"],
    "fortresses": ["castle", "fort"],
    "water": ["beach", "water"],
    "food": ["restaurant", "cafe", "fast_food"],
    "drinks": ["bar", "pub"],
    "surprise me": ["attraction", "viewpoint", "museum", "park"],
}
_DEFAULT_TAGS = ["attraction", "viewpoint", "restaurant"]


def _tags_for_interests(interests: list[str]) -> list[str]:
    wanted: list[str] = []
    for interest in interests:
        for tag in _INTEREST_TAGS.get(str(interest).strip().lower(), []):
            if tag not in wanted:
                wanted.append(tag)
    return (wanted or list(_DEFAULT_TAGS))[:_MAX_TAGS]


def _candidate_from_item(item: dict[str, Any]) -> PlaceCandidate | None:
    name = item.get("name") or (item.get("namedetails") or {}).get("name")
    if not name and item.get("display_name"):
        name = str(item["display_name"]).split(",")[0].strip()
    lat, lon = item.get("lat"), item.get("lon")
    osm_id, osm_type = item.get("osm_id"), item.get("osm_type")
    if not name or lat is None or lon is None:
        return None

    osm_class, osm_value = item.get("class"), item.get("type")
    osm_tags: dict[str, Any] = dict(item.get("extratags") or {})
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
        name=name,
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

    async def _nearby(self, client, lat: float, lon: float, radius_m: int, tag: str) -> list[dict[str, Any]]:
        response = await client.get(
            f"{settings.locationiq_url}/nearby",
            params={
                "key": settings.locationiq_api_key,
                "lat": lat,
                "lon": lon,
                "tag": tag,
                "radius": radius_m,
                "limit": 30,
                "format": "json",
            },
        )
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, list) else []

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
        tags = _tags_for_interests(interests)
        async with http_client(settings.http_timeout_seconds) as client:
            batches = await asyncio.gather(
                *[self._nearby(client, lat, lon, radius_m, tag) for tag in tags],
                return_exceptions=True,
            )

        candidates: list[PlaceCandidate] = []
        seen: set[str] = set()
        for batch in batches:
            if isinstance(batch, BaseException):
                continue  # best-effort: a failed tag bucket just contributes nothing
            for item in batch:
                candidate = _candidate_from_item(item)
                if candidate is None or candidate.source_id in seen:
                    continue
                seen.add(candidate.source_id)
                candidates.append(candidate)
        return candidates
