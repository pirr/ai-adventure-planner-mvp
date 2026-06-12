from __future__ import annotations

import asyncio
import logging
import math
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import settings
from app.schemas import PlaceCandidate, PlacePhoto
from app.services.geo import haversine_km
from app.services.i18n import t
from app.services.net import http_client
from app.services.storage import storage

logger = logging.getLogger(__name__)

# Reject a Text Search match further than this from the OSM coordinates:
# locationBias only *biases*, so a generic name can match the wrong city.
_MAX_MATCH_DISTANCE_KM = 1.0
# Only the fields we use. The mask is the billing lever — extend deliberately.
_FIELD_MASK = "places.id,places.location,places.rating,places.userRatingCount,places.photos"


@dataclass
class GooglePlaceInfo:
    rating: float
    rating_count: int
    photo_name: str | None  # "places/{id}/photos/{ref}", resolved lazily
    photo_attribution: str | None


# {source_id: (expires_at, info | None)}. Negative results are cached too, so
# places Google can't match don't re-bill on every search. In-process only —
# Google ToS caps caching at 30 days, and the TTL stays well inside that.
_cache: dict[str, tuple[float, GooglePlaceInfo | None]] = {}


def enabled() -> bool:
    return bool(settings.google_places_api_key)


def blended_quality(osm_quality: int, rating: float, rating_count: int) -> int:
    # Confidence grows with review volume: ~1k reviews -> trust Google fully.
    weight = min(1.0, math.log10(rating_count + 1) / 3)
    google_quality = rating / 5 * 100
    return max(0, min(100, round((1 - weight) * osm_quality + weight * google_quality)))


async def _search_text(client: httpx.AsyncClient, place: PlaceCandidate) -> dict[str, Any]:
    response = await client.post(
        f"{settings.google_places_url}/places:searchText",
        headers={
            "X-Goog-Api-Key": settings.google_places_api_key or "",
            "X-Goog-FieldMask": _FIELD_MASK,
        },
        json={
            "textQuery": place.name,
            "maxResultCount": 1,
            "locationBias": {
                "circle": {
                    "center": {"latitude": place.lat, "longitude": place.lon},
                    "radius": 1000.0,
                }
            },
        },
    )
    response.raise_for_status()
    return response.json()


def _info_from_payload(place: PlaceCandidate, payload: dict[str, Any]) -> GooglePlaceInfo | None:
    places = payload.get("places") or []
    if not places:
        return None
    result = places[0]
    rating = result.get("rating")
    rating_count = result.get("userRatingCount")
    if rating is None or rating_count is None:
        return None
    location = result.get("location") or {}
    result_lat, result_lon = location.get("latitude"), location.get("longitude")
    if result_lat is None or result_lon is None:
        return None
    if haversine_km(place.lat, place.lon, result_lat, result_lon) > _MAX_MATCH_DISTANCE_KM:
        return None
    photo_name = None
    photo_attribution = None
    for photo in result.get("photos") or []:
        if photo.get("name"):
            photo_name = photo["name"]
            authors = photo.get("authorAttributions") or []
            if authors and authors[0].get("displayName"):
                photo_attribution = authors[0]["displayName"]
            break
    return GooglePlaceInfo(
        rating=float(rating),
        rating_count=int(rating_count),
        photo_name=photo_name,
        photo_attribution=photo_attribution,
    )


async def enrich_places(
    places: list[PlaceCandidate], anonymous_id: str | None, lang: str = "en"
) -> tuple[dict[str, GooglePlaceInfo], list[str]]:
    """Google info per source_id for as many places as cache + budget allow.

    Never raises: per-place failures are skipped, and only a total failure of
    the live calls surfaces as a single data warning.
    """
    results: dict[str, GooglePlaceInfo] = {}
    if not enabled():
        return results, []

    now = time.time()
    misses: list[PlaceCandidate] = []
    for place in places[: settings.google_places_max_enriched]:
        cached = _cache.get(place.source_id)
        if cached and cached[0] > now:
            if cached[1] is not None:
                results[place.source_id] = cached[1]
            continue
        misses.append(place)

    granted = storage.reserve_google_calls(anonymous_id, len(misses))
    if misses and granted == 0:
        logger.info("google places: daily budget exhausted or no anonymous_id, skipping %d lookups", len(misses))
    to_fetch = misses[:granted]
    if not to_fetch:
        return results, []

    async with http_client(settings.google_places_timeout_seconds) as client:
        payloads = await asyncio.gather(
            *[_search_text(client, place) for place in to_fetch], return_exceptions=True
        )

    first_error: BaseException | None = None
    failures = 0
    for place, payload in zip(to_fetch, payloads):
        if isinstance(payload, BaseException):
            failures += 1  # transient: don't cache, retry on a later search
            first_error = first_error or payload
            continue
        info = _info_from_payload(place, payload)
        _cache[place.source_id] = (now + settings.google_places_cache_ttl_seconds, info)
        if info is not None:
            results[place.source_id] = info

    logger.debug(
        "google places: %d cached, %d fetched, %d failed (budget granted %d/%d)",
        len(places) - len(misses), len(to_fetch) - failures, failures, granted, len(misses),
    )
    warnings: list[str] = []
    if failures == len(to_fetch):
        exc = first_error.__class__.__name__ if first_error else "error"
        warnings.append(t(lang, "warn_google_unavailable", exc=exc))
    return results, warnings


async def resolve_photo(photo_name: str, attribution: str | None) -> PlacePhoto | None:
    """Resolve a photo reference to a key-less googleusercontent URL.

    skipHttpRedirect makes the API return the final URI as JSON instead of a
    302, so the browser never sees a URL with our API key in it.
    """
    async with http_client(settings.google_places_timeout_seconds) as client:
        response = await client.get(
            f"{settings.google_places_url}/{photo_name}/media",
            params={"maxWidthPx": 960, "skipHttpRedirect": "true"},
            headers={"X-Goog-Api-Key": settings.google_places_api_key or ""},
        )
        response.raise_for_status()
        photo_uri = response.json().get("photoUri")
    if not photo_uri:
        return None
    return PlacePhoto(url=photo_uri, source="Google Maps", attribution=attribution)
