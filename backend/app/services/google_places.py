from __future__ import annotations

import asyncio
import json
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
_CANDIDATE_FIELD_MASK = (
    "places.id,places.displayName,places.primaryType,places.types,places.location,"
    "places.rating,places.userRatingCount,places.photos"
)

_DRINK_TYPES = {"bar", "pub"}
_EAT_TYPES = {"cafe", "coffee_shop", "ice_cream_shop", "restaurant", "meal_takeaway"}
_TYPE_TO_PLACE_TYPE = {
    "art_gallery": "museum",
    "bar": "drinks",
    "cafe": "food",
    "coffee_shop": "food",
    "historical_landmark": "historic_site",
    "ice_cream_shop": "food",
    "meal_takeaway": "food",
    "museum": "museum",
    "park": "park",
    "pub": "drinks",
    "restaurant": "food",
    "tourist_attraction": "attraction",
}


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


def _info_to_json(info: GooglePlaceInfo | None) -> str | None:
    if info is None:
        return None
    return json.dumps(
        {
            "rating": info.rating,
            "rating_count": info.rating_count,
            "photo_name": info.photo_name,
            "photo_attribution": info.photo_attribution,
        },
        ensure_ascii=False,
    )


def _info_from_json(payload_json: str | None) -> GooglePlaceInfo | None:
    if payload_json is None:
        return None
    payload = json.loads(payload_json)
    return GooglePlaceInfo(
        rating=float(payload["rating"]),
        rating_count=int(payload["rating_count"]),
        photo_name=payload.get("photo_name"),
        photo_attribution=payload.get("photo_attribution"),
    )


def _cache_get(source_id: str, now: float) -> tuple[bool, GooglePlaceInfo | None]:
    cached = _cache.get(source_id)
    if cached is not None:
        expires_at, info = cached
        if expires_at > now:
            return True, info
        _cache.pop(source_id, None)

    try:
        entry = storage.place_cache.get_google_place(source_id, now)
    except Exception:  # noqa: BLE001 - cache failure must not break enrichment
        return False, None
    if entry is None:
        return False, None
    try:
        info = _info_from_json(entry.payload_json)
    except Exception:  # noqa: BLE001 - corrupt cache rows are treated as misses
        return False, None
    _cache[source_id] = (entry.expires_at, info)
    return True, info


def _cache_set(source_id: str, expires_at: float, info: GooglePlaceInfo | None) -> None:
    _cache[source_id] = (expires_at, info)
    try:
        storage.place_cache.set_google_place(source_id, expires_at, _info_to_json(info))
    except Exception:  # noqa: BLE001 - cache persistence is best-effort
        pass


def _candidate_text_query(interests: list[str]) -> str:
    normalized = {str(interest).strip().lower() for interest in interests}
    if "drinks" in normalized:
        return "bars pubs"
    if "food" in normalized:
        return "restaurants cafes"
    if "caves" in normalized:
        return "caves cave tours natural attractions"
    if {"history", "fortresses"} & normalized:
        return "historic sites museums castles fortresses tourist attractions"
    if {"nature", "viewpoints", "water"} & normalized:
        return "viewpoints parks beaches natural attractions"
    return "tourist attractions viewpoints restaurants"


async def _search_candidate_text(
    client: httpx.AsyncClient,
    lat: float,
    lon: float,
    radius_km: float,
    interests: list[str],
    lang: str,
) -> dict[str, Any]:
    response = await client.post(
        f"{settings.google_places_url}/places:searchText",
        headers={
            "X-Goog-Api-Key": settings.google_places_api_key or "",
            "X-Goog-FieldMask": _CANDIDATE_FIELD_MASK,
        },
        json={
            "textQuery": _candidate_text_query(interests),
            "maxResultCount": max(1, min(20, settings.google_places_candidate_limit)),
            "languageCode": "ru" if lang == "ru" else "en",
            "locationBias": {
                "circle": {
                    "center": {"latitude": lat, "longitude": lon},
                    "radius": min(50000.0, max(1000.0, radius_km * 1000)),
                }
            },
        },
    )
    response.raise_for_status()
    return response.json()


def _candidate_place_type(result: dict[str, Any]) -> str:
    types = {str(item) for item in result.get("types") or []}
    primary_type = str(result.get("primaryType") or "")
    display_name = result.get("displayName") or {}
    name = str(display_name.get("text") or "").lower()
    if "cave" in name or "cavern" in name:
        return "cave"
    if types & _DRINK_TYPES or primary_type in _DRINK_TYPES:
        return "drinks"
    if types & _EAT_TYPES or primary_type in _EAT_TYPES:
        return "food"
    return _TYPE_TO_PLACE_TYPE.get(primary_type, "attraction")


def _candidate_interests(place_type: str) -> list[str]:
    return {
        "attraction": ["surprise me"],
        "cave": ["caves", "nature"],
        "food": ["food"],
        "drinks": ["drinks"],
        "historic_site": ["history"],
        "museum": ["history"],
        "park": ["nature"],
        "viewpoint": ["viewpoints", "nature"],
    }.get(place_type, [])


def _candidate_activity(place_type: str) -> int:
    return {
        "attraction": 60,
        "cave": 45,
        "food": 45,
        "drinks": 50,
        "historic_site": 70,
        "museum": 75,
        "park": 60,
        "viewpoint": 35,
    }.get(place_type, 45)


def _candidate_walking(place_type: str) -> float:
    return {
        "attraction": 1.0,
        "cave": 1.5,
        "food": 0.4,
        "drinks": 0.3,
        "historic_site": 1.4,
        "museum": 0.8,
        "park": 1.6,
        "viewpoint": 1.2,
    }.get(place_type, 1.0)


def _photo_from_result(result: dict[str, Any]) -> tuple[str | None, str | None]:
    for photo in result.get("photos") or []:
        if not photo.get("name"):
            continue
        authors = photo.get("authorAttributions") or []
        attribution = authors[0].get("displayName") if authors else None
        return photo["name"], attribution
    return None, None


def _candidate_from_result(result: dict[str, Any], origin_lat: float, origin_lon: float, radius_km: float) -> PlaceCandidate | None:
    place_id = result.get("id")
    display_name = result.get("displayName") or {}
    name = display_name.get("text")
    location = result.get("location") or {}
    lat, lon = location.get("latitude"), location.get("longitude")
    if not place_id or not name or lat is None or lon is None:
        return None
    if haversine_km(origin_lat, origin_lon, float(lat), float(lon)) > max(1.0, radius_km * 1.15):
        return None

    place_type = _candidate_place_type(result)
    rating = result.get("rating")
    rating_count = result.get("userRatingCount")
    photo_name, photo_attribution = _photo_from_result(result)
    quality = 65
    if rating is not None and rating_count is not None:
        quality = blended_quality(60, float(rating), int(rating_count))
    return PlaceCandidate(
        source="google_places",
        source_id=f"google:{place_id}",
        name=name,
        type=place_type,
        lat=float(lat),
        lon=float(lon),
        tags={
            "google_primary_type": result.get("primaryType"),
            "google_types": result.get("types") or [],
            "interests": _candidate_interests(place_type),
        },
        estimated_activity_minutes=_candidate_activity(place_type),
        estimated_walking_km=_candidate_walking(place_type),
        difficulty="medium" if place_type == "viewpoint" else "easy",
        quality_score=quality,
        rating=float(rating) if rating is not None else None,
        rating_count=int(rating_count) if rating_count is not None else None,
        google_photo_name=photo_name,
        google_photo_attribution=photo_attribution,
    )


async def search_candidate_places(
    lat: float,
    lon: float,
    radius_km: float,
    interests: list[str],
    anonymous_id: str | None,
    lang: str = "en",
) -> tuple[list[PlaceCandidate], list[str]]:
    """Real live candidate places from one bounded Google Text Search.

    This is only a backstop for sparse OSM/Overpass results; it never returns
    sample data and it consumes a small app-side Google candidate budget.
    """
    if not enabled():
        return [], []
    granted = storage.api_usage.reserve_api_calls(
        "google_candidates",
        anonymous_id,
        1,
        daily_limit=settings.google_places_candidate_daily_limit,
        user_daily_limit=settings.google_places_candidate_user_daily_limit,
    )
    if granted < 1:
        logger.info("google places candidate search: daily budget exhausted or no anonymous_id")
        return [], []

    try:
        async with http_client(settings.google_places_timeout_seconds) as client:
            payload = await _search_candidate_text(client, lat, lon, radius_km, interests, lang)
    except Exception as exc:  # noqa: BLE001 - live source failure should degrade to other live sources
        return [], [t(lang, "warn_google_unavailable", exc=exc.__class__.__name__)]

    candidates = []
    seen: set[str] = set()
    for result in payload.get("places") or []:
        candidate = _candidate_from_result(result, lat, lon, radius_km)
        if candidate is None or candidate.source_id in seen:
            continue
        seen.add(candidate.source_id)
        candidates.append(candidate)
    logger.debug("google places candidate search: %d candidates", len(candidates))
    return candidates, []


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
        if place.source == "google_places":
            if place.rating is not None and place.rating_count is not None:
                results[place.source_id] = GooglePlaceInfo(
                    rating=place.rating,
                    rating_count=place.rating_count,
                    photo_name=place.google_photo_name,
                    photo_attribution=place.google_photo_attribution,
                )
            continue
        hit, info = _cache_get(place.source_id, now)
        if hit:
            if info is not None:
                results[place.source_id] = info
            continue
        misses.append(place)

    granted = storage.api_usage.reserve_api_calls(
        "google",
        anonymous_id,
        len(misses),
        daily_limit=settings.google_places_daily_limit,
        user_daily_limit=settings.google_places_user_daily_limit,
    )
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
        _cache_set(place.source_id, now + settings.google_places_cache_ttl_seconds, info)
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
