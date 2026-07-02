from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Awaitable, Protocol

from app.config import settings
from app.schemas import AdventureRequest, AdventureResponse, PlaceCandidate, WeatherSummary
from app.services import google_places
from app.services.place_photos import get_place_photo
from app.services.places import get_candidate_places
from app.services.routing import fallback_route, get_routes
from app.services.llm import LLMProvider, TemplateProvider, explain_recommendations
from app.services.llm.ab import explainer_provider
from app.services.explanations import stash as stash_explanations
from app.services.community import community_signals
from app.services.preferences import preference_profile, preference_profile_for_account
from app.services.scoring import ScoredCandidate, apply_diversity, apply_primary_rerank, rejected_from_scored, score_candidate, to_recommendation
from app.services.storage import storage
from app.services.weather import get_destination_forecasts, get_weather


logger = logging.getLogger(__name__)


class CandidateSource(Protocol):
    """Discovery seam: anything shaped like `places.get_candidate_places`.

    Production passes nothing and the default (`get_candidate_places`) is used,
    so behaviour is unchanged. The provider benchmark injects an alternative
    source to compare cheaper place-data providers through the real ranking
    pipeline. Kept here (not imported from eval/) so `app/` never depends on the
    benchmark code."""

    def __call__(
        self,
        lat: float,
        lon: float,
        available_minutes: int,
        transport_mode: str,
        interests: list[str],
        use_live_data: bool,
        lang: str = "en",
        anonymous_id: str | None = None,
    ) -> Awaitable[tuple[list[PlaceCandidate], list[str]]]: ...


@dataclass(frozen=True)
class ExperienceTier:
    """How much (costly) enrichment a request gets.

    Anonymous users get a deliberately lighter tier — rule-based explanations
    and a coarse, origin-only weather read — so their requests stay cheap and
    fast; signing in unlocks the full tier. Each lever is independently flag-
    gated so the whole policy lives, and changes, in one place."""

    llm_explanations: bool      # LLM prose vs. rule-based `why`
    destination_forecast: bool  # per-place arrival weather vs. origin only
    cache_origin_weather: bool  # serve origin weather from the coarse-grid cache


def _experience_tier(account_id: int | None) -> ExperienceTier:
    anonymous = account_id is None
    lite_weather = anonymous and settings.anon_fast_weather
    return ExperienceTier(
        llm_explanations=not (anonymous and settings.anon_disable_llm_explanations),
        destination_forecast=not lite_weather,
        cache_origin_weather=lite_weather,
    )


def _is_recommendable(candidate: ScoredCandidate, request: AdventureRequest) -> bool:
    # Scores can explain weak fits, but cards should still be practically doable.
    if candidate.route.round_trip_minutes > request.available_minutes:
        return False
    if candidate.total_minutes > request.available_minutes + 15:
        return False
    return candidate.breakdown.distance_fit > 15


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _order_key(candidate: ScoredCandidate, rotate: bool, seen: set[str]) -> tuple[int, int, float, int]:
    # "Show others": sink already-seen places below every unseen one (the
    # penalty exceeds the 0-100 score range), so fresh candidates surface
    # first and seen ones only backfill empty slots. Off otherwise, and it
    # never changes the displayed adventure_score.
    return (
        candidate.score - (1000 if rotate and candidate.place.source_id in seen else 0),
        candidate.place.quality_score,
        -candidate.route.distance_km,
        -candidate.total_minutes,
    )


def _select_candidates_for_routing(
    places: list[PlaceCandidate],
    request: AdventureRequest,
    weather: WeatherSummary,
    profile: dict | None,
    rotate: bool,
    seen: set[str],
) -> list[PlaceCandidate]:
    limit = max(request.limit + 8, settings.search_route_candidate_limit)
    if len(places) <= limit:
        return list(places)

    preliminary: list[ScoredCandidate] = []
    for place in places:
        route = fallback_route(request.lat, request.lon, place, request.transport_mode)
        preliminary.append(score_candidate(place, route, weather, request, profile))
    preliminary.sort(key=lambda candidate: _order_key(candidate, rotate, seen), reverse=True)
    return [candidate.place for candidate in preliminary[:limit]]


async def build_recommendations(
    request: AdventureRequest,
    provider: LLMProvider | None = None,
    account_id: int | None = None,
    defer_explanations: bool | None = None,
    candidate_source: CandidateSource | None = None,
) -> AdventureResponse:
    overall_started = time.perf_counter()
    request_id = str(uuid.uuid4())
    # Anonymous users get a lighter, cheaper tier (see ExperienceTier).
    tier = _experience_tier(account_id)
    # Personal Preference Fit signal from this user's past feedback (cold-start safe).
    profile = (
        preference_profile_for_account(account_id, store=storage)
        if account_id is not None
        else preference_profile(request.anonymous_id, store=storage)
    )
    # Origin weather and place search are independent; run them concurrently so
    # the slower of the two (Overpass on a cache miss) sets the latency, not the sum.
    stage_started = time.perf_counter()
    # Discovery seam: production uses get_candidate_places; the benchmark can
    # inject an alternative provider to compare it through the real pipeline.
    discover = candidate_source or get_candidate_places
    (weather, weather_warnings), (places, place_warnings) = await asyncio.gather(
        get_weather(request.lat, request.lon, request.use_live_data, request.lang, cache_ok=tier.cache_origin_weather),
        discover(
            request.lat,
            request.lon,
            request.available_minutes,
            request.transport_mode,
            request.interests,
            request.use_live_data,
            request.lang,
            request.anonymous_id,
        ),
    )
    logger.info(
        "recommendations_timing request_id=%s stage=weather_places candidates=%d duration_ms=%d",
        request_id,
        len(places),
        _elapsed_ms(stage_started),
    )

    # Per-user place state: hard-drop visited places before we spend routing
    # calls on them; keep `seen` to optionally rotate past them below.
    marks = (
        storage.place_marks.account_place_marks(account_id)
        if account_id is not None
        else storage.place_marks.place_marks(request.anonymous_id)
    )
    visited, seen = marks["visited"], marks["seen"]
    if visited:
        places = [place for place in places if place.source_id not in visited]
    rotate = request.exclude_seen and bool(seen)

    stage_started = time.perf_counter()
    candidate_places = _select_candidates_for_routing(places, request, weather, profile, rotate, seen)
    logger.info(
        "recommendations_timing request_id=%s stage=prefilter input=%d selected=%d duration_ms=%d",
        request_id,
        len(places),
        len(candidate_places),
        _elapsed_ms(stage_started),
    )
    stage_started = time.perf_counter()
    routes = await get_routes(request.lat, request.lon, candidate_places, request.transport_mode, request.use_live_data)
    logger.info(
        "recommendations_timing request_id=%s stage=routing candidates=%d duration_ms=%d",
        request_id,
        len(candidate_places),
        _elapsed_ms(stage_started),
    )
    # Community Confidence: aggregate other adventurers' feedback/visits for just
    # the routed candidates (one bounded query), then thread it through every scoring
    # pass and into the final badges. Empty until first-party data accrues.
    community = community_signals([place.source_id for place in candidate_places], store=storage)

    # First pass ranks every candidate using the weather at the user's origin.
    scored = [score_candidate(place, route, weather, request, profile, community) for place, route in zip(candidate_places, routes)]
    scored.sort(key=lambda candidate: _order_key(candidate, rotate, seen), reverse=True)

    # Second pass re-scores only the strongest candidates with the weather at
    # their arrival time, so a place that turns rainy by the time you get there
    # drops in the ranking. Limiting the pool keeps this to one extra request.
    pool_size = min(len(scored), request.limit + 5)
    pool, rest = scored[:pool_size], scored[pool_size:]
    # Google enrichment (optional, key-gated) only covers the pool: ratings
    # sharpen place_quality right where ranking is decided, at bounded cost.
    # create_task starts the calls now so they overlap the forecast await.
    enrichment_started = time.perf_counter()
    enrichment_task = (
        asyncio.create_task(
            google_places.enrich_places([candidate.place for candidate in pool], request.anonymous_id, request.lang)
        )
        if request.use_live_data and google_places.enabled()
        else None
    )
    stage_started = time.perf_counter()
    forecasts = await get_destination_forecasts(
        [(candidate.place.lat, candidate.place.lon) for candidate in pool],
        request.use_live_data and tier.destination_forecast,
        request.lang,
    )
    logger.info(
        "recommendations_timing request_id=%s stage=destination_forecasts candidates=%d duration_ms=%d",
        request_id,
        len(pool),
        _elapsed_ms(stage_started),
    )
    google_warnings: list[str] = []
    google_info: dict[str, google_places.GooglePlaceInfo] = {}
    if enrichment_task is not None:
        google_info, google_warnings = await enrichment_task
        logger.info(
            "recommendations_timing request_id=%s stage=google_enrichment candidates=%d enriched=%d duration_ms=%d",
            request_id,
            len(pool),
            len(google_info),
            _elapsed_ms(enrichment_started),
        )
    rescored = []
    for candidate, forecast in zip(pool, forecasts):
        place = candidate.place
        info = google_info.get(place.source_id)
        if info is not None:
            place.rating = info.rating
            place.rating_count = info.rating_count
            place.google_photo_name = info.photo_name
            place.google_photo_attribution = info.photo_attribution
            place.quality_score = google_places.blended_quality(place.quality_score, info.rating, info.rating_count)
        if forecast is None:
            # No destination forecast, but an enriched place still needs its
            # score refreshed (with the origin weather) to pick up the rating.
            rescored.append(score_candidate(place, candidate.route, weather, request, profile, community) if info else candidate)
            continue
        arrival_weather, timeline = forecast.at_arrival(
            candidate.route.one_way_minutes, candidate.place.estimated_activity_minutes, request.lang
        )
        refreshed = score_candidate(place, candidate.route, arrival_weather, request, profile, community)
        refreshed.arrival_weather = arrival_weather
        refreshed.forecast = timeline
        rescored.append(refreshed)

    final = sorted(rescored + rest, key=lambda candidate: _order_key(candidate, rotate, seen), reverse=True)
    recommendable = [item for item in final if _is_recommendable(item, request)]
    # Focused single-interest searches lead with matching places (e.g. "drinks"
    # -> pubs first); multi-interest browse keeps the score order.
    ranked = apply_primary_rerank(recommendable, request)
    # Then spread the head so a dense cluster of near-identical POIs (a dozen central
    # war memorials, all wikidata) can't monopolise the results; name-dedup + a soft
    # per-type cap, with a graceful fallback for single-theme towns (see scoring).
    top = apply_diversity(ranked, limit=request.limit, interests=request.interests)[: request.limit]
    photos = await asyncio.gather(
        *[get_place_photo(item.place, request.use_live_data, request.anonymous_id) for item in top]
    )
    recommendations = [to_recommendation(item, photo, community) for item, photo in zip(top, photos)]
    # Per-user render state: which of these the current user already wants to visit.
    wanted = marks.get("want_to_visit", set())
    for rec in recommendations:
        rec.wanted = rec.source_id in wanted
    if provider is not None:
        explainer = provider
    elif not tier.llm_explanations:
        explainer = TemplateProvider()
    else:
        explainer = explainer_provider(request)
    defer = settings.defer_explanations if defer_explanations is None else defer_explanations
    explanations_pending = False
    if defer and recommendations and not isinstance(explainer, TemplateProvider):
        stash_explanations(request_id, recommendations, request)
        explanations_pending = True
    else:
        stage_started = time.perf_counter()
        recommendations = await explain_recommendations(recommendations, request, explainer)
        logger.info(
            "recommendations_timing request_id=%s stage=explain recommendations=%d duration_ms=%d",
            request_id,
            len(recommendations),
            _elapsed_ms(stage_started),
        )
    chosen_ids = {item.place.source_id for item in top}
    rejected = rejected_from_scored(final, chosen_ids, limit=3, lang=request.lang)
    response = AdventureResponse(
        request_id=request_id,
        generated_at=datetime.utcnow(),
        weather=weather,
        recommendations=recommendations,
        rejected_alternatives=rejected,
        data_warnings=weather_warnings + place_warnings + google_warnings,
        explanations_pending=explanations_pending,
    )
    logger.info(
        "recommendations_timing request_id=%s stage=total recommendations=%d duration_ms=%d",
        request_id,
        len(response.recommendations),
        _elapsed_ms(overall_started),
    )
    return response
