from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.schemas import AdventureRequest, AnalyticsEvent, FeedbackRequest, ParseTextRequest, VisitedRequest
from app.services.llm.factory import get_llm_provider
from app.services.llm.template import TemplateProvider
from app.services.recommendations import build_recommendations
from app.services.storage import storage

app = FastAPI(title=settings.app_name, version=settings.version)

logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# The frontend lives in the repo-root `frontend/` directory, served by this
# backend at the same origin so its relative `/api/...` calls keep working.
FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "version": settings.version}


def _parse_feature_enabled() -> bool:
    if not settings.llm_parse_enabled:
        return False
    if settings.llm_parse_daily_limit <= 0 or settings.llm_parse_user_daily_limit <= 0:
        return False
    return not isinstance(get_llm_provider(), TemplateProvider)


@app.get("/api/features")
async def features() -> dict[str, bool]:
    return {"parse": _parse_feature_enabled()}


@app.post("/api/parse-request")
async def parse_request(payload: ParseTextRequest) -> dict[str, Any]:
    if not _parse_feature_enabled():
        raise HTTPException(status_code=404, detail="parse_disabled")
    granted = storage.reserve_api_calls(
        "parse",
        payload.anonymous_id,
        1,
        daily_limit=settings.llm_parse_daily_limit,
        user_daily_limit=settings.llm_parse_user_daily_limit,
    )
    if granted < 1:
        raise HTTPException(status_code=429, detail="parse_budget_exhausted")
    try:
        parsed = await get_llm_provider().parse_situation(payload.text, payload.lang)
    except Exception:  # noqa: BLE001 - provider bugs must not 500 with a stack trace
        raise HTTPException(status_code=502, detail="parse_failed")
    if parsed is not None and parsed.is_empty():
        parsed = None
    return {"parsed": parsed.dict() if parsed is not None else None}


@app.get("/api/sample-request")
async def sample_request() -> dict[str, Any]:
    return {
        "lat": 42.4304,
        "lon": 18.6964,
        "available_minutes": 300,
        "transport_mode": "car",
        "group_type": "family",
        "children_ages": [6, 13],
        "intensity": "easy",
        "interests": ["history", "fortresses", "viewpoints"],
        "max_walking_km": 3,
        "request_text": "Family trip for 5 hours with fortress, history and views.",
        "use_live_data": True,
        "limit": 5,
    }


@app.post("/api/recommendations")
async def recommendations(request: AdventureRequest):
    response = await build_recommendations(request)
    storage.save_response(response.request_id, request, response)
    return response


@app.post("/api/feedback")
async def feedback(payload: FeedbackRequest) -> dict[str, str]:
    storage.save_feedback(payload)
    return {"status": "ok"}


@app.get("/api/feedback")
async def feedback_list() -> dict[str, Any]:
    return {"items": storage.feedback_summary()}


@app.post("/api/events")
async def events(payload: AnalyticsEvent) -> dict[str, str]:
    storage.save_event(payload)
    return {"status": "ok"}


@app.get("/api/events")
async def events_list() -> dict[str, Any]:
    return {"items": storage.events_summary()}


@app.get("/api/ab")
async def ab() -> dict[str, Any]:
    return {"variants": storage.ab_summary()}


@app.post("/api/visited")
async def visited(payload: VisitedRequest) -> dict[str, str]:
    storage.mark_visited(payload.anonymous_id, payload.source_id)
    return {"status": "ok"}


@app.delete("/api/visited")
async def visited_clear(anonymous_id: str | None = None) -> dict[str, Any]:
    return {"status": "ok", "cleared": storage.clear_visited(anonymous_id)}


@app.get("/api/history")
async def history(anonymous_id: str | None = None) -> dict[str, Any]:
    return {"items": storage.history_for(anonymous_id)}


@app.delete("/api/history")
async def history_delete(anonymous_id: str | None = None) -> dict[str, Any]:
    return {"status": "ok", "deleted_sessions": storage.delete_user_data(anonymous_id)}
