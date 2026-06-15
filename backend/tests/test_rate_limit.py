"""Tests for the production HTTP rate limiter.

Two things are worth testing: the Fly-aware client-IP key function (the novel
bit — slowapi itself is well covered upstream) and that the same wiring main.py
uses (limiter + SlowAPIMiddleware + handler) actually returns 429 over budget.
"""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from starlette.requests import Request as StarletteRequest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import _client_ip  # noqa: E402


def _request(headers: dict[str, str], client_host: str = "10.0.0.1") -> StarletteRequest:
    scope = {
        "type": "http",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "client": (client_host, 0),
    }
    return StarletteRequest(scope)


def test_client_ip_prefers_fly_header() -> None:
    req = _request(
        {"fly-client-ip": "203.0.113.7", "x-forwarded-for": "198.51.100.2, 10.0.0.5"}
    )
    assert _client_ip(req) == "203.0.113.7"


def test_client_ip_falls_back_to_forwarded_for() -> None:
    # No Fly header: take the first (original client) hop, not the proxy chain.
    req = _request({"x-forwarded-for": "198.51.100.2, 10.0.0.5"})
    assert _client_ip(req) == "198.51.100.2"


def test_client_ip_falls_back_to_socket_peer() -> None:
    req = _request({}, client_host="192.168.1.50")
    assert _client_ip(req) == "192.168.1.50"


def test_limiter_returns_429_over_budget() -> None:
    limiter = Limiter(key_func=_client_ip, default_limits=["2/minute"], enabled=True)
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.add_middleware(SlowAPIMiddleware)

    @app.get("/ping")
    async def ping(request: Request) -> dict[str, str]:  # noqa: ARG001
        return {"status": "ok"}

    client = TestClient(app)
    headers = {"fly-client-ip": "203.0.113.99"}
    assert client.get("/ping", headers=headers).status_code == 200
    assert client.get("/ping", headers=headers).status_code == 200
    blocked = client.get("/ping", headers=headers)
    assert blocked.status_code == 429

    # A different client IP is in its own bucket and is unaffected.
    assert client.get("/ping", headers={"fly-client-ip": "203.0.113.1"}).status_code == 200


def test_per_route_decorator_on_dict_endpoint() -> None:
    # Mirrors main.py's pattern: a @limiter.limit route that returns a dict.
    # With headers_enabled the decorator injects X-RateLimit-* headers, which
    # requires the endpoint to declare `response: Response` — regression guard.
    limiter = Limiter(key_func=_client_ip, enabled=True, headers_enabled=True)
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.add_middleware(SlowAPIMiddleware)

    @app.post("/work")
    @limiter.limit("2/minute")
    async def work(request: Request, response: Response) -> dict[str, str]:  # noqa: ARG001
        return {"status": "ok"}

    client = TestClient(app)
    headers = {"fly-client-ip": "203.0.113.50"}
    first = client.post("/work", headers=headers)
    assert first.status_code == 200
    assert "x-ratelimit-remaining" in first.headers
    assert client.post("/work", headers=headers).status_code == 200
    assert client.post("/work", headers=headers).status_code == 429
