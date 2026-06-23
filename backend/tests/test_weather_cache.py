import asyncio
from types import SimpleNamespace

import app.services.weather as weather_module
from app.schemas import WeatherSummary
from app.services.weather import get_weather

# Drive get_weather and the cache helpers off a fixed settings object so the
# test does not depend on the ambient env (e.g. an OPENWEATHER_API_KEY).
_SETTINGS = SimpleNamespace(
    openweather_api_key=None,
    use_open_meteo_fallback=True,
    anon_weather_cache_ttl_seconds=1800,
    anon_weather_cache_precision=1,  # ~11km buckets
    anon_weather_cache_max_entries=512,
)


def _live() -> WeatherSummary:
    return WeatherSummary(source="open-meteo", summary="Clear", score=85, confidence="live")


def _counting_open_meteo(calls):
    async def _fake(lat, lon, lang):
        calls.append((lat, lon))
        return _live()
    return _fake


def test_cache_ok_serves_nearby_requests_from_one_fetch(monkeypatch):
    weather_module._origin_cache.clear()
    monkeypatch.setattr(weather_module, "settings", _SETTINGS)
    calls = []
    monkeypatch.setattr(weather_module, "_open_meteo", _counting_open_meteo(calls))

    first, _ = asyncio.run(get_weather(42.43, 18.69, use_live_data=True, cache_ok=True))
    second, _ = asyncio.run(get_weather(42.44, 18.71, use_live_data=True, cache_ok=True))

    assert len(calls) == 1  # both rounded to the same (42.4, 18.7) bucket
    assert second is first


def test_cache_off_always_fetches(monkeypatch):
    weather_module._origin_cache.clear()
    monkeypatch.setattr(weather_module, "settings", _SETTINGS)
    calls = []
    monkeypatch.setattr(weather_module, "_open_meteo", _counting_open_meteo(calls))

    asyncio.run(get_weather(42.43, 18.69, use_live_data=True, cache_ok=False))
    asyncio.run(get_weather(42.43, 18.69, use_live_data=True, cache_ok=False))

    assert len(calls) == 2


def test_fallback_weather_is_not_cached(monkeypatch):
    weather_module._origin_cache.clear()
    monkeypatch.setattr(weather_module, "settings", _SETTINGS)

    async def _boom(lat, lon, lang):
        raise RuntimeError("provider down")

    monkeypatch.setattr(weather_module, "_open_meteo", _boom)

    weather, _ = asyncio.run(get_weather(42.43, 18.69, use_live_data=True, cache_ok=True))

    assert weather.confidence == "fallback"
    assert weather_module._origin_cache == {}
