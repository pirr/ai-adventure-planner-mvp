from __future__ import annotations

import math
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.config import settings
from app.schemas import HourlyForecast, WeatherSummary
from app.services.i18n import t, weather_label
from app.services.net import http_client


def _weather_score(temp: float | None, rain_now: float | None, rain_24h: float | None, wind_kmh: float | None, uv: float | None, code: int | None = None) -> int:
    score = 85
    if temp is not None:
        if temp < 0:
            score -= 35
        elif temp < 7:
            score -= 15
        elif 12 <= temp <= 26:
            score += 8
        elif 27 <= temp <= 32:
            score -= 8
        elif temp > 32:
            score -= 25
    if rain_now is not None:
        if rain_now > 3:
            score -= 35
        elif rain_now > 0.5:
            score -= 18
        elif rain_now > 0:
            score -= 8
    if rain_24h is not None:
        if rain_24h > 20:
            score -= 25
        elif rain_24h > 8:
            score -= 12
    if wind_kmh is not None:
        if wind_kmh > 50:
            score -= 30
        elif wind_kmh > 30:
            score -= 15
        elif wind_kmh > 20:
            score -= 6
    if uv is not None:
        if uv >= 9:
            score -= 18
        elif uv >= 7:
            score -= 8
    if code in {95, 96, 99}:
        score -= 45
    return max(0, min(100, int(round(score))))


async def _open_meteo(lat: float, lon: float, lang: str) -> WeatherSummary:
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m,relative_humidity_2m,precipitation,rain,weather_code,wind_speed_10m",
        "hourly": "temperature_2m,precipitation,uv_index,wind_speed_10m",
        "daily": "sunrise,sunset,uv_index_max,precipitation_sum",
        "timezone": "auto",
        "forecast_days": 2,
        "past_days": 1,
    }
    async with http_client(settings.http_timeout_seconds) as client:
        response = await client.get(url, params=params)
        response.raise_for_status()
        data = response.json()

    current: dict[str, Any] = data.get("current") or {}
    hourly: dict[str, Any] = data.get("hourly") or {}
    daily: dict[str, Any] = data.get("daily") or {}
    rain_values = hourly.get("precipitation") or []
    recent_rain_values = rain_values[:24] if len(rain_values) >= 24 else rain_values
    rain_24h = float(sum(value or 0 for value in recent_rain_values)) if recent_rain_values else None
    uv_values = hourly.get("uv_index") or []
    uv = float(max([value or 0 for value in uv_values[:12]])) if uv_values else None
    temp = current.get("temperature_2m")
    rain_now = current.get("rain") or current.get("precipitation")
    wind = current.get("wind_speed_10m")
    humidity = current.get("relative_humidity_2m")
    code = current.get("weather_code")
    summary = weather_label(lang, code)
    score = _weather_score(temp, rain_now, rain_24h, wind, uv, code)
    sunrise = (daily.get("sunrise") or [None])[0]
    sunset = (daily.get("sunset") or [None])[0]
    return WeatherSummary(
        source="open-meteo",
        temperature_c=float(temp) if temp is not None else None,
        rain_mm_now=float(rain_now) if rain_now is not None else None,
        rain_mm_last_24h=rain_24h,
        wind_kmh=float(wind) if wind is not None else None,
        humidity_percent=float(humidity) if humidity is not None else None,
        uv_index=uv,
        sunrise=sunrise,
        sunset=sunset,
        summary=summary,
        score=score,
        confidence="live",
    )


async def _openweather(lat: float, lon: float, api_key: str, lang: str) -> WeatherSummary:
    # Optional adapter if the user has an OpenWeather key. Open-Meteo fallback is used otherwise.
    url = "https://api.openweathermap.org/data/3.0/onecall"
    params = {
        "lat": lat,
        "lon": lon,
        "appid": api_key,
        "units": "metric",
        "lang": lang,
        "exclude": "minutely,alerts",
    }
    async with http_client(settings.http_timeout_seconds) as client:
        response = await client.get(url, params=params)
        response.raise_for_status()
        data = response.json()

    current = data.get("current") or {}
    daily = (data.get("daily") or [{}])[0]
    hourly = data.get("hourly") or []
    rain_24h = sum((item.get("rain", {}).get("1h", 0) or 0) for item in hourly[:24]) if hourly else None
    temp = current.get("temp")
    rain_now = (current.get("rain") or {}).get("1h", 0) or 0
    wind = current.get("wind_speed")
    wind_kmh = float(wind) * 3.6 if wind is not None else None
    uv = current.get("uvi")
    weather = (current.get("weather") or [{}])[0]
    summary = weather.get("description") or t(lang, "weather_data_available")
    score = _weather_score(temp, rain_now, rain_24h, wind_kmh, uv)
    sunrise = datetime.fromtimestamp(daily.get("sunrise")).isoformat() if daily.get("sunrise") else None
    sunset = datetime.fromtimestamp(daily.get("sunset")).isoformat() if daily.get("sunset") else None
    return WeatherSummary(
        source="openweather",
        temperature_c=float(temp) if temp is not None else None,
        rain_mm_now=float(rain_now) if rain_now is not None else None,
        rain_mm_last_24h=float(rain_24h) if rain_24h is not None else None,
        wind_kmh=wind_kmh,
        humidity_percent=float(current.get("humidity")) if current.get("humidity") is not None else None,
        uv_index=float(uv) if uv is not None else None,
        sunrise=sunrise,
        sunset=sunset,
        summary=summary,
        score=score,
        confidence="live",
    )


def fallback_weather(lang: str = "en") -> WeatherSummary:
    return WeatherSummary(
        source="fallback",
        temperature_c=22,
        rain_mm_now=0,
        rain_mm_last_24h=0,
        wind_kmh=10,
        humidity_percent=None,
        uv_index=5,
        sunrise=None,
        sunset=None,
        summary=t(lang, "weather_fallback_summary"),
        score=78,
        confidence="fallback",
    )


# Coarse-grid cache of live origin weather, used only on the anonymous fast
# path (cache_ok=True). Keyed by rounded coordinates + language so nearby
# anonymous requests in a region reuse one forecast for a short TTL. In-process
# and lossy on restart by design. {key: (expires_at, weather)}.
_origin_cache: dict[tuple, tuple[float, WeatherSummary]] = {}


def _origin_cache_key(lat: float, lon: float, lang: str) -> tuple:
    precision = settings.anon_weather_cache_precision
    return (round(lat, precision), round(lon, precision), lang)


def _origin_cache_get(key: tuple) -> WeatherSummary | None:
    entry = _origin_cache.get(key)
    if entry is None:
        return None
    expires_at, weather = entry
    if expires_at <= time.time():
        _origin_cache.pop(key, None)
        return None
    return weather


def _origin_cache_put(key: tuple, weather: WeatherSummary) -> None:
    ttl = settings.anon_weather_cache_ttl_seconds
    max_entries = settings.anon_weather_cache_max_entries
    if ttl <= 0 or max_entries <= 0:
        return
    now = time.time()
    for cached_key, (expires_at, _) in list(_origin_cache.items()):
        if expires_at <= now:
            _origin_cache.pop(cached_key, None)
    while len(_origin_cache) >= max_entries:
        _origin_cache.pop(next(iter(_origin_cache)))
    _origin_cache[key] = (now + ttl, weather)


async def get_weather(
    lat: float, lon: float, use_live_data: bool, lang: str = "en", cache_ok: bool = False
) -> tuple[WeatherSummary, list[str]]:
    warnings: list[str] = []
    if not use_live_data:
        warnings.append(t(lang, "warn_live_disabled"))
        return fallback_weather(lang), warnings

    cache_key = _origin_cache_key(lat, lon, lang) if cache_ok else None
    if cache_key is not None:
        cached = _origin_cache_get(cache_key)
        if cached is not None:
            return cached, warnings

    # Resolve live weather: OpenWeather (if keyed) then Open-Meteo. Only a live
    # result is cached; the hardcoded fallback below is never stored.
    weather: WeatherSummary | None = None
    if settings.openweather_api_key:
        try:
            weather = await _openweather(lat, lon, settings.openweather_api_key, lang)
        except Exception as exc:  # noqa: BLE001
            warnings.append(t(lang, "warn_openweather_unavailable", exc=exc.__class__.__name__))

    if weather is None and settings.use_open_meteo_fallback:
        try:
            weather = await _open_meteo(lat, lon, lang)
        except Exception as exc:  # noqa: BLE001
            warnings.append(t(lang, "warn_openmeteo_unavailable", exc=exc.__class__.__name__))

    if weather is None:
        return fallback_weather(lang), warnings

    if cache_key is not None:
        _origin_cache_put(cache_key, weather)
    return weather, warnings


# ---------------------------------------------------------------------------
# Destination / arrival forecast
#
# The summary above describes the weather at the user's current location, now.
# For a place that is an hour's drive away the weather *on arrival* is what
# matters, so we fetch an hourly forecast at each destination and slice out the
# hours the user will actually experience (travel time -> end of the visit).
# ---------------------------------------------------------------------------

# Cap the per-place timeline so the UI strip stays readable even for all-day trips.
_MAX_FORECAST_HOURS = 6


def _hhmm(value: str) -> str:
    # "2026-06-06T14:00" -> "14:00"
    return value[11:16] if len(value) >= 16 else value


def _as_float(value: Any) -> float | None:
    return float(value) if value is not None else None


def _now_index(times: list[str], current_time: str | None) -> int:
    """Index of the hourly slot covering "now".

    Open-Meteo hourly times are zero-padded local ISO strings, so a lexical
    compare on the "YYYY-MM-DDTHH" prefix is also a chronological one.
    """
    if not current_time:
        return 0
    target = current_time[:13]
    index = 0
    for i, value in enumerate(times):
        if value[:13] <= target:
            index = i
        else:
            break
    return index


@dataclass
class DestinationForecast:
    times: list[str]
    temps: list[float | None]
    precip: list[float | None]
    winds: list[float | None]
    uvs: list[float | None]
    codes: list[int | None]
    sunsets: list[str]
    now_index: int

    def _at(self, seq: list, index: int):
        if not seq:
            return None
        return seq[max(0, min(index, len(seq) - 1))]

    def at_arrival(self, one_way_minutes: int, activity_minutes: int, lang: str) -> tuple[WeatherSummary, list[HourlyForecast]]:
        arrival_offset = max(1, math.ceil(one_way_minutes / 60))
        end_offset = min(arrival_offset + math.ceil(max(activity_minutes, 0) / 60), _MAX_FORECAST_HOURS)

        timeline: list[HourlyForecast] = []
        for offset in range(1, end_offset + 1):
            index = self.now_index + offset
            if index >= len(self.times):
                break
            timeline.append(
                HourlyForecast(
                    time=_hhmm(self.times[index]),
                    hour_offset=offset,
                    label=weather_label(lang, self._at(self.codes, index)),
                    temperature_c=_as_float(self._at(self.temps, index)),
                    precipitation_mm=_as_float(self._at(self.precip, index)),
                    wind_kmh=_as_float(self._at(self.winds, index)),
                    is_arrival=(offset == arrival_offset),
                )
            )

        arrival_index = max(0, min(self.now_index + arrival_offset, len(self.times) - 1))
        temp = _as_float(self._at(self.temps, arrival_index))
        rain_now = _as_float(self._at(self.precip, arrival_index))
        wind = _as_float(self._at(self.winds, arrival_index))
        uv = _as_float(self._at(self.uvs, arrival_index))
        code = self._at(self.codes, arrival_index)
        window = [value or 0 for value in self.precip[max(0, arrival_index - 23): arrival_index + 1]]
        rain_24h = float(sum(window)) if window else None
        arrival_date = self.times[arrival_index][:10] if self.times else None
        sunset = next((s for s in self.sunsets if s[:10] == arrival_date), self.sunsets[-1] if self.sunsets else None)

        arrival_weather = WeatherSummary(
            source="open-meteo",
            temperature_c=temp,
            rain_mm_now=rain_now,
            rain_mm_last_24h=rain_24h,
            wind_kmh=wind,
            humidity_percent=None,
            uv_index=uv,
            sunrise=None,
            sunset=sunset,
            summary=weather_label(lang, code),
            score=_weather_score(temp, rain_now, rain_24h, wind, uv, code),
            confidence="live",
        )
        return arrival_weather, timeline


def _parse_destination_block(block: dict[str, Any]) -> DestinationForecast | None:
    hourly: dict[str, Any] = block.get("hourly") or {}
    times = hourly.get("time") or []
    if not times:
        return None
    current_time = (block.get("current") or {}).get("time")
    return DestinationForecast(
        times=times,
        temps=hourly.get("temperature_2m") or [],
        precip=hourly.get("precipitation") or [],
        winds=hourly.get("wind_speed_10m") or [],
        uvs=hourly.get("uv_index") or [],
        codes=hourly.get("weather_code") or [],
        sunsets=(block.get("daily") or {}).get("sunset") or [],
        now_index=_now_index(times, current_time),
    )


async def get_destination_forecasts(points: list[tuple[float, float]], use_live_data: bool, lang: str = "en") -> list[DestinationForecast | None]:
    """Hourly forecast for each destination, aligned 1:1 with ``points``.

    Open-Meteo accepts comma-separated coordinates, so every top candidate is
    covered by a single request. Best-effort: any failure (or live data off)
    yields ``None`` entries, and callers fall back to the origin weather.
    """
    if not points:
        return []
    if not use_live_data or not settings.use_open_meteo_fallback:
        return [None] * len(points)

    params = {
        "latitude": ",".join(f"{lat:.4f}" for lat, _ in points),
        "longitude": ",".join(f"{lon:.4f}" for _, lon in points),
        "current": "temperature_2m",
        "hourly": "temperature_2m,precipitation,weather_code,wind_speed_10m,uv_index",
        "daily": "sunset",
        "timezone": "auto",
        "forecast_days": 2,
        "past_days": 1,
    }
    try:
        async with http_client(settings.http_timeout_seconds) as client:
            response = await client.get("https://api.open-meteo.com/v1/forecast", params=params)
            response.raise_for_status()
            data = response.json()
    except Exception:  # noqa: BLE001 - destination forecast is best-effort
        return [None] * len(points)

    blocks = data if isinstance(data, list) else [data]
    results: list[DestinationForecast | None] = [_parse_destination_block(block) for block in blocks]
    if len(results) < len(points):
        results.extend([None] * (len(points) - len(results)))
    return results[: len(points)]
