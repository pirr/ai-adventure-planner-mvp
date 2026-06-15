from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.config import settings
from app.schemas import PlaceCandidate
from app.services import google_places
from app.services.i18n import t
from app.services.net import http_client
from app.services.sample_data import fallback_places


# Server-busy statuses worth a quick same-endpoint retry (momentary hiccup).
_RETRY_OVERPASS_STATUS = {502, 503, 504}
# Rate-limit / no-free-slot statuses. A quick retry won't clear these (slots
# refill over seconds), so fail over to the next endpoint / sample places now.
_FAILOVER_OVERPASS_STATUS = {406, 429}


INTEREST_OSM_FILTERS = {
    "nature": ["tourism=viewpoint", "leisure=park", "natural=beach", "natural=water", "waterway=waterfall"],
    "viewpoints": ["tourism=viewpoint"],
    "history": ["historic", "tourism=museum", "tourism=attraction"],
    "fortresses": ["historic=fort", "historic=castle", "castle_type"],
    "water": ["natural=beach", "natural=water", "waterway=waterfall"],
    "food": ["amenity=cafe", "amenity=restaurant", "amenity=fast_food"],
    "drinks": ["amenity=bar", "amenity=pub", "amenity=biergarten"],
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
    normalized = {str(interest).strip().lower() for interest in interests}
    food_block = ""
    if normalized & {"food", "drinks"}:
        food_block = f"""
  node(around:{radius_m},{lat},{lon})["amenity"~"restaurant|cafe|bar|pub|fast_food|ice_cream|biergarten"];
  way(around:{radius_m},{lat},{lon})["amenity"~"restaurant|cafe|bar|pub|fast_food|ice_cream|biergarten"];
  relation(around:{radius_m},{lat},{lon})["amenity"~"restaurant|cafe|bar|pub|fast_food|ice_cream|biergarten"];
"""
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
{food_block}
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
    if amenity in {"bar", "pub", "biergarten"}:
        return "drinks"
    if amenity in {"cafe", "fast_food", "ice_cream", "restaurant"}:
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
        "drinks": 50,
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
        "drinks": 0.3,
    }.get(place_type, 1.0)


def _overpass_endpoints() -> list[str]:
    endpoints = [settings.overpass_url]
    for url in settings.overpass_mirrors:
        if url and url not in endpoints:
            endpoints.append(url)
    return endpoints


async def _overpass_request(client: httpx.AsyncClient, query: str) -> dict[str, Any]:
    """POST the query to each Overpass endpoint, then fall over to the next.

    Rate-limit rejections come back instantly and won't clear on a quick retry,
    so we only retry genuinely busy endpoints (502/503/504) and otherwise move on.
    The most informative error (a real HTTP status over a mirror's connection
    error) is re-raised so the caller can report a meaningful cause.
    """
    attempts = max(1, settings.overpass_max_attempts)
    last_exc: Exception | None = None
    status_exc: httpx.HTTPStatusError | None = None  # preferred for reporting
    for url in _overpass_endpoints():
        for attempt in range(attempts):
            try:
                response = await client.post(url, data={"data": query})
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as exc:
                last_exc = exc
                if status_exc is None:
                    status_exc = exc
                if exc.response.status_code in _RETRY_OVERPASS_STATUS and attempt + 1 < attempts:
                    await asyncio.sleep(settings.overpass_retry_backoff_seconds * (attempt + 1))
                    continue
                break  # rate-limited, non-transient, or out of retries -> next endpoint
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last_exc = exc
                break  # connection/timeout -> try the next endpoint
    # Prefer a real HTTP status (e.g. the primary's 406) over a mirror's
    # connection error, so the surfaced warning names the actual cause.
    raise status_exc or last_exc or RuntimeError("No Overpass endpoint configured")


async def fetch_osm_places(lat: float, lon: float, radius_km: float, interests: list[str]) -> list[PlaceCandidate]:
    radius_m = int(radius_km * 1000)
    query = _build_overpass_query(lat, lon, radius_m, interests)
    async with http_client(settings.overpass_timeout_seconds) as client:
        payload = await _overpass_request(client, query)

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


def _needs_google_candidates(candidates: list[PlaceCandidate], interests: list[str]) -> bool:
    normalized = {str(interest).strip().lower() for interest in interests}
    if len(candidates) < 8:
        return True
    if "food" in normalized and sum(candidate.type == "food" for candidate in candidates) < 5:
        return True
    if "drinks" in normalized and sum(candidate.type == "drinks" for candidate in candidates) < 5:
        return True
    return False


def _merge_live_candidates(primary: list[PlaceCandidate], secondary: list[PlaceCandidate]) -> list[PlaceCandidate]:
    merged = list(primary)
    seen = {candidate.source_id for candidate in merged}
    for candidate in secondary:
        if candidate.source_id in seen:
            continue
        merged.append(candidate)
        seen.add(candidate.source_id)
    return merged


async def get_candidate_places(
    lat: float,
    lon: float,
    available_minutes: int,
    transport_mode: str,
    interests: list[str],
    use_live_data: bool,
    lang: str = "en",
    anonymous_id: str | None = None,
) -> tuple[list[PlaceCandidate], list[str]]:
    radius_km = radius_for_request(available_minutes, transport_mode)
    warnings: list[str] = []
    candidates: list[PlaceCandidate] = []

    if use_live_data:
        try:
            candidates = await fetch_osm_places(lat, lon, radius_km, interests)
        except httpx.HTTPStatusError as exc:
            warnings.append(t(lang, "warn_osm_unavailable", exc=f"HTTP {exc.response.status_code}"))
        except Exception as exc:  # noqa: BLE001 - MVP should degrade gracefully
            warnings.append(t(lang, "warn_osm_unavailable", exc=exc.__class__.__name__))

        if _needs_google_candidates(candidates, interests):
            google_candidates, google_warnings = await google_places.search_candidate_places(
                lat, lon, radius_km, interests, anonymous_id, lang
            )
            candidates = _merge_live_candidates(candidates, google_candidates)
            warnings.extend(google_warnings)

    if not use_live_data:
        candidates = fallback_places(lat, lon, radius_km)
        warnings.append(t(lang, "warn_places_disabled"))
    elif len(candidates) < 8:
        warnings.append(t(lang, "warn_places_limited"))

    return candidates, warnings
