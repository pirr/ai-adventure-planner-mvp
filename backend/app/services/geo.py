from __future__ import annotations

import math


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return radius * c


def google_maps_url(origin_lat: float, origin_lon: float, dest_lat: float, dest_lon: float, mode: str) -> str:
    mode_map = {"car": "driving", "walk": "walking", "bike": "bicycling"}
    travelmode = mode_map.get(mode, "driving")
    return (
        "https://www.google.com/maps/dir/?api=1"
        f"&origin={origin_lat:.6f},{origin_lon:.6f}"
        f"&destination={dest_lat:.6f},{dest_lon:.6f}"
        f"&travelmode={travelmode}"
    )
