from __future__ import annotations

import logging

from app.config import settings
from app.schemas import PlaceCandidate, RouteInfo
from app.services.geo import apple_maps_url, google_maps_url, haversine_km
from app.services.net import http_client

logger = logging.getLogger(__name__)


SPEED_KMH = {
    "walk": 4.5,
    "bike": 14.0,
    "car": 42.0,
}

# Straight-line distance understates real road distance because roads bend
# around bays, rivers, and mountains (a Bay of Kotor crossing is ~5 km as the
# crow flies but ~25 km by road). Only the offline fallback uses this factor;
# live routing measures the real road network instead.
DETOUR_FACTOR = 1.4


def _travel_minutes(distance_km: float, transport_mode: str) -> int:
    speed = SPEED_KMH.get(transport_mode, 35.0)
    minutes = max(4, int(round((distance_km / speed) * 60)))
    if transport_mode == "car":
        minutes += 4  # parking / local roads buffer
    return minutes


def fallback_route(origin_lat: float, origin_lon: float, place: PlaceCandidate, transport_mode: str) -> RouteInfo:
    straight = haversine_km(origin_lat, origin_lon, place.lat, place.lon)
    distance = round(straight * DETOUR_FACTOR, 2)
    one_way = _travel_minutes(distance, transport_mode)
    return RouteInfo(
        source="haversine-estimate",
        one_way_minutes=one_way,
        round_trip_minutes=one_way * 2,
        distance_km=distance,
        map_url=google_maps_url(origin_lat, origin_lon, place.lat, place.lon, transport_mode),
        apple_map_url=apple_maps_url(origin_lat, origin_lon, place.lat, place.lon, transport_mode),
        confidence="estimated",
    )


async def osrm_route(origin_lat: float, origin_lon: float, place: PlaceCandidate, transport_mode: str) -> RouteInfo:
    # The public OSRM demo only serves the driving network, so we use it for the
    # real road *distance* in every mode. Driving time comes straight from OSRM;
    # walking/cycling time is that road distance at the mode's own pace, which is
    # far closer to reality than a straight-line estimate.
    coords = f"{origin_lon:.6f},{origin_lat:.6f};{place.lon:.6f},{place.lat:.6f}"
    url = f"{settings.osrm_url}/route/v1/driving/{coords}"
    params = {"overview": "false", "alternatives": "false", "steps": "false"}
    async with http_client(settings.http_timeout_seconds) as client:
        response = await client.get(url, params=params)
        response.raise_for_status()
        data = response.json()
    route = (data.get("routes") or [None])[0]
    if not route:
        raise RuntimeError("OSRM returned no route")
    distance = round(float(route["distance"]) / 1000, 2)
    if transport_mode == "car":
        one_way = max(2, int(round(float(route["duration"]) / 60)))
    else:
        one_way = _travel_minutes(distance, transport_mode)
    return RouteInfo(
        source="osrm",
        one_way_minutes=one_way,
        round_trip_minutes=one_way * 2,
        distance_km=distance,
        map_url=google_maps_url(origin_lat, origin_lon, place.lat, place.lon, transport_mode),
        apple_map_url=apple_maps_url(origin_lat, origin_lon, place.lat, place.lon, transport_mode),
        confidence="live",
    )


async def get_route(origin_lat: float, origin_lon: float, place: PlaceCandidate, transport_mode: str, use_live_data: bool) -> RouteInfo:
    if use_live_data:
        try:
            return await osrm_route(origin_lat, origin_lon, place, transport_mode)
        except Exception as exc:
            logger.warning(
                "OSRM routing failed for %s toward %s (%s); using straight-line estimate",
                transport_mode, place.name, exc,
            )
    return fallback_route(origin_lat, origin_lon, place, transport_mode)
