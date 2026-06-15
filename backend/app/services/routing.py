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


def _osrm_route_info(
    origin_lat: float,
    origin_lon: float,
    place: PlaceCandidate,
    transport_mode: str,
    distance_m: float,
    duration_s: float,
) -> RouteInfo:
    distance = round(float(distance_m) / 1000, 2)
    if transport_mode == "car":
        one_way = max(2, int(round(float(duration_s) / 60)))
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
    return _osrm_route_info(origin_lat, origin_lon, place, transport_mode, route["distance"], route["duration"])


async def osrm_table_routes(
    origin_lat: float,
    origin_lon: float,
    places: list[PlaceCandidate],
    transport_mode: str,
) -> list[RouteInfo]:
    # One OSRM Table call replaces N route calls. The public demo still uses
    # the driving network, so walking/cycling keep the same road-distance logic
    # as osrm_route().
    if not places:
        return []
    coords = ";".join(
        [f"{origin_lon:.6f},{origin_lat:.6f}"]
        + [f"{place.lon:.6f},{place.lat:.6f}" for place in places]
    )
    url = f"{settings.osrm_url}/table/v1/driving/{coords}"
    params = {
        "sources": "0",
        "destinations": ";".join(str(index) for index in range(1, len(places) + 1)),
        "annotations": "duration,distance",
    }
    async with http_client(settings.http_timeout_seconds) as client:
        response = await client.get(url, params=params)
        response.raise_for_status()
        data = response.json()

    duration_row = (data.get("durations") or [[]])[0]
    distance_row = (data.get("distances") or [[]])[0]
    routes: list[RouteInfo] = []
    for index, place in enumerate(places):
        try:
            duration_s = duration_row[index]
            distance_m = distance_row[index]
        except (IndexError, TypeError):
            duration_s = distance_m = None
        if duration_s is None or distance_m is None:
            routes.append(fallback_route(origin_lat, origin_lon, place, transport_mode))
            continue
        routes.append(_osrm_route_info(origin_lat, origin_lon, place, transport_mode, distance_m, duration_s))
    return routes


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


async def get_routes(
    origin_lat: float,
    origin_lon: float,
    places: list[PlaceCandidate],
    transport_mode: str,
    use_live_data: bool,
) -> list[RouteInfo]:
    if not use_live_data:
        return [fallback_route(origin_lat, origin_lon, place, transport_mode) for place in places]
    try:
        return await osrm_table_routes(origin_lat, origin_lon, places, transport_mode)
    except Exception as exc:
        logger.warning(
            "OSRM batch routing failed for %s toward %d places (%s); using straight-line estimates",
            transport_mode,
            len(places),
            exc,
        )
        return [fallback_route(origin_lat, origin_lon, place, transport_mode) for place in places]
