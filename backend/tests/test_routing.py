import asyncio

import httpx

from app.schemas import PlaceCandidate
from app.services import routing


def _place(lat: float = 42.4870, lon: float = 18.6982) -> PlaceCandidate:
    return PlaceCandidate(
        source="openstreetmap",
        source_id="osm:node:1",
        name="Perast",
        type="historic_site",
        lat=lat,
        lon=lon,
    )


def _mock_osrm(monkeypatch, payload: dict, seen: list | None = None) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(str(request.url))
        return httpx.Response(200, json=payload)

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(routing, "http_client", lambda timeout: httpx.AsyncClient(transport=transport))


# Tivat -> Perast is only ~5.5 km in a straight line, but the drive around the
# Bay of Kotor is ~25.4 km. OSRM reports the real road distance.
OSRM_PAYLOAD = {"routes": [{"distance": 25400.0, "duration": 2280.0}]}


def test_walk_uses_real_road_distance_not_straight_line(monkeypatch):
    _mock_osrm(monkeypatch, OSRM_PAYLOAD)
    route = asyncio.run(routing.osrm_route(42.4380, 18.6936, _place(), "walk"))
    assert route.source == "osrm"
    assert route.confidence == "live"
    assert route.distance_km == 25.4
    # walk pace applied to the *road* distance, not the crow-flies distance
    assert route.one_way_minutes == round(25.4 / routing.SPEED_KMH["walk"] * 60)


def test_bike_uses_real_road_distance(monkeypatch):
    _mock_osrm(monkeypatch, OSRM_PAYLOAD)
    route = asyncio.run(routing.osrm_route(42.4380, 18.6936, _place(), "bike"))
    assert route.source == "osrm"
    assert route.distance_km == 25.4
    assert route.one_way_minutes == round(25.4 / routing.SPEED_KMH["bike"] * 60)


def test_car_keeps_osrm_duration(monkeypatch):
    _mock_osrm(monkeypatch, OSRM_PAYLOAD)
    route = asyncio.run(routing.osrm_route(42.4380, 18.6936, _place(), "car"))
    assert route.source == "osrm"
    assert route.distance_km == 25.4
    assert route.one_way_minutes == round(2280.0 / 60)  # OSRM's real driving time


def test_get_route_falls_back_and_logs_when_osrm_fails(monkeypatch, caplog):
    def boom(timeout):
        raise RuntimeError("osrm down")

    monkeypatch.setattr(routing, "http_client", boom)
    with caplog.at_level("WARNING"):
        route = asyncio.run(
            routing.get_route(42.4380, 18.6936, _place(), "walk", use_live_data=True)
        )
    assert route.source == "haversine-estimate"
    assert route.confidence == "estimated"
    assert any("osrm" in rec.message.lower() for rec in caplog.records)
