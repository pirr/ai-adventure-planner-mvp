import asyncio
import dataclasses

import pytest

from app.config import settings as real_settings
from app.schemas import AdventureRequest, PlaceCandidate, PlacePhoto, RouteInfo, WeatherSummary
from app.services import google_places
from app.services.google_places import blended_quality, enrich_places
from app.services.llm import TemplateProvider
from app.services.place_photos import get_place_photo
from app.services.recommendations import build_recommendations
from app.services.scoring import score_candidate, to_recommendation
from app.services.storage import Storage


def _settings(**overrides):
    return dataclasses.replace(real_settings, **overrides)


def _place(source_id="osm:node:1", lat=42.0, lon=18.0, **kwargs):
    return PlaceCandidate(
        source="openstreetmap", source_id=source_id, name="Old Fort",
        type="fortress", lat=lat, lon=lon, **kwargs,
    )


def _payload(lat=42.0, lon=18.0, rating=4.6, count=1200, photo=True):
    place = {"location": {"latitude": lat, "longitude": lon}, "rating": rating, "userRatingCount": count}
    if photo:
        place["photos"] = [{"name": "places/abc/photos/xyz", "authorAttributions": [{"displayName": "A. Author"}]}]
    return {"places": [place]}


def _patch_search(monkeypatch, payload_by_id):
    """Replace the HTTP call; returns the list of source_ids actually fetched."""
    calls = []

    async def fake(client, place):
        calls.append(place.source_id)
        result = payload_by_id[place.source_id]
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(google_places, "_search_text", fake)
    return calls


@pytest.fixture
def google_env(tmp_path, monkeypatch):
    """Key set, fresh budgets/SQLite, empty cache. Returns the Storage."""
    store = Storage(tmp_path / "google.db")
    cfg = _settings(google_places_api_key="test-key")
    monkeypatch.setattr("app.services.google_places.settings", cfg)
    monkeypatch.setattr("app.services.storage.settings", cfg)
    monkeypatch.setattr("app.services.google_places.storage", store)
    google_places._cache.clear()
    return store


# --- blended_quality -------------------------------------------------------

def test_blended_quality_trusts_high_review_counts():
    # ~1k+ reviews -> Google rating dominates regardless of the OSM heuristic.
    assert blended_quality(50, 4.8, 2000) == 96


def test_blended_quality_barely_moves_on_tiny_review_counts():
    assert blended_quality(50, 5.0, 3) == 60


def test_blended_quality_stays_in_bounds():
    assert blended_quality(100, 5.0, 100000) == 100
    assert blended_quality(0, 0.0, 100000) == 0


# --- enrich_places ---------------------------------------------------------

def test_enrich_builds_info_by_source_id(google_env, monkeypatch):
    _patch_search(monkeypatch, {"osm:node:1": _payload()})
    results, warnings = asyncio.run(enrich_places([_place()], "u"))

    info = results["osm:node:1"]
    assert info.rating == 4.6
    assert info.rating_count == 1200
    assert info.photo_name == "places/abc/photos/xyz"
    assert info.photo_attribution == "A. Author"
    assert warnings == []


def test_enrich_rejects_matches_further_than_1km(google_env, monkeypatch):
    # locationBias only biases; a hit ~1.6 km away is the wrong place.
    _patch_search(monkeypatch, {"osm:node:1": _payload(lon=18.02)})
    results, warnings = asyncio.run(enrich_places([_place()], "u"))
    assert results == {} and warnings == []


def test_enrich_swallows_per_place_errors(google_env, monkeypatch):
    _patch_search(monkeypatch, {"osm:node:1": RuntimeError("boom"), "osm:node:2": _payload()})
    results, warnings = asyncio.run(enrich_places([_place(), _place(source_id="osm:node:2")], "u"))
    assert set(results) == {"osm:node:2"}
    assert warnings == []


def test_enrich_warns_only_when_every_call_fails(google_env, monkeypatch):
    _patch_search(monkeypatch, {"osm:node:1": RuntimeError("boom")})
    results, warnings = asyncio.run(enrich_places([_place()], "u"))
    assert results == {}
    assert len(warnings) == 1 and "Google Places" in warnings[0]


def test_enrich_serves_repeat_searches_from_cache(google_env, monkeypatch):
    calls = _patch_search(monkeypatch, {"osm:node:1": _payload()})
    first, _ = asyncio.run(enrich_places([_place()], "u"))
    second, _ = asyncio.run(enrich_places([_place()], "u"))
    assert calls == ["osm:node:1"]  # one HTTP call total
    assert first == second


def test_enrich_caches_negative_results_too(google_env, monkeypatch):
    calls = _patch_search(monkeypatch, {"osm:node:1": {"places": []}})
    asyncio.run(enrich_places([_place()], "u"))
    results, _ = asyncio.run(enrich_places([_place()], "u"))
    assert calls == ["osm:node:1"]  # the unmatched place isn't re-billed
    assert results == {}


def test_enrich_cache_hits_consume_no_budget(google_env, monkeypatch):
    store = google_env
    _patch_search(monkeypatch, {"osm:node:1": _payload()})
    asyncio.run(enrich_places([_place()], "u"))
    asyncio.run(enrich_places([_place()], "u"))
    with store._connect() as conn:
        row = conn.execute("SELECT count FROM api_usage WHERE scope='global'").fetchone()
    assert row["count"] == 1


def test_enrich_makes_no_calls_when_budget_exhausted(google_env, monkeypatch):
    cfg = _settings(google_places_api_key="test-key", google_places_daily_limit=0)
    monkeypatch.setattr("app.services.google_places.settings", cfg)
    monkeypatch.setattr("app.services.storage.settings", cfg)
    calls = _patch_search(monkeypatch, {"osm:node:1": _payload()})

    results, warnings = asyncio.run(enrich_places([_place()], "u"))
    assert calls == [] and results == {} and warnings == []


def test_enrich_disabled_without_key(monkeypatch):
    monkeypatch.setattr("app.services.google_places.settings", _settings(google_places_api_key=None))
    results, warnings = asyncio.run(enrich_places([_place()], "u"))
    assert results == {} and warnings == []


# --- storage.reserve_google_calls ------------------------------------------

def _budget_store(tmp_path, monkeypatch, daily=100, per_user=50):
    cfg = _settings(google_places_daily_limit=daily, google_places_user_daily_limit=per_user)
    monkeypatch.setattr("app.services.storage.settings", cfg)
    return Storage(tmp_path / "budget.db")


def test_reserve_clamps_to_the_tighter_limit(tmp_path, monkeypatch):
    store = _budget_store(tmp_path, monkeypatch, daily=10, per_user=3)
    assert store.reserve_google_calls("u", 5) == 3


def test_users_share_the_global_budget(tmp_path, monkeypatch):
    store = _budget_store(tmp_path, monkeypatch, daily=5, per_user=5)
    assert store.reserve_google_calls("a", 3) == 3
    assert store.reserve_google_calls("b", 3) == 2  # only 2 left globally


def test_reserve_requires_an_anonymous_id(tmp_path, monkeypatch):
    store = _budget_store(tmp_path, monkeypatch)
    assert store.reserve_google_calls(None, 5) == 0


def test_budget_resets_on_a_new_day(tmp_path, monkeypatch):
    store = _budget_store(tmp_path, monkeypatch, daily=2, per_user=2)
    assert store.reserve_google_calls("u", 2) == 2
    assert store.reserve_google_calls("u", 1) == 0
    monkeypatch.setattr(Storage, "_usage_day", staticmethod(lambda: "2099-01-01"))
    assert store.reserve_google_calls("u", 1) == 1


def test_old_usage_rows_are_pruned(tmp_path, monkeypatch):
    store = _budget_store(tmp_path, monkeypatch)
    with store._connect() as conn:
        conn.execute("INSERT INTO api_usage (day, scope, key, count) VALUES ('2020-01-01', 'global', '', 9)")
    store.reserve_google_calls("u", 1)
    with store._connect() as conn:
        days = [row["day"] for row in conn.execute("SELECT day FROM api_usage")]
    assert "2020-01-01" not in days


# --- photo fallback order ---------------------------------------------------

def test_commons_photo_wins_over_google(monkeypatch):
    async def fail(*args):
        raise AssertionError("Google photo must not be resolved when Commons has one")

    monkeypatch.setattr(google_places, "resolve_photo", fail)
    place = _place(tags={"wikimedia_commons": "File:Fort.jpg"}, google_photo_name="places/abc/photos/xyz")
    photo = asyncio.run(get_place_photo(place, use_live_data=True))
    assert photo is not None and "commons.wikimedia.org" in photo.url


def test_google_photo_backfills_when_free_sources_are_empty(monkeypatch):
    async def fake(photo_name, attribution):
        return PlacePhoto(url="https://lh3.googleusercontent.com/abc", source="Google Maps", attribution=attribution)

    monkeypatch.setattr(google_places, "resolve_photo", fake)
    place = _place(google_photo_name="places/abc/photos/xyz", google_photo_attribution="A. Author")
    photo = asyncio.run(get_place_photo(place, use_live_data=True))
    assert photo is not None
    assert photo.source == "Google Maps"
    assert photo.attribution == "A. Author"


# --- pipeline ----------------------------------------------------------------

def test_to_recommendation_copies_rating():
    place = _place(rating=4.6, rating_count=1200)
    route = RouteInfo(source="estimate", one_way_minutes=20, round_trip_minutes=40, distance_km=10, map_url="https://maps.example")
    weather = WeatherSummary(source="test", summary="clear", score=80)
    scored = score_candidate(place, route, weather, AdventureRequest(lat=42.0, lon=18.0))
    rec = to_recommendation(scored)
    assert rec.rating == 4.6 and rec.rating_count == 1200


def test_pipeline_without_key_has_no_google_traces(tmp_path, monkeypatch):
    # The offline path (also what eval runs) must be untouched by this feature.
    monkeypatch.setattr("app.services.recommendations.storage", Storage(tmp_path / "p.db"))
    request = AdventureRequest(
        lat=42.4304, lon=18.6964, available_minutes=300, transport_mode="car",
        use_live_data=False, interests=["history", "fortresses"], limit=5,
    )
    response = asyncio.run(build_recommendations(request, provider=TemplateProvider()))
    assert response.recommendations
    assert all(r.rating is None and r.rating_count is None for r in response.recommendations)
    assert not any("Google" in w for w in response.data_warnings)
