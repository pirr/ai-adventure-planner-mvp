from __future__ import annotations

from datetime import datetime
from statistics import mean
from typing import Any

import httpx

from app.config import settings
from app.schemas import WeatherSummary
from app.services.i18n import t, weather_label


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
    async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
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
    async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
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


async def get_weather(lat: float, lon: float, use_live_data: bool, lang: str = "en") -> tuple[WeatherSummary, list[str]]:
    warnings: list[str] = []
    if not use_live_data:
        warnings.append(t(lang, "warn_live_disabled"))
        return fallback_weather(lang), warnings

    if settings.openweather_api_key:
        try:
            return await _openweather(lat, lon, settings.openweather_api_key, lang), warnings
        except Exception as exc:  # noqa: BLE001
            warnings.append(t(lang, "warn_openweather_unavailable", exc=exc.__class__.__name__))

    if settings.use_open_meteo_fallback:
        try:
            return await _open_meteo(lat, lon, lang), warnings
        except Exception as exc:  # noqa: BLE001
            warnings.append(t(lang, "warn_openmeteo_unavailable", exc=exc.__class__.__name__))

    return fallback_weather(lang), warnings
