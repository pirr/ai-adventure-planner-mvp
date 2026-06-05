from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.schemas import AdventureRequest, FeedbackRequest
from app.services.recommendations import build_recommendations
from app.services.storage import storage

app = FastAPI(title=settings.app_name, version=settings.version)

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
