from __future__ import annotations

import dataclasses
from datetime import datetime
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

from app.config import settings as real_settings
from app import main
from app.schemas import AdventureResponse, Recommendation, ScoreBreakdown, WeatherSummary
from app.services import auth, recommendations
from app.services.storage import Storage


def _settings(**overrides):
    return dataclasses.replace(real_settings, **overrides)


def _patch_store(tmp_path, monkeypatch) -> Storage:
    store = Storage(tmp_path / "auth.db")
    monkeypatch.setattr(main, "storage", store)
    monkeypatch.setattr(auth, "storage", store)
    monkeypatch.setattr(recommendations, "storage", store)
    return store


def _register(client: TestClient, email: str = "User@Example.com") -> dict:
    response = client.post(
        "/api/auth/register",
        json={"email": email, "password": "correct horse", "anonymous_id": "anon-1", "lang": "en"},
    )
    assert response.status_code == 200
    assert "httponly" in response.headers["set-cookie"].lower()
    return response.json()


def _recommendation_payload(**overrides) -> dict:
    payload = {
        "lat": 42.4304,
        "lon": 18.6964,
        "available_minutes": 300,
        "transport_mode": "car",
        "use_live_data": False,
        "interests": ["history"],
        "anonymous_id": "anon-1",
        "limit": 1,
    }
    payload.update(overrides)
    return payload


async def _fake_recommendations(payload, account_id=None):  # noqa: ARG001
    return AdventureResponse(
        request_id="req-more",
        generated_at=datetime.utcnow(),
        weather=WeatherSummary(source="test", summary="Clear", score=80),
        recommendations=[
            Recommendation(
                id="sample-1",
                source_id="sample:1",
                title="Sample place",
                place_type="viewpoint",
                lat=payload.lat,
                lon=payload.lon,
                adventure_score=80,
                score_breakdown=ScoreBreakdown(
                    time_fit=80,
                    weather_fit=80,
                    distance_fit=80,
                    safety_fit=80,
                    effort_fit=80,
                    group_fit=80,
                    interest_fit=80,
                    place_quality=80,
                ),
                total_minutes=90,
                travel_minutes=30,
                activity_minutes=60,
                distance_km=4.0,
                walking_km=1.0,
                difficulty="easy",
                description="A test recommendation.",
                why=["Fits the test."],
                warnings=[],
                map_url="https://maps.example.test",
                source="test",
            )
        ],
        rejected_alternatives=[],
    )


def test_auth_routes_register_me_and_logout(tmp_path, monkeypatch):
    _patch_store(tmp_path, monkeypatch)
    client = TestClient(main.app)

    assert client.get("/api/history").status_code == 401
    payload = _register(client)
    assert payload["user"]["email"].lower() == "user@example.com"
    assert payload["csrf_token"]

    me = client.get("/api/auth/me").json()
    assert me["user"]["email"].lower() == "user@example.com"
    assert me["csrf_token"] == payload["csrf_token"]
    assert client.get("/api/history").status_code == 200

    blocked = client.post("/api/auth/logout")
    assert blocked.status_code == 403
    logged_out = client.post("/api/auth/logout", headers={"X-CSRF-Token": payload["csrf_token"]})
    assert logged_out.status_code == 200
    assert logged_out.json()["user"] is None
    assert client.get("/api/history").status_code == 401


def test_login_rejects_bad_password_and_accepts_existing_user(tmp_path, monkeypatch):
    _patch_store(tmp_path, monkeypatch)
    client = TestClient(main.app)
    _register(client)
    client.post("/api/auth/logout", headers={"X-CSRF-Token": client.get("/api/auth/me").json()["csrf_token"]})

    bad = client.post("/api/auth/login", json={"email": "user@example.com", "password": "wrong"})
    assert bad.status_code == 401

    good = client.post("/api/auth/login", json={"email": "user@example.com", "password": "correct horse"})
    assert good.status_code == 200
    assert good.json()["user"]["email"].lower() == "user@example.com"


def test_visited_routes_require_auth_and_csrf(tmp_path, monkeypatch):
    _patch_store(tmp_path, monkeypatch)
    client = TestClient(main.app)
    assert client.post("/api/visited", json={"anonymous_id": "anon-1", "source_id": "p1"}).status_code == 401

    auth_payload = _register(client)
    csrf = auth_payload["csrf_token"]
    no_csrf = client.post("/api/visited", json={"anonymous_id": "anon-1", "source_id": "p1"})
    assert no_csrf.status_code == 403
    ok = client.post(
        "/api/visited",
        json={"anonymous_id": "anon-1", "source_id": "p1"},
        headers={"X-CSRF-Token": csrf},
    )
    assert ok.status_code == 200
    assert client.delete("/api/visited", headers={"X-CSRF-Token": csrf}).json()["cleared"] == 1


def test_features_reports_more_recommendations_auth_gate(monkeypatch):
    monkeypatch.setattr(main, "settings", _settings(require_auth_for_more_recommendations=True))
    client = TestClient(main.app)

    response = client.get("/api/features")

    assert response.status_code == 200
    assert response.json()["require_auth_for_more_recommendations"] is True


def test_more_recommendations_gate_only_blocks_anonymous_rotation(tmp_path, monkeypatch):
    _patch_store(tmp_path, monkeypatch)
    monkeypatch.setattr(main, "settings", _settings(require_auth_for_more_recommendations=True))
    monkeypatch.setattr(main, "build_recommendations", _fake_recommendations)
    client = TestClient(main.app)

    first = client.post("/api/recommendations", json=_recommendation_payload(exclude_seen=False))
    blocked = client.post("/api/recommendations", json=_recommendation_payload(exclude_seen=True))

    assert first.status_code == 200
    assert blocked.status_code == 401
    assert blocked.json()["detail"] == "auth_required_for_more_recommendations"


def test_more_recommendations_gate_allows_authenticated_rotation(tmp_path, monkeypatch):
    _patch_store(tmp_path, monkeypatch)
    monkeypatch.setattr(main, "settings", _settings(require_auth_for_more_recommendations=True))
    monkeypatch.setattr(main, "build_recommendations", _fake_recommendations)
    client = TestClient(main.app)
    csrf = _register(client)["csrf_token"]

    response = client.post(
        "/api/recommendations",
        json=_recommendation_payload(exclude_seen=True),
        headers={"X-CSRF-Token": csrf},
    )

    assert response.status_code == 200
    assert response.json()["recommendations"][0]["source_id"] == "sample:1"


def test_clear_history_preserves_account_visited_marks(tmp_path, monkeypatch):
    store = _patch_store(tmp_path, monkeypatch)
    client = TestClient(main.app)
    csrf = _register(client)["csrf_token"]
    account_id = client.get("/api/auth/me").json()["user"]["id"]

    with store._connect() as conn:
        conn.execute(
            "INSERT INTO search_sessions (id, created_at, anonymous_id, account_id, lat, lon, request_json, response_json) "
            "VALUES ('s1', '2026-01-01T00:00:00', 'anon-1', ?, 1, 2, '{}', '{}')",
            (account_id,),
        )
        conn.execute(
            "INSERT INTO account_place_marks (account_id, source_id, seen, visited, updated_at) "
            "VALUES (?, 'p1', 1, 1, '2026-01-01T00:00:00')",
            (account_id,),
        )

    cleared = client.delete("/api/history", headers={"X-CSRF-Token": csrf})
    assert cleared.status_code == 200
    assert store.place_marks.account_place_marks(account_id) == {"seen": set(), "visited": {"p1"}, "want_to_visit": set()}


def test_want_to_visit_endpoints_toggle_and_list(tmp_path, monkeypatch):
    store = _patch_store(tmp_path, monkeypatch)
    client = TestClient(main.app)
    csrf = _register(client)["csrf_token"]
    account_id = client.get("/api/auth/me").json()["user"]["id"]

    for sid in ("osm:1", "osm:2"):
        r = client.post("/api/want-to-visit", json={"source_id": sid, "wanted": True}, headers={"X-CSRF-Token": csrf})
        assert r.status_code == 200 and r.json()["wanted"] is True
    assert store.place_marks.account_place_marks(account_id)["want_to_visit"] == {"osm:1", "osm:2"}

    listed = client.get("/api/want-to-visit").json()["items"]
    assert {item["source_id"] for item in listed} == {"osm:1", "osm:2"}

    # Toggle one off, then clear the rest.
    off = client.post("/api/want-to-visit", json={"source_id": "osm:1", "wanted": False}, headers={"X-CSRF-Token": csrf})
    assert off.status_code == 200 and off.json()["wanted"] is False
    assert store.place_marks.account_place_marks(account_id)["want_to_visit"] == {"osm:2"}

    cleared = client.delete("/api/want-to-visit", headers={"X-CSRF-Token": csrf})
    assert cleared.status_code == 200 and cleared.json()["cleared"] == 1
    assert store.place_marks.account_place_marks(account_id)["want_to_visit"] == set()


def test_want_to_visit_requires_auth(tmp_path, monkeypatch):
    _patch_store(tmp_path, monkeypatch)
    client = TestClient(main.app)
    resp = client.post("/api/want-to-visit", json={"source_id": "osm:1", "wanted": True})
    assert resp.status_code >= 400  # no session -> rejected


def test_merge_anonymous_data_into_account(tmp_path):
    store = Storage(tmp_path / "merge.db")
    with store._connect() as conn:
        conn.execute(
            "INSERT INTO search_sessions (id, created_at, anonymous_id, lat, lon, request_json, response_json) "
            "VALUES ('s1', '2026-01-01T00:00:00', 'anon-1', 1, 2, '{}', '{}')"
        )
        conn.execute(
            "INSERT INTO feedback (created_at, request_id, recommendation_id, rating, anonymous_id) "
            "VALUES ('2026-01-01T00:00:00', 's1', 'r1', 'up', 'anon-1')"
        )
        conn.execute(
            "INSERT INTO events (created_at, event, request_id, anonymous_id) "
            "VALUES ('2026-01-01T00:00:00', 'search_started', 's1', 'anon-1')"
        )
        conn.execute(
            "INSERT INTO place_marks (anonymous_id, source_id, seen, visited, updated_at) "
            "VALUES ('anon-1', 'p1', 1, 1, '2026-01-01T00:00:00')"
        )
    account = store.accounts.create_email_account("person@example.com", "hash")
    store.lifecycle.merge_anonymous_into_account("anon-1", account["id"])

    with store._connect() as conn:
        assert conn.execute("SELECT account_id FROM search_sessions WHERE id='s1'").fetchone()["account_id"] == account["id"]
        assert conn.execute("SELECT account_id FROM feedback WHERE request_id='s1'").fetchone()["account_id"] == account["id"]
        assert conn.execute("SELECT account_id FROM events WHERE request_id='s1'").fetchone()["account_id"] == account["id"]
    marks = store.place_marks.account_place_marks(account["id"])
    assert marks == {"seen": {"p1"}, "visited": {"p1"}, "want_to_visit": set()}


def test_google_oauth_callback_creates_session(tmp_path, monkeypatch):
    _patch_store(tmp_path, monkeypatch)
    cfg = _settings(
        google_oauth_client_id="cid",
        google_oauth_client_secret="secret",
        google_oauth_redirect_uri="http://testserver/api/auth/google/callback",
    )
    monkeypatch.setattr(auth, "settings", cfg)

    async def fake_exchange(code: str):
        assert code == "abc"
        return {"id_token": "raw-id-token"}

    def fake_verify(raw: str):
        assert raw == "raw-id-token"
        return {"sub": "google-sub-1", "email": "person@gmail.com", "email_verified": True}

    monkeypatch.setattr(auth, "exchange_google_code", fake_exchange)
    monkeypatch.setattr(auth, "verify_google_id_token", fake_verify)
    client = TestClient(main.app)

    start = client.get("/api/auth/google/start?anonymous_id=anon-1", follow_redirects=False)
    assert start.status_code == 307
    assert auth.oauth_state_cookie_name() in start.headers["set-cookie"]
    query = parse_qs(urlparse(start.headers["location"]).query)
    state = query["state"][0]

    callback = client.get(f"/api/auth/google/callback?code=abc&state={state}", follow_redirects=False)
    assert callback.status_code == 303
    me = client.get("/api/auth/me").json()
    assert me["user"]["email"] == "person@gmail.com"
    assert me["user"]["provider"] == "google"
    assert me["user"]["email_verified"] is True


def test_google_oauth_callback_requires_browser_state_cookie(tmp_path, monkeypatch):
    _patch_store(tmp_path, monkeypatch)
    cfg = _settings(
        google_oauth_client_id="cid",
        google_oauth_client_secret="secret",
        google_oauth_redirect_uri="http://testserver/api/auth/google/callback",
    )
    monkeypatch.setattr(auth, "settings", cfg)
    client = TestClient(main.app)

    start = client.get("/api/auth/google/start", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    callback = TestClient(main.app).get(f"/api/auth/google/callback?code=abc&state={state}", follow_redirects=False)

    assert callback.status_code == 400
    assert callback.json()["detail"] == "invalid_oauth_state"


def test_google_oauth_rejects_reused_state(tmp_path, monkeypatch):
    _patch_store(tmp_path, monkeypatch)
    cfg = _settings(
        google_oauth_client_id="cid",
        google_oauth_client_secret="secret",
        google_oauth_redirect_uri="http://testserver/api/auth/google/callback",
    )
    monkeypatch.setattr(auth, "settings", cfg)

    async def fake_exchange(code: str):
        return {"id_token": "raw-id-token"}

    monkeypatch.setattr(auth, "exchange_google_code", fake_exchange)
    monkeypatch.setattr(auth, "verify_google_id_token", lambda raw: {"sub": "s", "email": "p@gmail.com", "email_verified": True})
    client = TestClient(main.app)
    state = parse_qs(urlparse(client.get("/api/auth/google/start", follow_redirects=False).headers["location"]).query)["state"][0]

    assert client.get(f"/api/auth/google/callback?code=abc&state={state}", follow_redirects=False).status_code == 303
    assert client.get(f"/api/auth/google/callback?code=abc&state={state}", follow_redirects=False).status_code == 400
