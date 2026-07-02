from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Iterable

from app.config import settings
from app.schemas import AdventureRequest, CommunitySignal, HourlyForecast, PlaceCandidate, PlacePhoto, Recommendation, RejectedAlternative, RouteInfo, ScoreBreakdown, WeatherSummary
from app.services.geo import haversine_km
from app.services.i18n import t


INTEREST_ALIASES = {
    "views": "viewpoints",
    "view": "viewpoints",
    "history": "history",
    "historic": "history",
    "fortress": "fortresses",
    "fortresses": "fortresses",
    "castle": "fortresses",
    "nature": "nature",
    "cave": "caves",
    "caves": "caves",
    "water": "water",
    "food": "food",
    "drink": "drinks",
    "drinks": "drinks",
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
    "cave": {"caves", "nature", "active"},
    "natural_site": {"nature", "viewpoints"},
    "picnic": {"nature", "family", "food"},
    "trail": {"nature", "active"},
    "food": {"food"},
    "drinks": {"drinks"},
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
    arrival_weather: WeatherSummary | None = None
    forecast: list[HourlyForecast] = field(default_factory=list)


def normalize_interest(value: str) -> str:
    return INTEREST_ALIASES.get(value.strip().lower(), value.strip().lower())


def _place_interests(place: PlaceCandidate) -> set[str]:
    """The set of interests a place can satisfy: its OSM-tagged interests, the
    interests inferred from its type, and the (normalized) type itself."""
    tag_interests = set(map(normalize_interest, place.tags.get("interests", [])))
    return tag_interests | PLACE_INTERESTS.get(place.type, set()) | {normalize_interest(place.type)}


def place_matches_interest(place: PlaceCandidate, interest: str) -> bool:
    """True when `place` satisfies `interest`, using the same availability set
    `_interest_fit` scores on. Shared so ranking and scoring agree."""
    return normalize_interest(interest) in _place_interests(place)


def apply_primary_rerank(candidates: list["ScoredCandidate"], request: AdventureRequest) -> list["ScoredCandidate"]:
    """Lead with places that match a *focused* request (exactly one interest,
    not 'surprise me'). Stable partition — order only, scores untouched. No-op
    for multi-interest or 'surprise me' searches."""
    interests = [normalize_interest(i) for i in request.interests]
    if len(interests) != 1 or interests[0] == "surprise me":
        return list(candidates)
    primary = interests[0]
    matched = [c for c in candidates if place_matches_interest(c.place, primary)]
    rest = [c for c in candidates if not place_matches_interest(c.place, primary)]
    return matched + rest


# Diversity guard for the final selection. A dense cluster of near-identical POIs
# (a dozen central war memorials — all wikidata, so flat quality) otherwise
# monopolises the top-K: within one type the fits barely differ and proximity ties
# them, so the head collapses onto one spot/type. Three SOFT rules, each with a
# graceful fallback so a small single-theme town (e.g. Kotor's old town) still fills
# `limit`:
#   1. name-dedup  — the same place must not appear twice (even under two types);
#   2. spatial gap — skip a same-type POI within DIVERSITY_GAP_M of a picked one;
#   3. type cap    — at most DIVERSITY_TYPE_CAP of any single type.
# If the rules leave fewer than `limit`, top up from the deferred by notability
# (quality_score) — a freed slot gets the best remaining place, not the next plaque.
# Order-only, scores untouched.
DIVERSITY_GAP_M = 400.0
DIVERSITY_TYPE_CAP = 2


def _norm_name(name: str) -> str:
    return " ".join(name.lower().split())


def _clusters_with(candidate: "ScoredCandidate", picked: list["ScoredCandidate"], gap_m: float) -> bool:
    p = candidate.place
    return any(
        p.type == q.place.type and haversine_km(p.lat, p.lon, q.place.lat, q.place.lon) * 1000 <= gap_m
        for q in picked
    )


def _on_interest_predicate(interests: list[str] | None):
    """place -> True if it satisfies a requested interest. Everything is 'wanted'
    for an empty or 'surprise me' request (no interest to bias variety toward)."""
    if not interests:
        return lambda place: True
    norm = [normalize_interest(i) for i in interests]
    if "surprise me" in norm:
        return lambda place: True
    return lambda place: any(place_matches_interest(place, i) for i in norm)


def apply_diversity(
    candidates: list["ScoredCandidate"], *, limit: int,
    gap_m: float = DIVERSITY_GAP_M, type_cap: int = DIVERSITY_TYPE_CAP,
    interests: list[str] | None = None,
) -> list["ScoredCandidate"]:
    """Spread the top-`limit` so no single place, cluster, or type owns the head,
    while staying within what the user asked for and never returning fewer than a
    plain top-`limit` (a single-theme town falls back to its core). Soft rules:
    name-dedup, a spatial gap and a per-type cap on same-type POIs, and — when
    `interests` is given — off-interest types are held back (a food+drinks trip
    won't get a war memorial injected just for variety). Assumes `candidates` is
    already score-ranked; returns the full list reordered so downstream selection
    and alternatives still see every candidate."""
    if limit <= 0 or len(candidates) <= 1:
        return list(candidates)
    on_interest = _on_interest_predicate(interests)
    picked: list["ScoredCandidate"] = []
    deferred: list["ScoredCandidate"] = []
    seen_names: set[str] = set()
    type_counts: dict[str, int] = {}
    for candidate in candidates:
        p = candidate.place
        name = _norm_name(p.name)
        redundant = (
            len(picked) >= limit
            or name in seen_names
            or not on_interest(p)
            or _clusters_with(candidate, picked, gap_m)
            or type_counts.get(p.type, 0) >= type_cap
        )
        if redundant:
            deferred.append(candidate)
        else:
            picked.append(candidate)
            seen_names.add(name)
            type_counts[p.type] = type_counts.get(p.type, 0) + 1
    if len(picked) < limit:
        # Short after the soft rules -> relax gap/cap/off-interest (a single-theme or
        # narrow request isn't at fault), preferring on-interest then notability,
        # never re-introducing a duplicate place.
        for candidate in sorted(deferred, key=lambda c: (on_interest(c.place), c.place.quality_score), reverse=True):
            name = _norm_name(candidate.place.name)
            if name in seen_names:
                continue
            picked.append(candidate)
            seen_names.add(name)
            if len(picked) >= limit:
                break
    picked_ids = {id(c) for c in picked}
    ordered = picked + [c for c in candidates if id(c) not in picked_ids]
    return _promote_interest_coverage(ordered, limit, interests)


def _requested_interest_list(interests: list[str] | None) -> list[str]:
    requested: list[str] = []
    for interest in interests or []:
        normalized = normalize_interest(interest)
        if not normalized or normalized == "surprise me" or normalized in requested:
            continue
        requested.append(normalized)
    return requested


def _promote_interest_coverage(
    ordered: list["ScoredCandidate"],
    limit: int,
    interests: list[str] | None,
) -> list["ScoredCandidate"]:
    requested = _requested_interest_list(interests)
    if limit <= 0 or len(ordered) <= 1 or not requested:
        return ordered

    top = list(ordered[:limit])
    rest = list(ordered[limit:])
    if not top:
        return ordered

    def covers(items: list["ScoredCandidate"], interest: str) -> bool:
        return any(place_matches_interest(item.place, interest) for item in items)

    def coverage_counts() -> dict[str, int]:
        return {
            interest: sum(1 for item in top if place_matches_interest(item.place, interest))
            for interest in requested
        }

    for interest in requested:
        if covers(top, interest):
            continue
        top_names = {_norm_name(item.place.name) for item in top}
        promote_index = next(
            (
                index for index, item in enumerate(rest)
                if place_matches_interest(item.place, interest) and _norm_name(item.place.name) not in top_names
            ),
            None,
        )
        if promote_index is None:
            continue
        promoted = rest.pop(promote_index)
        if len(top) < limit:
            top.append(promoted)
            continue

        counts = coverage_counts()
        replace_index = None
        for index in range(len(top) - 1, -1, -1):
            matched = [item for item in requested if place_matches_interest(top[index].place, item)]
            if all(counts[item] > 1 for item in matched):
                replace_index = index
                break
        if replace_index is None:
            replace_index = len(top) - 1
        displaced = top[replace_index]
        top[replace_index] = promoted
        rest.insert(0, displaced)

    return top + rest


def _clamp(value: int | float) -> int:
    return max(0, min(100, int(round(value))))


# Community Confidence: aggregated first-party signal from other adventurers.
# Guardrails against rich-get-richer popularity bias: the liked-ratio only nudges
# the score once a place has a few distinct ratings, and even then it is shrunk
# toward neutral by sample size. Badges (the loud social-proof chips) need a higher
# bar so they only appear once a place is genuinely well-attested. The four gates
# are env-tunable via Settings (see config.py); only the neutral baseline is fixed,
# because it must match ScoreBreakdown's cold-start default.
COMMUNITY_NEUTRAL = 70  # mirrors personal_preference_fit's cold-start neutral


def _community_confidence_fit(place: PlaceCandidate, community: dict | None) -> int:
    """Cross-user 'wisdom of our adventurers' for this place, shrunk toward neutral
    by sample size. Neutral (70) on cold start or thin data so a place is never
    carried (or sunk) by one or two votes."""
    sig = (community or {}).get(place.source_id)
    if not sig:
        return COMMUNITY_NEUTRAL
    ups, downs = sig.get("ups", 0), sig.get("downs", 0)
    rated = ups + downs
    prior = settings.community_shrink_prior
    # Experience: liked-ratio shrunk toward neutral by sample size (unchanged).
    score: float = COMMUNITY_NEUTRAL
    if rated >= settings.community_min_raters:
        raw = (ups / rated) * 100
        score = (raw * rated + COMMUNITY_NEUTRAL * prior) / (rated + prior)
    # Intent: one-sided "want to visit" demand nudges the score upward, saturating
    # with distinct wanters (the prior shrinks thin samples), capped by want_span.
    wants = sig.get("want_to_visit", 0)
    if wants >= settings.community_min_raters:
        score += settings.community_want_span * (wants / (wants + prior))
    return _clamp(score)


def _community_badge(community: dict | None, source_id: str | None) -> CommunitySignal | None:
    """Build the user-facing social-proof badge for a place, or None when neither
    dimension clears its display threshold (keeps badges meaningful and anonymous)."""
    sig = (community or {}).get(source_id)
    if not sig:
        return None
    raters, visits = sig.get("raters", 0), sig.get("recent_visits", 0)
    wants = sig.get("want_to_visit", 0)
    show_liked = raters >= settings.community_badge_min_raters
    show_visits = visits >= settings.community_badge_min_visits
    show_wants = wants >= settings.community_badge_min_wants
    if not (show_liked or show_visits or show_wants):
        return None
    ups, downs = sig.get("ups", 0), sig.get("downs", 0)
    rated = ups + downs
    return CommunitySignal(
        liked_ratio=round(ups / rated, 2) if (show_liked and rated) else 0.0,
        sample_size=raters if show_liked else 0,
        recent_visits=visits if show_visits else 0,
        want_to_visit=wants if show_wants else 0,
    )


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
    normalized_list = [normalize_interest(item) for item in interests]
    normalized = set(normalized_list)
    if not normalized or "surprise me" in normalized:
        return 78
    available = _place_interests(place)
    matches = normalized & available
    if matches:
        primary_bonus = 10 if normalized_list and normalized_list[0] in matches else 0
        return min(100, 70 + 15 * len(matches) + primary_bonus)
    if {"nature", "viewpoints"} & normalized and place.type in {"park", "water", "viewpoint"}:
        return 82
    if {"history", "fortresses"} & normalized and place.type in {"historic_site", "fortress", "museum"}:
        return 88
    return 35


def _group_fit(place: PlaceCandidate, request: AdventureRequest) -> int:
    score = 88
    has_children = request.group_type in {"family", "kids"} or bool(request.children_ages)
    young_child = any(age <= 7 for age in request.children_ages)
    has_dog = request.with_dog or request.group_type == "dog"
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
    if request.reduced_mobility:
        if place.difficulty == "hard":
            score -= 50
        elif place.difficulty == "medium":
            score -= 22
        if place.estimated_walking_km > 2:
            score -= 20
    elif request.with_elderly:
        if place.difficulty == "hard":
            score -= 30
        elif place.difficulty == "medium":
            score -= 10
        if place.estimated_walking_km > 3:
            score -= 15
    if has_dog and place.type in {"museum", "historic_site"}:
        score -= 20
    if max_walk is not None and place.estimated_walking_km > max_walk:
        score -= 35
    return _clamp(score)


def _effort_fit(place: PlaceCandidate, request: AdventureRequest) -> int:
    score = 100
    walk = place.estimated_walking_km

    if request.intensity == "easy":
        if place.difficulty == "medium":
            score -= 25
        elif place.difficulty == "hard":
            score -= 60
        if walk > 4:
            score -= 25
        elif walk > 2.5:
            score -= 18
        elif walk > 1.8:
            score -= 10
    elif request.intensity == "medium":
        if place.difficulty == "hard":
            score -= 20
        elif place.difficulty == "easy" and walk < 1:
            score -= 14
        if walk > 5:
            score -= 18
        elif walk < 0.8:
            score -= 10
    else:  # active
        if place.difficulty == "easy":
            score -= 28
        elif place.difficulty == "medium":
            score -= 8
        if walk < 1:
            score -= 22
        elif walk < 1.8:
            score -= 14
        elif walk >= 3.5:
            score += 6
        if "active" in _place_interests(place):
            score += 8

    return _clamp(score)


def _safety_fit(place: PlaceCandidate, request: AdventureRequest, weather: WeatherSummary, total_minutes: int) -> tuple[int, list[str]]:
    score = 88
    warnings: list[str] = []
    lang = request.lang

    rain_24h = weather.rain_mm_last_24h or 0
    temp = weather.temperature_c
    wind = weather.wind_kmh or 0
    uv = weather.uv_index or 0

    if rain_24h > 8 and place.type in {"trail", "park", "viewpoint", "water"}:
        score -= 18
        warnings.append(t(lang, "warn_rain_paths"))
    if rain_24h > 20:
        score -= 12
        warnings.append(t(lang, "warn_rain_heavy"))
    if temp is not None and temp > 32:
        score -= 18
        warnings.append(t(lang, "warn_high_temp"))
    if uv >= 8 and place.type in {"viewpoint", "water", "trail", "park"}:
        score -= 8
        warnings.append(t(lang, "warn_high_uv"))
    if wind > 35 and place.type in {"viewpoint", "water"}:
        score -= 12
        warnings.append(t(lang, "warn_strong_wind"))
    if place.difficulty == "hard":
        score -= 22
        warnings.append(t(lang, "warn_hard_terrain"))
    elif place.difficulty == "medium" and request.intensity == "easy":
        score -= 8
        warnings.append(t(lang, "warn_medium_difficulty"))

    if request.reduced_mobility and (place.difficulty in {"medium", "hard"} or place.estimated_walking_km > 2):
        score -= 15
        warnings.append(t(lang, "warn_reduced_mobility"))
    elif request.with_elderly and (place.difficulty == "hard" or place.estimated_walking_km > 3.5):
        score -= 10
        warnings.append(t(lang, "warn_elderly"))

    if request.max_walking_km is not None and place.estimated_walking_km > request.max_walking_km:
        warnings.append(t(lang, "warn_walking_over_limit", km=place.estimated_walking_km, limit=request.max_walking_km))

    if weather.sunset:
        try:
            sunset = datetime.fromisoformat(weather.sunset.replace("Z", "+00:00"))
            finish = datetime.now(sunset.tzinfo) + timedelta(minutes=total_minutes)
            if finish > sunset:
                score -= 20
                warnings.append(t(lang, "warn_after_sunset"))
        except Exception:
            pass

    return _clamp(score), warnings


def _place_quality(place: PlaceCandidate) -> int:
    return _clamp(place.quality_score)


def _personal_preference_fit(place: PlaceCandidate, profile: dict | None) -> int:
    """0–100 from the user's own feedback history; neutral (70) on cold start.

    Direct signal = net up/down for this place type. When the type is unseen, a
    half-weight signal spills over from related types that share an interest tag.
    """
    if not profile:
        return 70
    place_types = profile.get("place_types", {})
    net: float = place_types.get(place.type, 0)
    if net == 0:
        place_interests = PLACE_INTERESTS.get(place.type, set())
        related = sum(
            value for ptype, value in place_types.items()
            if PLACE_INTERESTS.get(ptype, set()) & place_interests
        )
        net = related * 0.5
    if net == 0:
        return 70
    return _clamp(70 + 12 * net)


def _why(place: PlaceCandidate, route: RouteInfo, weather: WeatherSummary, breakdown: ScoreBreakdown, request: AdventureRequest) -> list[str]:
    items: list[str] = []
    lang = request.lang
    if breakdown.time_fit >= 80:
        items.append(t(lang, "why_fits_time"))
    if breakdown.weather_fit >= 75:
        # The template adds its own trailing period, so trim one off the summary
        # (some summaries are full sentences ending in ".") to avoid "..".
        items.append(t(lang, "why_weather", summary=weather.summary.lower().rstrip(" .")))
    if breakdown.distance_fit >= 80:
        items.append(t(lang, "why_travel", minutes=route.one_way_minutes))
    if breakdown.group_fit >= 80:
        items.append(t(lang, "why_group"))
    if breakdown.effort_fit >= 85:
        items.append(t(lang, "why_effort"))
    if breakdown.interest_fit >= 80:
        items.append(t(lang, "why_interest"))
    if breakdown.personal_preference_fit >= 85:
        items.append(t(lang, "why_preference"))
    if breakdown.community_confidence >= 80:
        items.append(t(lang, "why_community"))
    if place.estimated_walking_km <= 2.5:
        items.append(t(lang, "why_walking", km=place.estimated_walking_km))
    if not items:
        items.append(t(lang, "why_best"))
    return items[:5]


_DESCRIPTION_KEYS = {
    "viewpoint": "desc_viewpoint",
    "fortress": "desc_fortress",
    "historic_site": "desc_historic_site",
    "museum": "desc_museum",
    "park": "desc_park",
    "water": "desc_water",
    "cave": "desc_cave",
    "natural_site": "desc_natural_site",
    "picnic": "desc_picnic",
    "trail": "desc_trail",
}


def _description(place: PlaceCandidate, lang: str) -> str:
    return t(lang, _DESCRIPTION_KEYS.get(place.type, "desc_default"))


def _confidence(place: PlaceCandidate, route: RouteInfo, weather: WeatherSummary) -> str:
    live_count = sum([
        place.source in {"google_places", "openstreetmap"},
        route.confidence == "live",
        weather.confidence == "live",
    ])
    if live_count == 3:
        return "live"
    if live_count == 0:
        return "fallback"
    return "mixed"


def score_candidate(place: PlaceCandidate, route: RouteInfo, weather: WeatherSummary, request: AdventureRequest, profile: dict | None = None, community: dict | None = None) -> ScoredCandidate:
    total_minutes = route.round_trip_minutes + place.estimated_activity_minutes
    time_fit = _time_fit(total_minutes, request.available_minutes)
    weather_fit = weather.score
    distance_fit = _distance_fit(route, request)
    effort_fit = _effort_fit(place, request)
    group_fit = _group_fit(place, request)
    interest_fit = _interest_fit(place, request.interests)
    place_quality = _place_quality(place)
    personal_preference_fit = _personal_preference_fit(place, profile)
    community_confidence = _community_confidence_fit(place, community)
    safety_fit, warnings = _safety_fit(place, request, weather, total_minutes)

    # Adventure Score: explicit effort fit keeps Easy/Medium/Active searches
    # behaviorally distinct while safety and group fit still guard bad matches.
    # Personal and community fit split the old 8% personalization weight evenly;
    # both are neutral (70) until data exists, so cold-start scores are unchanged.
    score = round(
        0.15 * time_fit
        + 0.15 * weather_fit
        + 0.12 * distance_fit
        + 0.13 * safety_fit
        + 0.12 * effort_fit
        + 0.08 * group_fit
        + 0.09 * interest_fit
        + 0.08 * place_quality
        + 0.04 * personal_preference_fit
        + 0.04 * community_confidence
    )
    breakdown = ScoreBreakdown(
        time_fit=time_fit,
        weather_fit=weather_fit,
        distance_fit=distance_fit,
        safety_fit=safety_fit,
        effort_fit=effort_fit,
        group_fit=group_fit,
        interest_fit=interest_fit,
        place_quality=place_quality,
        personal_preference_fit=personal_preference_fit,
        community_confidence=community_confidence,
    )
    return ScoredCandidate(
        place=place,
        route=route,
        total_minutes=total_minutes,
        score=_clamp(score),
        breakdown=breakdown,
        why=_why(place, route, weather, breakdown, request),
        warnings=warnings,
        description=_description(place, request.lang),
        data_confidence=_confidence(place, route, weather),
    )


def to_recommendation(scored: ScoredCandidate, photo: PlacePhoto | None = None, community: dict | None = None) -> Recommendation:
    place = scored.place
    route = scored.route
    return Recommendation(
        id=place.source_id.replace(":", "_"),
        source_id=place.source_id,
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
        rating=place.rating,
        rating_count=place.rating_count,
        description=scored.description,
        why=scored.why,
        warnings=scored.warnings,
        map_url=route.map_url,
        apple_map_url=route.apple_map_url,
        source=place.source,
        data_confidence=scored.data_confidence,  # type: ignore[arg-type]
        photo=photo,
        arrival_weather=scored.arrival_weather,
        forecast=scored.forecast,
        tags=place.tags,
        community=_community_badge(community, place.source_id),
    )


def rejected_from_scored(items: list[ScoredCandidate], chosen_ids: set[str], limit: int = 3, lang: str = "en") -> list[RejectedAlternative]:
    rejected: list[RejectedAlternative] = []
    for item in sorted(items, key=lambda c: c.score):
        if item.place.source_id in chosen_ids:
            continue
        reasons = []
        b = item.breakdown
        if b.time_fit < 60:
            reasons.append(t(lang, "rej_no_time"))
        if b.safety_fit < 65:
            reasons.append(t(lang, "rej_safety"))
        if b.effort_fit < 65:
            reasons.append(t(lang, "rej_effort"))
        if b.group_fit < 65:
            reasons.append(t(lang, "rej_group"))
        if b.interest_fit < 55:
            reasons.append(t(lang, "rej_interest"))
        reason = ", ".join(reasons) if reasons else t(lang, "rej_lower_score")
        rejected.append(RejectedAlternative(title=item.place.name, reason=reason, score=item.score))
        if len(rejected) >= limit:
            break
    return rejected
