import asyncio

import httpx

from app.schemas import AdventureRequest, PlaceCandidate, WeatherSummary
from app.services import recommendations, routing
from app.services.llm import TemplateProvider
from app.services.storage import Storage


def _place(
    lat: float = 42.4870,
    lon: float = 18.6982,
    source_id: str = "osm:node:1",
    name: str = "Perast",
) -> PlaceCandidate:
    return PlaceCandidate(
        source="openstreetmap",
        source_id=source_id,
        name=name,
        type="historic_site",
        lat=lat,
        lon=lon,
    )


def _mock_osrm(monkeypatch, payload: dict, seen: list[httpx.Request] | None = None) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(200, json=payload)

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(routing, "http_client", lambda timeout: httpx.AsyncClient(transport=transport))


# Tivat -> Perast is only ~5.5 km in a straight line, but the drive around the
# Bay of Kotor is ~25.4 km. OSRM reports the real road distance.
OSRM_PAYLOAD = {"routes": [{"distance": 25400.0, "duration": 2280.0}]}
OSRM_TABLE_PAYLOAD = {
    "durations": [[120.0, 300.0]],
    "distances": [[1000.0, 5000.0]],
}


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


def test_table_routes_multiple_destinations_in_one_request(monkeypatch):
    seen: list[httpx.Request] = []
    places = [
        _place(source_id="osm:node:1", name="First"),
        _place(lat=42.4900, lon=18.7100, source_id="osm:node:2", name="Second"),
    ]
    _mock_osrm(monkeypatch, OSRM_TABLE_PAYLOAD, seen)

    routes = asyncio.run(routing.osrm_table_routes(42.4380, 18.6936, places, "car"))

    assert len(seen) == 1
    assert seen[0].url.path.startswith("/table/v1/driving/")
    assert seen[0].url.params.get("sources") == "0"
    assert seen[0].url.params.get("destinations") == "1;2"
    assert seen[0].url.params.get("annotations") == "duration,distance"
    assert [route.source for route in routes] == ["osrm", "osrm"]
    assert [route.distance_km for route in routes] == [1.0, 5.0]
    assert [route.one_way_minutes for route in routes] == [2, 5]


def test_table_walk_uses_road_distance_not_osrm_duration(monkeypatch):
    _mock_osrm(monkeypatch, {"durations": [[2280.0]], "distances": [[25400.0]]})
    route = asyncio.run(routing.osrm_table_routes(42.4380, 18.6936, [_place()], "walk"))[0]

    assert route.source == "osrm"
    assert route.distance_km == 25.4
    assert route.one_way_minutes == round(25.4 / routing.SPEED_KMH["walk"] * 60)


def test_table_routes_fall_back_per_missing_cell(monkeypatch):
    places = [_place(source_id="osm:node:1"), _place(source_id="osm:node:2")]
    _mock_osrm(monkeypatch, {"durations": [[120.0, None]], "distances": [[1000.0, None]]})

    routes = asyncio.run(routing.osrm_table_routes(42.4380, 18.6936, places, "car"))

    assert routes[0].source == "osrm"
    assert routes[1].source == "haversine-estimate"
    assert routes[1].confidence == "estimated"


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


def test_get_routes_falls_back_all_when_table_request_fails(monkeypatch, caplog):
    def boom(timeout):
        raise RuntimeError("osrm down")

    places = [_place(source_id="osm:node:1"), _place(source_id="osm:node:2")]
    monkeypatch.setattr(routing, "http_client", boom)
    with caplog.at_level("WARNING"):
        routes = asyncio.run(routing.get_routes(42.4380, 18.6936, places, "walk", use_live_data=True))

    assert [route.source for route in routes] == ["haversine-estimate", "haversine-estimate"]
    assert any("batch routing failed" in rec.message.lower() for rec in caplog.records)


def test_get_routes_live_disabled_makes_no_osrm_call(monkeypatch):
    def fail(timeout):
        raise AssertionError("OSRM should not be called when live data is off")

    monkeypatch.setattr(routing, "http_client", fail)
    routes = asyncio.run(routing.get_routes(42.4380, 18.6936, [_place()], "car", use_live_data=False))
    assert routes[0].source == "haversine-estimate"


def test_recommendations_use_one_osrm_table_request(monkeypatch, tmp_path):
    seen: list[httpx.Request] = []
    places = [
        _place(source_id="osm:node:1", name="First Fort"),
        _place(lat=42.4900, lon=18.7100, source_id="osm:node:2", name="Second Fort"),
    ]

    async def fake_weather(*args, **kwargs):
        return WeatherSummary(source="test", summary="clear", score=90), []

    async def fake_places(*args, **kwargs):
        return places, []

    async def fake_forecasts(points, *args, **kwargs):
        return [None] * len(points)

    async def fake_photo(*args, **kwargs):
        return None

    monkeypatch.setattr(recommendations, "storage", Storage(tmp_path / "recommendations.db"))
    monkeypatch.setattr(recommendations, "get_weather", fake_weather)
    monkeypatch.setattr(recommendations, "get_candidate_places", fake_places)
    monkeypatch.setattr(recommendations, "get_destination_forecasts", fake_forecasts)
    monkeypatch.setattr(recommendations, "get_place_photo", fake_photo)
    _mock_osrm(monkeypatch, {"durations": [[600.0, 900.0]], "distances": [[10000.0, 12000.0]]}, seen)

    response = asyncio.run(
        recommendations.build_recommendations(
            AdventureRequest(
                lat=42.4380,
                lon=18.6936,
                available_minutes=120,
                transport_mode="car",
                interests=["history"],
                use_live_data=True,
                limit=2,
            ),
            provider=TemplateProvider(),
        )
    )

    assert len(seen) == 1
    assert seen[0].url.path.startswith("/table/v1/driving/")
    assert len(response.recommendations) == 2
