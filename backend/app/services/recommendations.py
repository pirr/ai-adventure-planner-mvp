from __future__ import annotations

import asyncio
import uuid
from datetime import datetime

from app.schemas import AdventureRequest, AdventureResponse
from app.services.place_photos import get_place_photo
from app.services.places import get_candidate_places
from app.services.routing import get_route
from app.services.scoring import rejected_from_scored, score_candidate, to_recommendation
from app.services.weather import get_weather


async def build_recommendations(request: AdventureRequest) -> AdventureResponse:
    request_id = str(uuid.uuid4())
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
    scored = [score_candidate(place, route, weather, request) for place, route in zip(places[:40], routes)]
    scored_sorted = sorted(scored, key=lambda c: c.score, reverse=True)
    top = scored_sorted[: request.limit]
    photos = await asyncio.gather(*[get_place_photo(item.place, request.use_live_data) for item in top])
    recommendations = [to_recommendation(item, photo) for item, photo in zip(top, photos)]
    chosen_ids = {item.place.source_id for item in top}
    rejected = rejected_from_scored(scored_sorted, chosen_ids, limit=3, lang=request.lang)
    return AdventureResponse(
        request_id=request_id,
        generated_at=datetime.utcnow(),
        weather=weather,
        recommendations=recommendations,
        rejected_alternatives=rejected,
        data_warnings=weather_warnings + place_warnings,
    )
