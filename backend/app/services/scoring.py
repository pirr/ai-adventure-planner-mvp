from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable

from app.schemas import AdventureRequest, PlaceCandidate, Recommendation, RejectedAlternative, RouteInfo, ScoreBreakdown, WeatherSummary


INTEREST_ALIASES = {
    "views": "viewpoints",
    "view": "viewpoints",
    "history": "history",
    "historic": "history",
    "fortress": "fortresses",
    "fortresses": "fortresses",
    "castle": "fortresses",
    "nature": "nature",
    "water": "water",
    "food": "food",
    "surprise": "surprise me",
    "surprise me": "surprise me",
}

PLACE_INTERESTS = {
    "viewpoint": {"viewpoints", "nature"},
    "park": {"nature", "family"},
    "water": {"water", "nature"},
    "museum": {"history", "family"},
    "historic_site": {"history", "viewpoints"},
    "fortress": {"history", "fortresses", "viewpoints"},
    "attraction": {"history", "viewpoints", "family"},
    "trail": {"nature", "active"},
    "food": {"food"},
}


@dataclass
class ScoredCandidate:
    place: PlaceCandidate
    route: RouteInfo
    total_minutes: int
    score: int
    breakdown: ScoreBreakdown
    why: list[str]
    warnings: list[str]
    description: str
    data_confidence: str


def normalize_interest(value: str) -> str:
    return INTEREST_ALIASES.get(value.strip().lower(), value.strip().lower())


def _clamp(value: int | float) -> int:
    return max(0, min(100, int(round(value))))


def _time_fit(total_minutes: int, available_minutes: int) -> int:
    if total_minutes <= available_minutes:
        unused = available_minutes - total_minutes
        if unused <= max(15, available_minutes * 0.25):
            return 100
        return 92
    ratio = total_minutes / available_minutes
    if ratio <= 1.10:
        return 75
    if ratio <= 1.25:
        return 55
    if ratio <= 1.50:
        return 25
    return 0


def _distance_fit(route: RouteInfo, request: AdventureRequest) -> int:
    one_way_limit = {
        "walk": max(10, request.available_minutes * 0.25),
        "bike": max(15, request.available_minutes * 0.30),
        "car": max(15, request.available_minutes * 0.33),
    }[request.transport_mode]
    if route.one_way_minutes <= one_way_limit:
        return 100
    if route.one_way_minutes <= one_way_limit * 1.3:
        return 75
    if route.one_way_minutes <= one_way_limit * 1.8:
        return 45
    return 15


def _interest_fit(place: PlaceCandidate, interests: Iterable[str]) -> int:
    normalized = {normalize_interest(item) for item in interests}
    if not normalized or "surprise me" in normalized:
        return 78
    tag_interests = set(map(normalize_interest, place.tags.get("interests", [])))
    inferred = PLACE_INTERESTS.get(place.type, set())
    available = tag_interests | inferred | {normalize_interest(place.type)}
    matches = normalized & available
    if matches:
        return min(100, 70 + 15 * len(matches))
    if {"nature", "viewpoints"} & normalized and place.type in {"park", "water", "viewpoint"}:
        return 82
    if {"history", "fortresses"} & normalized and place.type in {"historic_site", "fortress", "museum"}:
        return 88
    return 35


def _group_fit(place: PlaceCandidate, request: AdventureRequest) -> int:
    score = 88
    has_children = request.group_type in {"family", "kids"} or bool(request.children_ages)
    young_child = any(age <= 7 for age in request.children_ages)
    max_walk = request.max_walking_km
    if has_children:
        if place.difficulty == "hard":
            score -= 45
        elif place.difficulty == "medium":
            score -= 12
        if young_child and place.estimated_walking_km > 3:
            score -= 35
        elif place.estimated_walking_km > 4:
            score -= 20
    if request.group_type == "dog" and place.type in {"museum", "historic_site"}:
        score -= 20
    if max_walk is not None and place.estimated_walking_km > max_walk:
        score -= 35
    return _clamp(score)


def _safety_fit(place: PlaceCandidate, request: AdventureRequest, weather: WeatherSummary, total_minutes: int) -> tuple[int, list[str]]:
    score = 88
    warnings: list[str] = []

    rain_24h = weather.rain_mm_last_24h or 0
    temp = weather.temperature_c
    wind = weather.wind_kmh or 0
    uv = weather.uv_index or 0

    if rain_24h > 8 and place.type in {"trail", "park", "viewpoint", "water"}:
        score -= 18
        warnings.append("Rain in the last 24 hours may make natural paths muddy or slippery.")
    if rain_24h > 20:
        score -= 12
        warnings.append("Heavy recent rainfall increases the risk of poor trail conditions.")
    if temp is not None and temp > 32:
        score -= 18
        warnings.append("High temperature may make walking uncomfortable, especially for children.")
    if uv >= 8 and place.type in {"viewpoint", "water", "trail", "park"}:
        score -= 8
        warnings.append("High UV index: bring sun protection and water.")
    if wind > 35 and place.type in {"viewpoint", "water"}:
        score -= 12
        warnings.append("Strong wind may reduce comfort at exposed viewpoints or waterfront areas.")
    if place.difficulty == "hard":
        score -= 22
        warnings.append("This option may involve difficult terrain or climbing.")
    elif place.difficulty == "medium" and request.intensity == "easy":
        score -= 8
        warnings.append("Some parts may be moderately difficult for an easy walk.")

    if request.max_walking_km is not None and place.estimated_walking_km > request.max_walking_km:
        warnings.append(f"Walking distance is about {place.estimated_walking_km:.1f} km, above your limit of {request.max_walking_km:.1f} km.")

    if weather.sunset:
        try:
            sunset = datetime.fromisoformat(weather.sunset.replace("Z", "+00:00"))
            finish = datetime.now(sunset.tzinfo) + timedelta(minutes=total_minutes)
            if finish > sunset:
                score -= 20
                warnings.append("The adventure may finish after sunset.")
        except Exception:
            pass

    return _clamp(score), warnings


def _place_quality(place: PlaceCandidate) -> int:
    return _clamp(place.quality_score)


def _why(place: PlaceCandidate, route: RouteInfo, weather: WeatherSummary, breakdown: ScoreBreakdown, request: AdventureRequest) -> list[str]:
    items: list[str] = []
    if breakdown.time_fit >= 80:
        items.append("Fits your available time window.")
    if breakdown.weather_fit >= 75:
        items.append(f"Weather looks suitable: {weather.summary.lower()}.")
    if breakdown.distance_fit >= 80:
        items.append(f"Travel time is manageable: about {route.one_way_minutes} min one way.")
    if breakdown.group_fit >= 80:
        items.append("Suitable for the selected group and difficulty level.")
    if breakdown.interest_fit >= 80:
        items.append("Matches your selected interests.")
    if place.estimated_walking_km <= 2.5:
        items.append(f"Walking is limited to about {place.estimated_walking_km:.1f} km.")
    if not items:
        items.append("This is the best available option after filtering nearby places.")
    return items[:5]


def _description(place: PlaceCandidate) -> str:
    by_type = {
        "viewpoint": "A scenic stop with a short walk and a strong visual payoff.",
        "fortress": "A history-focused stop with views and exploration potential.",
        "historic_site": "A cultural walk that works well for a short trip.",
        "museum": "A low-risk indoor or semi-indoor option with educational value.",
        "park": "A flexible nature option with easy pacing.",
        "water": "A relaxed water-focused stop suitable for a light outing.",
        "trail": "An active outdoor option that may require more effort.",
    }
    return by_type.get(place.type, "A nearby place that fits part of your request.")


def _confidence(place: PlaceCandidate, route: RouteInfo, weather: WeatherSummary) -> str:
    live_count = sum([place.source == "openstreetmap", route.confidence == "live", weather.confidence == "live"])
    if live_count == 3:
        return "live"
    if live_count == 0:
        return "fallback"
    return "mixed"


def score_candidate(place: PlaceCandidate, route: RouteInfo, weather: WeatherSummary, request: AdventureRequest) -> ScoredCandidate:
    total_minutes = route.round_trip_minutes + place.estimated_activity_minutes
    time_fit = _time_fit(total_minutes, request.available_minutes)
    weather_fit = weather.score
    distance_fit = _distance_fit(route, request)
    group_fit = _group_fit(place, request)
    interest_fit = _interest_fit(place, request.interests)
    place_quality = _place_quality(place)
    safety_fit, warnings = _safety_fit(place, request, weather, total_minutes)

    score = round(
        0.20 * time_fit
        + 0.20 * weather_fit
        + 0.15 * distance_fit
        + 0.15 * safety_fit
        + 0.10 * group_fit
        + 0.10 * interest_fit
        + 0.10 * place_quality
    )
    breakdown = ScoreBreakdown(
        time_fit=time_fit,
        weather_fit=weather_fit,
        distance_fit=distance_fit,
        safety_fit=safety_fit,
        group_fit=group_fit,
        interest_fit=interest_fit,
        place_quality=place_quality,
    )
    return ScoredCandidate(
        place=place,
        route=route,
        total_minutes=total_minutes,
        score=_clamp(score),
        breakdown=breakdown,
        why=_why(place, route, weather, breakdown, request),
        warnings=warnings,
        description=_description(place),
        data_confidence=_confidence(place, route, weather),
    )


def to_recommendation(scored: ScoredCandidate) -> Recommendation:
    place = scored.place
    route = scored.route
    return Recommendation(
        id=place.source_id.replace(":", "_"),
        title=place.name,
        place_type=place.type,
        lat=place.lat,
        lon=place.lon,
        adventure_score=scored.score,
        score_breakdown=scored.breakdown,
        total_minutes=scored.total_minutes,
        travel_minutes=route.round_trip_minutes,
        activity_minutes=place.estimated_activity_minutes,
        distance_km=route.distance_km,
        walking_km=place.estimated_walking_km,
        difficulty=place.difficulty,
        description=scored.description,
        why=scored.why,
        warnings=scored.warnings,
        map_url=route.map_url,
        source=place.source,
        data_confidence=scored.data_confidence,  # type: ignore[arg-type]
        tags=place.tags,
    )


def rejected_from_scored(items: list[ScoredCandidate], chosen_ids: set[str], limit: int = 3) -> list[RejectedAlternative]:
    rejected: list[RejectedAlternative] = []
    for item in sorted(items, key=lambda c: c.score):
        if item.place.source_id in chosen_ids:
            continue
        reasons = []
        b = item.breakdown
        if b.time_fit < 60:
            reasons.append("does not fit the available time")
        if b.safety_fit < 65:
            reasons.append("safety/weather risk")
        if b.group_fit < 65:
            reasons.append("not ideal for the selected group")
        if b.interest_fit < 55:
            reasons.append("weak match with interests")
        reason = ", ".join(reasons) if reasons else "lower overall Adventure Score"
        rejected.append(RejectedAlternative(title=item.place.name, reason=reason, score=item.score))
        if len(rejected) >= limit:
            break
    return rejected
