"""Rank a provider's candidates through the *production* scoring, offline.

This is what makes the comparison fair and reproducible: every provider's places
go through the same `score_candidate` weights, with routing replaced by the
deterministic `fallback_route` and weather pinned to a neutral constant. So the
only thing that varies between runs is the place data itself — exactly what the
benchmark is trying to isolate.

The two enrichment arms are toggled here: `enrich=True` runs Google enrichment on
the top pool (ratings/photos sharpen `place_quality`, as in production);
`enrich=False` is the cheap provider on its own.
"""
from __future__ import annotations

from app.schemas import AdventureRequest, PlaceCandidate, Recommendation, WeatherSummary
from app.services import google_places
from app.services.routing import fallback_route
from app.services.scoring import ScoredCandidate, apply_primary_rerank, score_candidate, to_recommendation

# Neutral, fixed weather: weather_fit contributes a constant 70 to every score,
# so a provider can't look better or worse because of live weather on the day.
NEUTRAL_WEATHER = WeatherSummary(source="benchmark-fixed", summary="", score=70, confidence="estimated")


def _score_all(candidates: list[PlaceCandidate], request: AdventureRequest) -> list[ScoredCandidate]:
    scored: list[ScoredCandidate] = []
    for place in candidates:
        route = fallback_route(request.lat, request.lon, place, request.transport_mode)
        scored.append(score_candidate(place, route, NEUTRAL_WEATHER, request))
    return scored


async def rank_offline(
    candidates: list[PlaceCandidate],
    request: AdventureRequest,
    *,
    enrich: bool = False,
    anonymous_id: str | None = None,
) -> list[ScoredCandidate]:
    """Score, optionally enrich the top pool, and return the ranked candidates.

    Mutates the passed candidates' rating/quality when enriching, so callers that
    reuse a pool across arms must pass a copy."""
    scored = _score_all(candidates, request)
    scored.sort(key=lambda candidate: candidate.score, reverse=True)

    if enrich and google_places.enabled():
        # Enrich only the pool that decides ranking, matching production's bounded cost.
        pool = [candidate.place for candidate in scored[: request.limit + 5]]
        info, _ = await google_places.enrich_places(pool, anonymous_id, request.lang)
        changed = False
        for place in pool:
            got = info.get(place.source_id)
            if got is None:
                continue
            place.rating = got.rating
            place.rating_count = got.rating_count
            place.google_photo_name = got.photo_name
            place.google_photo_attribution = got.photo_attribution
            place.quality_score = google_places.blended_quality(place.quality_score, got.rating, got.rating_count)
            changed = True
        if changed:
            scored = _score_all([candidate.place for candidate in scored], request)
            scored.sort(key=lambda candidate: candidate.score, reverse=True)

    return apply_primary_rerank(scored, request)


def to_recommendations(scored: list[ScoredCandidate], k: int) -> list[Recommendation]:
    """Top-k as full Recommendation objects (title/why/rating), for the LLM-judge."""
    return [to_recommendation(candidate) for candidate in scored[:k]]
