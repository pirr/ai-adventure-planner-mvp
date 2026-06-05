from __future__ import annotations

import httpx

from app.config import settings
from app.schemas import PlaceCandidate, RouteInfo
from app.services.geo import google_maps_url, haversine_km


SPEED_KMH = {
    "walk": 4.5,
    "bike": 14.0,
    "car": 42.0,
}


def fallback_route(origin_lat: float, origin_lon: float, place: PlaceCandidate, transport_mode: str) -> RouteInfo:
    distance = haversine_km(origin_lat, origin_lon, place.lat, place.lon)
    speed = SPEED_KMH.get(transport_mode, 35.0)
    one_way = max(4, int(round((distance / speed) * 60)))
    if transport_mode == "car":
        one_way += 4  # parking / local roads buffer
    return RouteInfo(
        source="haversine-estimate",
        one_way_minutes=one_way,
        round_trip_minutes=one_way * 2,
        distance_km=round(distance, 2),
        map_url=google_maps_url(origin_lat, origin_lon, place.lat, place.lon, transport_mode),
        confidence="estimated",
    )


async def osrm_route(origin_lat: float, origin_lon: float, place: PlaceCandidate, transport_mode: str) -> RouteInfo:
    # Public OSRM demo server is primarily reliable for driving. Other modes use fallback estimates.
    if transport_mode != "car":
        return fallback_route(origin_lat, origin_lon, place, transport_mode)
    coords = f"{origin_lon:.6f},{origin_lat:.6f};{place.lon:.6f},{place.lat:.6f}"
    url = f"{settings.osrm_url}/route/v1/driving/{coords}"
    params = {"overview": "false", "alternatives": "false", "steps": "false"}
    async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
        response = await client.get(url, params=params)
        response.raise_for_status()
        data = response.json()
    route = (data.get("routes") or [None])[0]
    if not route:
        raise RuntimeError("OSRM returned no route")
    one_way = max(2, int(round(float(route["duration"]) / 60)))
    distance = round(float(route["distance"]) / 1000, 2)
    return RouteInfo(
        source="osrm",
        one_way_minutes=one_way,
        round_trip_minutes=one_way * 2,
        distance_km=distance,
        map_url=google_maps_url(origin_lat, origin_lon, place.lat, place.lon, transport_mode),
        confidence="live",
    )


async def get_route(origin_lat: float, origin_lon: float, place: PlaceCandidate, transport_mode: str, use_live_data: bool) -> RouteInfo:
    if use_live_data:
        try:
            return await osrm_route(origin_lat, origin_lon, place, transport_mode)
        except Exception:
            pass
    return fallback_route(origin_lat, origin_lon, place, transport_mode)
