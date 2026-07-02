from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from app.config import settings
from app.schemas import PlaceCandidate
from app.services import google_places
from app.services.i18n import t
from app.services.net import http_client
from app.services.sample_data import fallback_places
from app.services.scoring import normalize_interest, place_matches_interest


logger = logging.getLogger(__name__)

# Server-busy statuses worth a quick same-endpoint retry (momentary hiccup).
_RETRY_OVERPASS_STATUS = {502, 503, 504}
# Rate-limit / no-free-slot statuses. A quick retry won't clear these (slots
# refill over seconds), so fail over to the next endpoint / sample places now.
_FAILOVER_OVERPASS_STATUS = {406, 429}

# Cap (metres) for the dense food/drink amenity scan. Larger radii overrun
# Overpass's per-query timeout and the whole query returns nothing.
AMENITY_MAX_RADIUS_M = 8000
# Route relations are numerous and expensive. Keep them local so a broader
# car-radius nature search does not sink the whole Overpass query.
ROUTE_MAX_RADIUS_M = 8000

# {cache_key: (expires_at, candidates)}. Candidates are deep-copied on both
# read and write so downstream enrichment does not mutate cached entries.
_candidate_cache: dict[tuple[Any, ...], tuple[float, list[PlaceCandidate]]] = {}


class OverpassRuntimeError(RuntimeError):
    """Overpass returned HTTP 200 but aborted server-side (a timeout/runtime
    error reported in the JSON `remark`), so the payload is empty and unusable.
    Raised so the caller fails over to mirrors/sample places instead of
    silently treating it as 'no places nearby'."""


def _is_overpass_timeout_remark(payload: dict[str, Any]) -> bool:
    remark = str(payload.get("remark") or "")
    return "timed out" in remark or "runtime error" in remark


def _clone_candidates(candidates: list[PlaceCandidate]) -> list[PlaceCandidate]:
    return [
        candidate.model_copy(deep=True) if hasattr(candidate, "model_copy") else candidate.copy(deep=True)
        for candidate in candidates
    ]


def _cache_key(lat: float, lon: float, radius_km: float, interests: list[str]) -> tuple[Any, ...]:
    normalized = tuple(sorted(normalize_interest(str(interest)) for interest in interests))
    return ("osm-v2", round(lat, 3), round(lon, 3), round(radius_km, 1), normalized)


def _candidate_cache_get(key: tuple[Any, ...]) -> list[PlaceCandidate] | None:
    if settings.search_candidate_cache_ttl_seconds <= 0:
        return None
    cached = _candidate_cache.get(key)
    if cached is None:
        return None
    expires_at, candidates = cached
    if expires_at <= time.time():
        _candidate_cache.pop(key, None)
        return None
    return _clone_candidates(candidates)


def _candidate_cache_set(key: tuple[Any, ...], candidates: list[PlaceCandidate]) -> None:
    ttl = settings.search_candidate_cache_ttl_seconds
    max_entries = settings.search_candidate_cache_max_entries
    if ttl <= 0 or max_entries <= 0:
        return
    now = time.time()
    for cache_key, (expires_at, _) in list(_candidate_cache.items()):
        if expires_at <= now:
            _candidate_cache.pop(cache_key, None)
    while len(_candidate_cache) >= max_entries:
        _candidate_cache.pop(next(iter(_candidate_cache)))
    _candidate_cache[key] = (now + ttl, _clone_candidates(candidates))


def _search_radius_steps(full_radius_km: float) -> list[float]:
    if not settings.search_progressive_enabled:
        return [full_radius_km]
    radii = [radius for radius in sorted(settings.search_radius_tiers_km) if 0 < radius < full_radius_km]
    radii.append(full_radius_km)
    deduped: list[float] = []
    seen: set[float] = set()
    for radius in radii:
        marker = round(radius, 2)
        if marker in seen:
            continue
        seen.add(marker)
        deduped.append(radius)
    return deduped


def _merge_candidates(primary: list[PlaceCandidate], secondary: list[PlaceCandidate]) -> list[PlaceCandidate]:
    merged = list(primary)
    seen = {candidate.source_id for candidate in merged}
    for candidate in secondary:
        if candidate.source_id in seen:
            continue
        merged.append(candidate)
        seen.add(candidate.source_id)
    return merged


def _matching_candidate_count(candidates: list[PlaceCandidate], interests: list[str]) -> int:
    requested = [normalize_interest(str(interest)) for interest in interests]
    requested = [interest for interest in requested if interest and interest != "surprise me"]
    if not requested:
        return len(candidates)
    return sum(1 for candidate in candidates if any(place_matches_interest(candidate, interest) for interest in requested))


def _has_enough_osm_candidates(candidates: list[PlaceCandidate], interests: list[str]) -> bool:
    target = max(8, settings.search_osm_target_candidates)
    if len(candidates) < target:
        return False

    requested = [normalize_interest(str(interest)) for interest in interests if str(interest).strip()]
    focused = requested[0] if len(requested) == 1 else None
    if focused in {"food", "drinks"}:
        # Food/drink amenities are only scanned locally by design. If the local
        # ring is sparse, Google candidate search is the better bounded backfill
        # than a city-scale broad Overpass scan.
        return _matching_candidate_count(candidates, interests) >= 5 or len(candidates) >= target
    if focused and focused != "surprise me":
        return _matching_candidate_count(candidates, interests) >= min(10, max(5, target // 3))

    if not requested or "surprise me" in requested:
        return len({candidate.type for candidate in candidates}) >= 3
    covered = sum(
        1 for interest in requested
        if any(place_matches_interest(candidate, interest) for candidate in candidates)
    )
    return covered >= min(len(requested), 2)


INTEREST_OSM_FILTERS = {
    "nature": [
        "tourism=viewpoint",
        "tourism=picnic_site",
        "leisure=park",
        "leisure=nature_reserve",
        "natural=beach",
        "natural=water",
        "natural=cave_entrance",
        "waterway=waterfall",
        "route=hiking",
        "route=foot",
    ],
    "caves": ["natural=cave_entrance"],
    "viewpoints": ["tourism=viewpoint"],
    "history": ["historic", "tourism=museum", "tourism=attraction"],
    "fortresses": ["historic=fort", "historic=castle", "castle_type"],
    "water": [
        "natural=beach",
        "natural=water",
        "natural=spring",
        "natural=hot_spring",
        "waterway=waterfall",
        "waterway=rapids",
    ],
    "food": ["amenity=cafe", "amenity=restaurant", "amenity=fast_food"],
    "drinks": ["amenity=bar", "amenity=pub", "amenity=biergarten"],
    "surprise me": [
        "tourism=viewpoint",
        "historic",
        "leisure=park",
        "tourism=attraction",
        "tourism=picnic_site",
    ],
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
    normalized = {normalize_interest(str(interest)) for interest in interests if str(interest).strip()}
    broad = not normalized or "surprise me" in normalized
    blocks: list[str] = []

    def add(block: str) -> None:
        if block not in blocks:
            blocks.append(block)

    if broad or normalized & {"viewpoints", "nature"}:
        add(f"""
  node(around:{radius_m},{lat},{lon})["tourism"="viewpoint"];
  way(around:{radius_m},{lat},{lon})["tourism"="viewpoint"];
  relation(around:{radius_m},{lat},{lon})["tourism"="viewpoint"];
""")
    if broad or normalized & {"history", "fortresses"}:
        add(f"""
  node(around:{radius_m},{lat},{lon})["tourism"~"attraction|museum|gallery"];
  way(around:{radius_m},{lat},{lon})["tourism"~"attraction|museum|gallery"];
  relation(around:{radius_m},{lat},{lon})["tourism"~"attraction|museum|gallery"];
  node(around:{radius_m},{lat},{lon})["historic"];
  way(around:{radius_m},{lat},{lon})["historic"];
  relation(around:{radius_m},{lat},{lon})["historic"];
""")
    if broad:
        add(f"""
  node(around:{radius_m},{lat},{lon})["tourism"="zoo"];
  way(around:{radius_m},{lat},{lon})["tourism"="zoo"];
  relation(around:{radius_m},{lat},{lon})["tourism"="zoo"];
""")
    if broad or "caves" in normalized:
        add(f"""
  node(around:{radius_m},{lat},{lon})["natural"="cave_entrance"];
  way(around:{radius_m},{lat},{lon})["natural"="cave_entrance"];
""")
    if broad or "nature" in normalized:
        route_radius = min(radius_m, ROUTE_MAX_RADIUS_M)
        add(f"""
  node(around:{radius_m},{lat},{lon})["tourism"~"picnic_site|alpine_hut|wilderness_hut"]["name"];
  way(around:{radius_m},{lat},{lon})["tourism"~"picnic_site|alpine_hut|wilderness_hut"]["name"];
  relation(around:{radius_m},{lat},{lon})["tourism"~"picnic_site|alpine_hut|wilderness_hut"]["name"];
  node(around:{radius_m},{lat},{lon})["leisure"="park"];
  way(around:{radius_m},{lat},{lon})["leisure"="park"];
  node(around:{radius_m},{lat},{lon})["leisure"~"nature_reserve|garden|playground|dog_park|swimming_area|bathing_place|bird_hide"]["name"];
  way(around:{radius_m},{lat},{lon})["leisure"~"nature_reserve|garden|playground|dog_park|swimming_area|bathing_place|bird_hide"]["name"];
  relation(around:{radius_m},{lat},{lon})["leisure"~"nature_reserve|garden|playground|dog_park|swimming_area|bathing_place|bird_hide"]["name"];
  node(around:{radius_m},{lat},{lon})["natural"~"beach|water|peak|cave_entrance"];
  way(around:{radius_m},{lat},{lon})["natural"~"beach|water|peak|cave_entrance"];
  node(around:{radius_m},{lat},{lon})["natural"~"spring|hot_spring|arch|rock|stone|sinkhole|volcano|cape|bay|saddle"]["name"];
  way(around:{radius_m},{lat},{lon})["natural"~"spring|hot_spring|arch|rock|stone|sinkhole|volcano|cape|bay|saddle"]["name"];
  node(around:{radius_m},{lat},{lon})["waterway"="waterfall"];
  node(around:{radius_m},{lat},{lon})["waterway"="rapids"]["name"];
  way(around:{radius_m},{lat},{lon})["waterway"="rapids"]["name"];
  relation(around:{route_radius},{lat},{lon})["route"~"hiking|foot|running|bicycle|mtb"]["name"];
""")
    if broad or "water" in normalized:
        add(f"""
  node(around:{radius_m},{lat},{lon})["natural"~"beach|water|spring|hot_spring|bay"];
  way(around:{radius_m},{lat},{lon})["natural"~"beach|water|spring|hot_spring|bay"];
  node(around:{radius_m},{lat},{lon})["waterway"="waterfall"];
  node(around:{radius_m},{lat},{lon})["waterway"="rapids"]["name"];
  way(around:{radius_m},{lat},{lon})["waterway"="rapids"]["name"];
  node(around:{radius_m},{lat},{lon})["leisure"~"swimming_area|bathing_place"]["name"];
  way(around:{radius_m},{lat},{lon})["leisure"~"swimming_area|bathing_place"]["name"];
  relation(around:{radius_m},{lat},{lon})["leisure"~"swimming_area|bathing_place"]["name"];
""")
    if normalized & {"food", "drinks"}:
        # Food/drink amenities are dense; scanning them over a city-scale radius
        # blows past Overpass's per-query [timeout:10] and the whole query
        # returns zero elements. Cap the amenity search to a local radius — a
        # bar/cafe 50km away is never a good "near me" result anyway.
        amenity_radius = min(radius_m, AMENITY_MAX_RADIUS_M)
        add(f"""
  node(around:{amenity_radius},{lat},{lon})["amenity"~"restaurant|cafe|bar|pub|fast_food|ice_cream|biergarten"];
  way(around:{amenity_radius},{lat},{lon})["amenity"~"restaurant|cafe|bar|pub|fast_food|ice_cream|biergarten"];
  relation(around:{amenity_radius},{lat},{lon})["amenity"~"restaurant|cafe|bar|pub|fast_food|ice_cream|biergarten"];
""")
    if not blocks:
        add(f"""
  node(around:{radius_m},{lat},{lon})["tourism"~"viewpoint|attraction|museum"];
  way(around:{radius_m},{lat},{lon})["tourism"~"viewpoint|attraction|museum"];
  relation(around:{radius_m},{lat},{lon})["tourism"~"viewpoint|attraction|museum"];
""")

    query_blocks = "".join(blocks)
    return f"""
[out:json][timeout:10];
(
{query_blocks}
);
out center tags 80;
"""


def _place_type_from_tags(tags: dict[str, Any]) -> str:
    historic = tags.get("historic")
    tourism = tags.get("tourism")
    leisure = tags.get("leisure")
    natural = tags.get("natural")
    amenity = tags.get("amenity")
    waterway = tags.get("waterway")
    route = tags.get("route")
    if historic in {"fort", "castle", "citywalls", "archaeological_site"}:
        return "fortress" if historic in {"fort", "castle", "citywalls"} else "historic_site"
    if historic:
        return "historic_site"
    if tourism == "viewpoint":
        return "viewpoint"
    if tourism == "museum":
        return "museum"
    if tourism == "picnic_site":
        return "picnic"
    if tourism in {"alpine_hut", "wilderness_hut"}:
        return "trail"
    if tourism:
        return "attraction"
    if leisure in {"swimming_area", "bathing_place"}:
        return "water"
    if leisure in {"park", "nature_reserve", "garden", "playground", "dog_park", "bird_hide"}:
        return "park"
    if natural in {"beach", "water", "spring", "hot_spring", "bay"}:
        return "water"
    if waterway in {"waterfall", "rapids"}:
        return "water"
    if natural in {"peak", "cape", "saddle"}:
        return "viewpoint"
    if natural == "cave_entrance":
        return "cave"
    if natural in {"arch", "rock", "stone", "sinkhole", "volcano"}:
        return "natural_site"
    if route in {"hiking", "foot", "running", "bicycle", "mtb"}:
        return "trail"
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
        "cave": 45,
        "food": 45,
        "drinks": 50,
        "natural_site": 45,
        "picnic": 45,
        "trail": 100,
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
        "cave": 1.5,
        "food": 0.4,
        "drinks": 0.3,
        "natural_site": 1.2,
        "picnic": 0.6,
        "trail": 4.0,
    }.get(place_type, 1.0)


def _estimate_difficulty(place_type: str) -> str:
    if place_type in {"viewpoint", "cave", "natural_site", "trail"}:
        return "medium"
    return "easy"


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
                payload = response.json()
                if not payload.get("elements") and _is_overpass_timeout_remark(payload):
                    # Server-side abort (HTTP 200 + remark, no data). Treat like
                    # a busy endpoint: retry, then fail over to the next one.
                    last_exc = OverpassRuntimeError(str(payload.get("remark")))
                    if attempt + 1 < attempts:
                        await asyncio.sleep(settings.overpass_retry_backoff_seconds * (attempt + 1))
                        continue
                    break
                return payload
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
    key = _cache_key(lat, lon, radius_km, interests)
    cached = _candidate_cache_get(key)
    if cached is not None:
        logger.info("place_search_osm_cache_hit radius_km=%.1f candidates=%d", radius_km, len(cached))
        return cached

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
                difficulty=_estimate_difficulty(place_type),
                quality_score=_quality_from_tags(tags, True),
            )
        )
    _candidate_cache_set(key, candidates)
    return candidates


async def _fetch_osm_places_progressive(
    lat: float,
    lon: float,
    radius_km: float,
    interests: list[str],
) -> list[PlaceCandidate]:
    candidates: list[PlaceCandidate] = []
    for search_radius_km in _search_radius_steps(radius_km):
        started = time.perf_counter()
        stage = await fetch_osm_places(lat, lon, search_radius_km, interests)
        candidates = _merge_candidates(candidates, stage)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "place_search_osm_stage radius_km=%.1f fetched=%d merged=%d duration_ms=%d",
            search_radius_km,
            len(stage),
            len(candidates),
            elapsed_ms,
        )
        if _has_enough_osm_candidates(candidates, interests):
            break
    return candidates


def _needs_google_candidates(candidates: list[PlaceCandidate], interests: list[str]) -> bool:
    normalized = {str(interest).strip().lower() for interest in interests}
    if len(candidates) < 8:
        return True
    focused = [normalize_interest(str(interest)) for interest in interests if str(interest).strip()]
    if len(focused) == 1 and focused[0] != "surprise me":
        return _matching_candidate_count(candidates, focused) < 3
    if "food" in normalized and sum(candidate.type == "food" for candidate in candidates) < 5:
        return True
    if "drinks" in normalized and sum(candidate.type == "drinks" for candidate in candidates) < 5:
        return True
    return False


def _merge_live_candidates(primary: list[PlaceCandidate], secondary: list[PlaceCandidate]) -> list[PlaceCandidate]:
    return _merge_candidates(primary, secondary)


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
            candidates = await _fetch_osm_places_progressive(lat, lon, radius_km, interests)
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
