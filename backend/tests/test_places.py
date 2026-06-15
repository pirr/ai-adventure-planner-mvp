import asyncio

from app.schemas import PlaceCandidate
from app.services import places
from app.services.places import _build_overpass_query, _place_type_from_tags


_AMENITY_REGEX = '"amenity"~"restaurant|cafe|bar|pub|fast_food|ice_cream|biergarten"'


def test_food_interest_adds_amenities_to_overpass_query():
    query = _build_overpass_query(42.43, 18.69, 25000, ["food", "history"])

    assert _AMENITY_REGEX in query


def test_drinks_interest_adds_amenities_to_overpass_query():
    query = _build_overpass_query(42.43, 18.69, 25000, ["drinks"])

    assert _AMENITY_REGEX in query


def test_non_food_interest_keeps_amenities_out_of_overpass_query():
    query = _build_overpass_query(42.43, 18.69, 25000, ["history", "fortresses"])

    assert _AMENITY_REGEX not in query


def test_amenity_block_radius_is_capped_for_large_radius():
    # The dense amenity scan blows past Overpass's per-query timeout at city
    # scale, so it is capped to a local radius while base blocks keep the full one.
    from app.services.places import AMENITY_MAX_RADIUS_M

    query = _build_overpass_query(42.43, 18.69, 25000, ["drinks"])
    assert 'around:25000,42.43,18.69)["tourism"' in query  # base block, full radius
    assert f'around:{AMENITY_MAX_RADIUS_M},42.43,18.69)["amenity"' in query
    assert 'around:25000,42.43,18.69)["amenity"' not in query


def test_amenity_block_uses_full_radius_when_within_cap():
    query = _build_overpass_query(42.43, 18.69, 5000, ["drinks"])
    assert 'around:5000,42.43,18.69)["amenity"' in query


def test_eat_amenities_become_food_places():
    assert _place_type_from_tags({"amenity": "restaurant"}) == "food"
    assert _place_type_from_tags({"amenity": "cafe"}) == "food"
    assert _place_type_from_tags({"amenity": "fast_food"}) == "food"


def test_drink_amenities_become_drinks_places():
    assert _place_type_from_tags({"amenity": "pub"}) == "drinks"
    assert _place_type_from_tags({"amenity": "bar"}) == "drinks"
    assert _place_type_from_tags({"amenity": "biergarten"}) == "drinks"


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
