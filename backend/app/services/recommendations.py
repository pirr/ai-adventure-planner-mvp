from __future__ import annotations

import asyncio
import uuid
from datetime import datetime

from app.schemas import AdventureRequest, AdventureResponse
from app.services.place_photos import get_place_photo
from app.services.places import get_candidate_places
from app.services.routing import get_route
from app.services.llm import LLMProvider, explain_recommendations, get_llm_provider
from app.services.scoring import rejected_from_scored, score_candidate, to_recommendation
from app.services.storage import storage
from app.services.weather import get_destination_forecasts, get_weather


async def build_recommendations(request: AdventureRequest, provider: LLMProvider | None = None) -> AdventureResponse:
    request_id = str(uuid.uuid4())
    # Personal Preference Fit signal from this user's past feedback (cold-start safe).
    profile = storage.preference_profile(request.anonymous_id)
    weather, weather_warnings = await get_weather(request.lat, request.lon, request.use_live_data, request.lang)
    places, place_warnings = await get_candidate_places(
        request.lat,
        request.lon,
        request.available_minutes,
        request.transport_mode,
        request.interests,
        request.use_live_data,
        request.lang,
    )

    routes = await asyncio.gather(
        *[get_route(request.lat, request.lon, place, request.transport_mode, request.use_live_data) for place in places[:40]]
    )
    # First pass ranks every candidate using the weather at the user's origin.
    scored = [score_candidate(place, route, weather, request, profile) for place, route in zip(places[:40], routes)]
    scored.sort(key=lambda c: c.score, reverse=True)

    # Second pass re-scores only the strongest candidates with the weather at
    # their arrival time, so a place that turns rainy by the time you get there
    # drops in the ranking. Limiting the pool keeps this to one extra request.
    pool_size = min(len(scored), request.limit + 5)
    pool, rest = scored[:pool_size], scored[pool_size:]
    forecasts = await get_destination_forecasts(
        [(candidate.place.lat, candidate.place.lon) for candidate in pool], request.use_live_data, request.lang
    )
    rescored = []
    for candidate, forecast in zip(pool, forecasts):
        if forecast is None:
            rescored.append(candidate)
            continue
        arrival_weather, timeline = forecast.at_arrival(
            candidate.route.one_way_minutes, candidate.place.estimated_activity_minutes, request.lang
        )
        refreshed = score_candidate(candidate.place, candidate.route, arrival_weather, request, profile)
        refreshed.arrival_weather = arrival_weather
        refreshed.forecast = timeline
        rescored.append(refreshed)

    final = sorted(rescored + rest, key=lambda c: c.score, reverse=True)
    top = final[: request.limit]
    photos = await asyncio.gather(*[get_place_photo(item.place, request.use_live_data) for item in top])
    recommendations = [to_recommendation(item, photo) for item, photo in zip(top, photos)]
    recommendations = await explain_recommendations(recommendations, request, provider or get_llm_provider())
    chosen_ids = {item.place.source_id for item in top}
    rejected = rejected_from_scored(final, chosen_ids, limit=3, lang=request.lang)
    return AdventureResponse(
        request_id=request_id,
        generated_at=datetime.utcnow(),
        weather=weather,
        recommendations=recommendations,
        rejected_alternatives=rejected,
        data_warnings=weather_warnings + place_warnings,
    )
