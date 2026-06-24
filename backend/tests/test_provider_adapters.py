"""Adapter parsing + taxonomy mapping, with HTTP mocked. Pins the assumed
response shapes and the category/tag maps so a provider change is caught here,
not in a live sweep."""
from __future__ import annotations

import asyncio
import dataclasses

import pytest

from app.config import settings as real_settings
from app.schemas import INTEREST_IDS, PlaceCandidate
from eval.providers import baseline as baseline_mod
from eval.providers import geoapify as geoapify_mod
from eval.providers import locationiq as locationiq_mod
from eval.providers.base import as_candidate_source
from eval.providers.geoapify import GeoapifyProvider, _place_type_from_categories
from eval.providers.locationiq import LocationIQProvider


def _settings(**overrides):
    return dataclasses.replace(real_settings, **overrides)


class _FakeResponse:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        return None

    def json(self):
        return self._data


class _FakeClient:
    """Stands in for the http_client() async context manager; routes .get() to a
    handler(url, params) -> json."""

    def __init__(self, handler):
        self._handler = handler

    async def get(self, url, params=None):
        return _FakeResponse(self._handler(url, params))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _patch_http(monkeypatch, module, handler):
    monkeypatch.setattr(module, "http_client", lambda timeout: _FakeClient(handler))


# --- Geoapify ----------------------------------------------------------------

def test_place_type_from_categories_maps_each_bucket():
    assert _place_type_from_categories(["catering", "catering.pub"]) == "drinks"
    assert _place_type_from_categories(["catering.restaurant"]) == "food"
    assert _place_type_from_categories(["entertainment.museum"]) == "museum"
    assert _place_type_from_categories(["tourism.sights.castle"]) == "fortress"
    assert _place_type_from_categories(["tourism.attraction.viewpoint"]) == "viewpoint"
    assert _place_type_from_categories(["natural.water"]) == "water"
    assert _place_type_from_categories(["leisure.park"]) == "park"
    assert _place_type_from_categories(["tourism.sights"]) == "historic_site"
    assert _place_type_from_categories(["tourism.attraction"]) == "attraction"
    assert _place_type_from_categories(["commercial.supermarket"]) == "place"


def test_geoapify_fetch_parses_features(monkeypatch):
    monkeypatch.setattr(geoapify_mod, "settings", _settings(geoapify_api_key="k"))
    payload = {
        "features": [
            {"properties": {
                "name": "Old Castle", "place_id": "geo1", "lat": 42.0, "lon": 18.0,
                "categories": ["tourism", "tourism.sights", "tourism.sights.castle"],
                "datasource": {"raw": {"wikidata": "Q1", "opening_hours": "Mo-Su 09:00-18:00"}},
            }},
            {"properties": {
                "name": "The Pub", "place_id": "geo2", "lat": 42.01, "lon": 18.01,
                "categories": ["catering", "catering.pub"],
            }},
            {  # lat/lon only in geometry
                "properties": {"name": "Museum X", "place_id": "geo3", "categories": ["entertainment.museum"]},
                "geometry": {"coordinates": [18.02, 42.02]},
            },
            {"properties": {"place_id": "geo4", "lat": 42.0, "lon": 18.0, "categories": ["tourism.sights"]}},  # no name
        ]
    }
    _patch_http(monkeypatch, geoapify_mod, lambda url, params: payload)

    out = asyncio.run(GeoapifyProvider().fetch(42.0, 18.0, 25.0, ["history", "drinks"]))
    by_id = {c.source_id: c for c in out}
    assert set(by_id) == {"geoapify:geo1", "geoapify:geo2", "geoapify:geo3"}  # geo4 (no name) dropped
    castle = by_id["geoapify:geo1"]
    assert castle.type == "fortress"
    assert castle.tags["opening_hours"] == "Mo-Su 09:00-18:00"
    assert castle.quality_score == 50 + 15 + 15 + 10 + 4  # name + wikidata + fortress + hours
    assert by_id["geoapify:geo2"].type == "drinks"
    museum = by_id["geoapify:geo3"]
    assert museum.type == "museum" and museum.lat == 42.02 and museum.lon == 18.02


def test_geoapify_without_key_returns_empty(monkeypatch):
    monkeypatch.setattr(geoapify_mod, "settings", _settings(geoapify_api_key=None))
    assert asyncio.run(GeoapifyProvider().fetch(42.0, 18.0, 25.0, ["history"])) == []


# --- LocationIQ --------------------------------------------------------------

def test_locationiq_fetch_uses_osm_tags_and_dedupes(monkeypatch):
    monkeypatch.setattr(locationiq_mod, "settings", _settings(locationiq_api_key="k"))
    by_tag = {
        "museum": [{"place_id": "l1", "osm_type": "way", "osm_id": "100", "lat": "42.0", "lon": "18.0",
                    "class": "tourism", "type": "museum", "name": "City Museum",
                    "extratags": {"wikidata": "Q9", "opening_hours": "24/7"}}],
        "attraction": [{"place_id": "l2", "osm_type": "node", "osm_id": "200", "lat": "42.1", "lon": "18.1",
                        "class": "tourism", "type": "attraction", "display_name": "Big Arch, Town"}],
        "castle": [
            {"place_id": "l3", "osm_type": "way", "osm_id": "300", "lat": "42.2", "lon": "18.2",
             "class": "historic", "type": "castle", "name": "Stone Fort"},
            {"place_id": "ldup", "osm_type": "way", "osm_id": "100", "lat": "42.0", "lon": "18.0",
             "class": "tourism", "type": "museum", "name": "City Museum dup"},  # same osm way 100 -> deduped
        ],
    }
    _patch_http(monkeypatch, locationiq_mod, lambda url, params: by_tag.get(params["tag"], []))

    out = asyncio.run(LocationIQProvider().fetch(42.0, 18.0, 25.0, ["history"]))
    by_id = {c.source_id: c for c in out}
    assert set(by_id) == {"locationiq:way:100", "locationiq:node:200", "locationiq:way:300"}
    assert by_id["locationiq:way:100"].type == "museum"
    assert by_id["locationiq:way:100"].quality_score == 50 + 15 + 15 + 8 + 4  # name+wikidata+tourism+hours
    assert by_id["locationiq:node:200"].name == "Big Arch"  # from display_name
    assert by_id["locationiq:way:300"].type == "fortress"  # historic=castle


def test_locationiq_without_key_returns_empty(monkeypatch):
    monkeypatch.setattr(locationiq_mod, "settings", _settings(locationiq_api_key=None))
    assert asyncio.run(LocationIQProvider().fetch(42.0, 18.0, 25.0, ["food"])) == []


# --- category/tag maps cover every interest ----------------------------------

def test_interest_maps_cover_all_interest_ids():
    for interest in INTEREST_IDS:
        assert geoapify_mod._categories_for_interests([interest]), interest
        assert locationiq_mod._tags_for_interests([interest]), interest


# --- baseline wiring + seam adapter ------------------------------------------

def test_baseline_returns_osm_when_dense(monkeypatch):
    osm = [PlaceCandidate(source="openstreetmap", source_id="osm:node:1", name="A", type="park", lat=42.0, lon=18.0)]

    async def fake_progressive(lat, lon, radius_km, interests):
        return osm

    monkeypatch.setattr(baseline_mod.places, "_fetch_osm_places_progressive", fake_progressive)
    monkeypatch.setattr(baseline_mod.places, "_needs_google_candidates", lambda c, i: False)
    out = asyncio.run(baseline_mod.OsmGoogleBaselineProvider().fetch(42.0, 18.0, 8.0, ["nature"]))
    assert [c.source_id for c in out] == ["osm:node:1"]


def test_baseline_backfills_google_when_sparse(monkeypatch):
    g = [PlaceCandidate(source="google_places", source_id="google:g1", name="G", type="food", lat=42.0, lon=18.0)]

    async def fake_progressive(lat, lon, radius_km, interests):
        return []

    async def fake_search(lat, lon, radius_km, interests, anon, lang):
        return g, []

    monkeypatch.setattr(baseline_mod.places, "_fetch_osm_places_progressive", fake_progressive)
    monkeypatch.setattr(baseline_mod.places, "_needs_google_candidates", lambda c, i: True)
    monkeypatch.setattr(baseline_mod.google_places, "search_candidate_places", fake_search)
    out = asyncio.run(baseline_mod.OsmGoogleBaselineProvider().fetch(42.0, 18.0, 8.0, ["food"], anonymous_id="u"))
    assert [c.source_id for c in out] == ["google:g1"]


def test_as_candidate_source_matches_production_signature():
    place = PlaceCandidate(source="x", source_id="x:1", name="N", type="park", lat=1.0, lon=2.0)

    class P:
        name = "p"

        async def fetch(self, lat, lon, radius_km, interests, lang="en", anonymous_id=None):
            return [place]

    source = as_candidate_source(P())
    candidates, warnings = asyncio.run(source(1.0, 2.0, 120, "car", ["nature"], True, "en", None))
    assert candidates == [place] and warnings == []
