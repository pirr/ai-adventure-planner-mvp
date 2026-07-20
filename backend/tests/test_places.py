import asyncio
import dataclasses
import time

import httpx
import pytest

from app.config import settings as real_settings
from app.schemas import PlaceCandidate
from app.services import places
from app.services.places import _build_overpass_query, _place_type_from_tags
from app.services.storage import Storage


_AMENITY_REGEX = '"amenity"~"restaurant|cafe|bar|pub|fast_food|ice_cream|biergarten"'
_ADVENTURE_TOURISM_REGEX = '"tourism"~"picnic_site|alpine_hut|wilderness_hut"'
_ADVENTURE_LEISURE_REGEX = (
    '"leisure"~"nature_reserve|garden|playground|dog_park|swimming_area|bathing_place|bird_hide"'
)
_ADVENTURE_NATURAL_REGEX = (
    '"natural"~"spring|hot_spring|arch|rock|stone|sinkhole|volcano|cape|bay|saddle"'
)
_ADVENTURE_ROUTE_REGEX = '"route"~"hiking|foot|running|bicycle|mtb"'


def _settings(**overrides):
    return dataclasses.replace(real_settings, **overrides)


def _candidate(index: int, place_type: str = "fortress") -> PlaceCandidate:
    return PlaceCandidate(
        source="openstreetmap",
        source_id=f"osm:node:{index}",
        name=f"Place {index}",
        type=place_type,
        lat=42.43 + index * 0.001,
        lon=18.69,
    )


def test_food_interest_adds_amenities_to_overpass_query():
    query = _build_overpass_query(42.43, 18.69, 25000, ["food", "history"])

    assert _AMENITY_REGEX in query


def test_drinks_interest_adds_amenities_to_overpass_query():
    query = _build_overpass_query(42.43, 18.69, 25000, ["drinks"])

    assert _AMENITY_REGEX in query


def test_non_food_interest_keeps_amenities_out_of_overpass_query():
    query = _build_overpass_query(42.43, 18.69, 25000, ["history", "fortresses"])

    assert _AMENITY_REGEX not in query


def test_adventure_tags_are_included_in_overpass_query():
    query = _build_overpass_query(42.43, 18.69, 25000, ["nature"])

    assert _ADVENTURE_TOURISM_REGEX in query
    assert _ADVENTURE_LEISURE_REGEX in query
    assert _ADVENTURE_NATURAL_REGEX in query
    assert '"waterway"="rapids"' in query
    assert _ADVENTURE_ROUTE_REGEX in query
    assert "camp_site" not in query


def test_caves_interest_uses_small_targeted_overpass_query():
    query = _build_overpass_query(42.43, 18.69, 25000, ["caves"])

    assert '"natural"="cave_entrance"' in query
    assert '"historic"' not in query
    assert '"route"~"hiking|foot|running|bicycle|mtb"' not in query
    assert _ADVENTURE_NATURAL_REGEX not in query


def test_amenity_block_radius_is_capped_for_large_radius():
    # The dense amenity scan blows past Overpass's per-query timeout at city
    # scale, so it is capped to a local radius while base blocks keep the full one.
    from app.services.places import AMENITY_MAX_RADIUS_M

    query = _build_overpass_query(42.43, 18.69, 25000, ["drinks"])
    assert f'around:{AMENITY_MAX_RADIUS_M},42.43,18.69)["amenity"' in query
    assert 'around:25000,42.43,18.69)["amenity"' not in query


def test_amenity_block_uses_full_radius_when_within_cap():
    query = _build_overpass_query(42.43, 18.69, 5000, ["drinks"])
    assert 'around:5000,42.43,18.69)["amenity"' in query


def test_progressive_radius_steps_include_full_radius(monkeypatch):
    monkeypatch.setattr(
        places,
        "settings",
        _settings(search_progressive_enabled=True, search_radius_tiers_km=(8, 25, 55)),
    )

    assert places._search_radius_steps(5) == [5]
    assert places._search_radius_steps(55) == [8, 25, 55]
    assert places._search_radius_steps(90) == [8, 25, 55, 90]


def test_progressive_osm_search_stops_when_first_ring_is_strong(monkeypatch):
    calls = []

    async def fake_fetch(lat, lon, radius_km, interests, timeout_seconds=None):
        calls.append(radius_km)
        return [_candidate(index) for index in range(35)]

    monkeypatch.setattr(
        places,
        "settings",
        _settings(search_progressive_enabled=True, search_radius_tiers_km=(8, 25, 55), search_osm_target_candidates=32),
    )
    monkeypatch.setattr(places, "fetch_osm_places", fake_fetch)

    candidates = asyncio.run(places._fetch_osm_places_progressive(42.43, 18.69, 55, ["history"]))

    assert len(candidates) == 35
    assert calls == [8]


def test_osm_candidate_cache_reuses_cloned_results(monkeypatch, tmp_path):
    calls = []
    places._candidate_cache.clear()
    monkeypatch.setattr(places, "storage", Storage(tmp_path / "places.db"))

    async def fake_overpass(client, query):
        calls.append(query)
        return {
            "elements": [
                {
                    "type": "node",
                    "id": 1,
                    "lat": 42.43,
                    "lon": 18.69,
                    "tags": {"name": "Original Fort", "historic": "castle"},
                }
            ]
        }

    monkeypatch.setattr(
        places,
        "settings",
        _settings(search_candidate_cache_ttl_seconds=60, search_candidate_cache_max_entries=8),
    )
    monkeypatch.setattr(places, "_overpass_request", fake_overpass)

    first = asyncio.run(places.fetch_osm_places(42.43, 18.69, 8, ["history"]))
    first[0].name = "Mutated"
    second = asyncio.run(places.fetch_osm_places(42.43, 18.69, 8, ["history"]))

    assert len(calls) == 1
    assert second[0].name == "Original Fort"


def test_osm_candidate_cache_persists_after_l1_clear(monkeypatch, tmp_path):
    calls = []
    places._candidate_cache.clear()
    monkeypatch.setattr(places, "storage", Storage(tmp_path / "places.db"))

    async def fake_overpass(client, query):
        calls.append(query)
        return {
            "elements": [
                {
                    "type": "node",
                    "id": 1,
                    "lat": 42.43,
                    "lon": 18.69,
                    "tags": {"name": "Persistent Fort", "historic": "castle"},
                }
            ]
        }

    monkeypatch.setattr(
        places,
        "settings",
        _settings(search_candidate_cache_ttl_seconds=60, search_candidate_cache_max_entries=8),
    )
    monkeypatch.setattr(places, "_overpass_request", fake_overpass)

    first = asyncio.run(places.fetch_osm_places(42.43, 18.69, 8, ["history"]))
    places._candidate_cache.clear()
    second = asyncio.run(places.fetch_osm_places(42.43, 18.69, 8, ["history"]))

    assert len(calls) == 1
    assert first[0].source_id == second[0].source_id
    assert second[0].name == "Persistent Fort"


def test_lipska_pecina_style_cave_node_becomes_cave_candidate(monkeypatch):
    places._candidate_cache.clear()

    async def fake_overpass(client, query):
        return {
            "elements": [
                {
                    "type": "node",
                    "id": 3599537244,
                    "lat": 42.3739938,
                    "lon": 18.9535409,
                    "tags": {
                        "name": "Lipska Pećina",
                        "name:en": "Lipa Cave",
                        "natural": "cave_entrance",
                        "access": "customers",
                        "fee": "yes",
                        "wikidata": "Q23808470",
                    },
                }
            ]
        }

    monkeypatch.setattr(
        places,
        "settings",
        _settings(search_candidate_cache_ttl_seconds=0),
    )
    monkeypatch.setattr(places, "_overpass_request", fake_overpass)

    candidates = asyncio.run(places.fetch_osm_places(42.3907, 18.9147, 8, ["caves"]))

    assert len(candidates) == 1
    cave = candidates[0]
    assert cave.source_id == "osm:node:3599537244"
    assert cave.name == "Lipska Pećina"
    assert cave.type == "cave"
    assert cave.difficulty == "medium"
    assert cave.quality_score >= 80


def test_overpass_remark_timeout_is_treated_as_failure():
    # Overpass reports a server-side timeout as HTTP 200 with a "remark" and no
    # elements; that must surface as an error so failover/fallback can engage.
    from app.services.places import _overpass_request

    def handler(request):
        return httpx.Response(200, json={"elements": [], "remark": 'runtime error: Query timed out in "query" after 11 seconds.'})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await _overpass_request(client, "q")

    with pytest.raises(Exception):
        asyncio.run(run())


def test_overpass_remark_with_partial_results_is_returned():
    # A remark alongside actual elements (partial results) is still usable.
    from app.services.places import _overpass_request

    def handler(request):
        return httpx.Response(200, json={"elements": [{"type": "node", "id": 1}], "remark": "timed out"})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await _overpass_request(client, "q")

    payload = asyncio.run(run())
    assert len(payload["elements"]) == 1


def test_overpass_request_returns_fast_hedged_mirror(monkeypatch):
    from app.services.places import _overpass_request

    seen = []

    async def handler(request):
        seen.append(str(request.url.host))
        if request.url.host == "primary.example":
            await asyncio.sleep(0.05)
            return httpx.Response(200, json={"elements": [{"type": "node", "id": 1}]})
        return httpx.Response(200, json={"elements": [{"type": "node", "id": 2}]})

    monkeypatch.setattr(
        places,
        "settings",
        _settings(
            overpass_url="https://primary.example/api",
            overpass_mirrors=("https://mirror.example/api",),
            overpass_hedge_delay_seconds=0.01,
            overpass_max_attempts=1,
        ),
    )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await _overpass_request(client, "q")

    started = time.perf_counter()
    payload = asyncio.run(run())

    assert payload["elements"][0]["id"] == 2
    assert {"primary.example", "mirror.example"} <= set(seen)
    assert time.perf_counter() - started < 0.08


def test_progressive_osm_search_returns_partial_results_on_total_timeout(monkeypatch):
    calls = []

    async def fake_fetch(lat, lon, radius_km, interests, timeout_seconds=None):
        calls.append(radius_km)
        if radius_km == 8:
            return [_candidate(1)]
        await asyncio.sleep(1)
        return [_candidate(2)]

    monkeypatch.setattr(
        places,
        "settings",
        _settings(
            search_progressive_enabled=True,
            search_radius_tiers_km=(8, 25),
            search_osm_target_candidates=32,
            search_osm_total_timeout_seconds=0.05,
        ),
    )
    monkeypatch.setattr(places, "fetch_osm_places", fake_fetch)

    candidates = asyncio.run(places._fetch_osm_places_progressive(42.43, 18.69, 25, ["history"]))

    assert [candidate.source_id for candidate in candidates] == ["osm:node:1"]
    assert calls == [8, 25]


def test_eat_amenities_become_food_places():
    assert _place_type_from_tags({"amenity": "restaurant"}) == "food"
    assert _place_type_from_tags({"amenity": "cafe"}) == "food"
    assert _place_type_from_tags({"amenity": "fast_food"}) == "food"


def test_drink_amenities_become_drinks_places():
    assert _place_type_from_tags({"amenity": "pub"}) == "drinks"
    assert _place_type_from_tags({"amenity": "bar"}) == "drinks"
    assert _place_type_from_tags({"amenity": "biergarten"}) == "drinks"


def test_adventure_natural_and_waterway_tags_become_specific_places():
    assert _place_type_from_tags({"natural": "cave_entrance"}) == "cave"
    assert _place_type_from_tags({"waterway": "waterfall"}) == "water"
    assert _place_type_from_tags({"waterway": "rapids"}) == "water"
    assert _place_type_from_tags({"natural": "spring"}) == "water"
    assert _place_type_from_tags({"natural": "hot_spring"}) == "water"
    assert _place_type_from_tags({"natural": "bay"}) == "water"
    assert _place_type_from_tags({"natural": "cape"}) == "viewpoint"
    assert _place_type_from_tags({"natural": "saddle"}) == "viewpoint"
    assert _place_type_from_tags({"natural": "arch"}) == "natural_site"
    assert _place_type_from_tags({"natural": "rock"}) == "natural_site"
    assert _place_type_from_tags({"natural": "stone"}) == "natural_site"
    assert _place_type_from_tags({"natural": "sinkhole"}) == "natural_site"
    assert _place_type_from_tags({"natural": "volcano"}) == "natural_site"


def test_adventure_tourism_leisure_and_route_tags_become_specific_places():
    assert _place_type_from_tags({"tourism": "picnic_site"}) == "picnic"
    assert _place_type_from_tags({"tourism": "alpine_hut"}) == "trail"
    assert _place_type_from_tags({"tourism": "wilderness_hut"}) == "trail"
    assert _place_type_from_tags({"route": "hiking"}) == "trail"
    assert _place_type_from_tags({"route": "mtb"}) == "trail"
    assert _place_type_from_tags({"leisure": "nature_reserve"}) == "park"
    assert _place_type_from_tags({"leisure": "garden"}) == "park"
    assert _place_type_from_tags({"leisure": "playground"}) == "park"
    assert _place_type_from_tags({"leisure": "dog_park"}) == "park"
    assert _place_type_from_tags({"leisure": "bird_hide"}) == "park"
    assert _place_type_from_tags({"leisure": "swimming_area"}) == "water"
    assert _place_type_from_tags({"leisure": "bathing_place"}) == "water"


def test_live_place_search_does_not_add_fallback_when_results_are_limited(monkeypatch):
    live_place = PlaceCandidate(
        source="openstreetmap",
        source_id="osm:node:1",
        name="Live Cafe",
        type="food",
        lat=42.43,
        lon=18.69,
    )

    async def fake_fetch(*args, **kwargs):
        return [live_place]

    monkeypatch.setattr(places, "fetch_osm_places", fake_fetch)
    candidates, warnings = asyncio.run(
        places.get_candidate_places(42.43, 18.69, 240, "car", ["food"], use_live_data=True)
    )

    assert candidates == [live_place]
    assert all(candidate.source != "fallback" for candidate in candidates)
    assert any("live results only" in warning for warning in warnings)


def test_live_place_search_uses_google_candidates_when_osm_is_empty(monkeypatch):
    google_place = PlaceCandidate(
        source="google_places",
        source_id="google:g1",
        name="Live Restaurant",
        type="food",
        lat=42.43,
        lon=18.69,
    )
    seen = {}

    async def fake_fetch(*args, **kwargs):
        return []

    async def fake_google(lat, lon, radius_km, interests, anonymous_id, lang):
        seen["anonymous_id"] = anonymous_id
        seen["interests"] = interests
        return [google_place], []

    monkeypatch.setattr(places, "fetch_osm_places", fake_fetch)
    monkeypatch.setattr(places.google_places, "search_candidate_places", fake_google)
    candidates, warnings = asyncio.run(
        places.get_candidate_places(
            42.43, 18.69, 240, "car", ["food"], use_live_data=True, anonymous_id="u"
        )
    )

    assert candidates == [google_place]
    assert all(candidate.source != "fallback" for candidate in candidates)
    assert seen == {"anonymous_id": "u", "interests": ["food"]}
    assert any("live results only" in warning for warning in warnings)


def test_live_place_search_returns_empty_when_overpass_fails(monkeypatch):
    async def fail_fetch(*args, **kwargs):
        raise TimeoutError("timeout")

    monkeypatch.setattr(places, "fetch_osm_places", fail_fetch)
    candidates, warnings = asyncio.run(
        places.get_candidate_places(42.43, 18.69, 240, "car", ["food"], use_live_data=True)
    )

    assert candidates == []
    assert any("OpenStreetMap/Overpass unavailable" in warning for warning in warnings)
    assert any("live results only" in warning for warning in warnings)
