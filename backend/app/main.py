from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

from app.config import settings
from app.schemas import (
    AdventureRequest,
    AnalyticsEvent,
    AuthLoginRequest,
    AuthRegisterRequest,
    AuthStatusResponse,
    ExplanationsRequest,
    FeedbackRequest,
    ParseTextRequest,
    VisitedRequest,
    WantToVisitRequest,
)
from app.services import auth
from app.services import explanations as explanations_service
from app.services.analytics import ab_summary
from app.services.llm.factory import get_llm_provider
from app.services.llm.template import TemplateProvider
from app.services.recommendations import build_recommendations
from app.services.search import save_response
from app.services.storage import storage

app = FastAPI(title=settings.app_name, version=settings.version)

logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


def _client_ip(request: Request) -> str:
    """Rate-limit bucket key. Behind Fly's proxy the real browser address is in
    Fly-Client-IP; fall back to the first X-Forwarded-For hop, then the socket
    peer for local dev. Keying on the proxy's own IP would lump every visitor
    into one bucket and rate-limit the whole site at once."""
    fly_ip = request.headers.get("fly-client-ip")
    if fly_ip:
        return fly_ip
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return get_remote_address(request)


limiter = Limiter(
    key_func=_client_ip,
    default_limits=[settings.rate_limit_default],
    enabled=settings.rate_limit_enabled,
    headers_enabled=True,
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

# Same-origin by default (empty allow-list). Widen only via ALLOWED_ORIGINS.
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.allowed_origins),
    allow_credentials=True,
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


def _auth_status(session: auth.SessionContext | None) -> AuthStatusResponse:
    if session is None:
        return AuthStatusResponse(user=None, csrf_token=None)
    return AuthStatusResponse(user=session.user, csrf_token=session.csrf_token)


def _session(request: Request) -> auth.SessionContext | None:
    return auth.session_context_from_request(request)


def _required_session(request: Request) -> auth.SessionContext:
    session = _session(request)
    if session is None:
        raise HTTPException(status_code=401, detail="auth_required")
    return session


def _raise_auth_error(exc: auth.AuthError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.detail)


@app.get("/api/features")
async def features() -> dict[str, bool]:
    return {
        "parse": _parse_feature_enabled(),
        "require_auth_for_more_recommendations": settings.require_auth_for_more_recommendations,
    }


@app.get("/api/auth/config")
async def auth_config() -> dict[str, bool]:
    return {"email_enabled": True, "google_enabled": auth.google_oauth_enabled()}


@app.get("/api/auth/me", response_model=AuthStatusResponse)
async def auth_me(request: Request) -> AuthStatusResponse:
    return _auth_status(_session(request))


@app.post("/api/auth/register", response_model=AuthStatusResponse)
@limiter.limit(settings.rate_limit_auth)
async def auth_register(
    request: Request,
    response: Response,
    payload: AuthRegisterRequest,
) -> AuthStatusResponse:
    try:
        account = auth.register_email(payload.email, payload.password, payload.anonymous_id, payload.lang)
        session = auth.start_session(response, account, user_agent=request.headers.get("user-agent"))
    except auth.AuthError as exc:
        _raise_auth_error(exc)
    return _auth_status(session)


@app.post("/api/auth/login", response_model=AuthStatusResponse)
@limiter.limit(settings.rate_limit_auth)
async def auth_login(
    request: Request,
    response: Response,
    payload: AuthLoginRequest,
) -> AuthStatusResponse:
    try:
        account = auth.login_email(payload.email, payload.password, payload.anonymous_id)
        session = auth.start_session(response, account, user_agent=request.headers.get("user-agent"))
    except auth.AuthError as exc:
        _raise_auth_error(exc)
    return _auth_status(session)


@app.post("/api/auth/logout", response_model=AuthStatusResponse)
async def auth_logout(request: Request, response: Response) -> AuthStatusResponse:
    session = _required_session(request)
    try:
        auth.require_csrf(request, session)
    except auth.AuthError as exc:
        _raise_auth_error(exc)
    storage.auth.revoke_session(session.token_hash)
    auth.clear_session_cookie(response)
    return AuthStatusResponse(user=None, csrf_token=None)


@app.get("/api/auth/google/start")
@limiter.limit(settings.rate_limit_auth)
async def auth_google_start(request: Request, anonymous_id: str | None = None) -> RedirectResponse:  # noqa: ARG001
    try:
        url, state = auth.create_google_authorization_url(anonymous_id)
    except auth.AuthError as exc:
        _raise_auth_error(exc)
    redirect = RedirectResponse(url)
    auth.set_oauth_state_cookie(redirect, state)
    return redirect


@app.get("/api/auth/google/callback")
@limiter.limit(settings.rate_limit_auth)
async def auth_google_callback(request: Request, code: str | None = None, state: str | None = None, error: str | None = None):
    if error or not code or not state:
        raise HTTPException(status_code=400, detail="google_oauth_failed")
    try:
        auth.require_oauth_state_cookie(request, state)
        account, _ = await auth.finish_google_login(code, state)
        redirect = RedirectResponse("/", status_code=303)
        auth.start_session(redirect, account, user_agent=request.headers.get("user-agent"))
        auth.clear_oauth_state_cookie(redirect)
        return redirect
    except auth.AuthError as exc:
        _raise_auth_error(exc)


@app.post("/api/parse-request")
@limiter.limit(settings.rate_limit_parse)
async def parse_request(request: Request, response: Response, payload: ParseTextRequest) -> dict[str, Any]:
    if not _parse_feature_enabled():
        raise HTTPException(status_code=404, detail="parse_disabled")
    granted = storage.api_usage.reserve_api_calls(
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
        "interests": ["history", "fortresses", "viewpoints", "caves"],
        "max_walking_km": 3,
        "request_text": "Family trip for 5 hours with fortress, history and views.",
        "use_live_data": True,
        "limit": 5,
    }


@app.post("/api/recommendations")
@limiter.limit(settings.rate_limit_recommendations)
async def recommendations(request: Request, response: Response, payload: AdventureRequest):
    session = _session(request)
    if settings.require_auth_for_more_recommendations and session is None and payload.exclude_seen:
        raise HTTPException(status_code=401, detail="auth_required_for_more_recommendations")
    if session is not None:
        try:
            auth.require_csrf(request, session)
        except auth.AuthError as exc:
            _raise_auth_error(exc)
    result = await build_recommendations(payload, account_id=session.account_id if session else None)
    save_response(result.request_id, payload, result, account_id=session.account_id if session else None)
    return result


@app.post("/api/explanations")
@limiter.limit(settings.rate_limit_explanations)
async def explanations(request: Request, response: Response, payload: ExplanationsRequest) -> dict[str, Any]:
    session = _session(request)
    if session is not None:
        try:
            auth.require_csrf(request, session)
        except auth.AuthError as exc:
            _raise_auth_error(exc)
    items = await explanations_service.resolve(payload.request_id)
    return {"request_id": payload.request_id, "explanations": items}


@app.post("/api/feedback")
async def feedback(request: Request, payload: FeedbackRequest) -> dict[str, str]:
    session = _session(request)
    if session is not None:
        try:
            auth.require_csrf(request, session)
        except auth.AuthError as exc:
            _raise_auth_error(exc)
    storage.save_feedback(payload, account_id=session.account_id if session else None)
    return {"status": "ok"}


@app.get("/api/feedback")
async def feedback_list() -> dict[str, Any]:
    return {"items": storage.feedback.summary()}


@app.post("/api/events")
async def events(request: Request, payload: AnalyticsEvent) -> dict[str, str]:
    session = _session(request)
    if session is not None:
        try:
            auth.require_csrf(request, session)
        except auth.AuthError as exc:
            _raise_auth_error(exc)
    storage.save_event(payload, account_id=session.account_id if session else None)
    return {"status": "ok"}


@app.get("/api/events")
async def events_list() -> dict[str, Any]:
    return {"items": storage.events.summary()}


@app.get("/api/ab")
async def ab() -> dict[str, Any]:
    return {"variants": ab_summary(storage)}


@app.post("/api/visited")
async def visited(request: Request, payload: VisitedRequest) -> dict[str, str]:
    session = _required_session(request)
    try:
        auth.require_csrf(request, session)
    except auth.AuthError as exc:
        _raise_auth_error(exc)
    storage.place_marks.mark_visited_account(session.account_id, payload.source_id)
    return {"status": "ok"}


@app.delete("/api/visited")
async def visited_clear(request: Request, anonymous_id: str | None = None) -> dict[str, Any]:  # noqa: ARG001
    session = _required_session(request)
    try:
        auth.require_csrf(request, session)
    except auth.AuthError as exc:
        _raise_auth_error(exc)
    return {"status": "ok", "cleared": storage.place_marks.clear_visited_account(session.account_id)}


@app.post("/api/want-to-visit")
async def want_to_visit(request: Request, payload: WantToVisitRequest) -> dict[str, Any]:
    session = _required_session(request)
    try:
        auth.require_csrf(request, session)
    except auth.AuthError as exc:
        _raise_auth_error(exc)
    storage.place_marks.set_want_to_visit_account(session.account_id, payload.source_id, payload.wanted)
    return {"status": "ok", "wanted": payload.wanted}


@app.get("/api/want-to-visit")
async def want_to_visit_list(request: Request) -> dict[str, Any]:
    session = _required_session(request)
    return {"items": storage.place_marks.wanted_places_account(session.account_id)}


@app.delete("/api/want-to-visit")
async def want_to_visit_clear(request: Request) -> dict[str, Any]:
    session = _required_session(request)
    try:
        auth.require_csrf(request, session)
    except auth.AuthError as exc:
        _raise_auth_error(exc)
    return {"status": "ok", "cleared": storage.place_marks.clear_want_to_visit_account(session.account_id)}


@app.get("/api/history")
async def history(request: Request, anonymous_id: str | None = None) -> dict[str, Any]:  # noqa: ARG001
    session = _required_session(request)
    return {"items": storage.search.history_for_account(session.account_id)}


@app.delete("/api/history")
async def history_delete(request: Request, anonymous_id: str | None = None) -> dict[str, Any]:  # noqa: ARG001
    session = _required_session(request)
    try:
        auth.require_csrf(request, session)
    except auth.AuthError as exc:
        _raise_auth_error(exc)
    return {"status": "ok", "deleted_sessions": storage.lifecycle.delete_account_history(session.account_id)}
