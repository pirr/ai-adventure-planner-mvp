# Deferred LLM Explanations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop `/api/recommendations` from blocking ~5 s on the LLM by returning ranked cards immediately and loading the AI prose through a new `/api/explanations` endpoint that the frontend shimmers into place.

**Architecture:** The recommendation pipeline already fills rule-based `description`/`why` on every card before the LLM runs; the LLM only overwrites `summary`/`why`/`data_confidence_note` when grounded. We defer that LLM step: `build_recommendations` stashes the finished recommendations in an in-process TTL map keyed by `request_id` and sets `explanations_pending=True`. A new endpoint resolves the stash by running the existing `explain_recommendations` on demand. The browser renders full cards with a shimmer over the two prose areas, then fills them once from the second call (variant "1B"), falling back to the already-shown rule-based text on any failure.

**Tech Stack:** FastAPI, pydantic, httpx, slowapi, pytest (run inside the Docker image), vanilla-JS frontend, Docker Compose.

## Global Constraints

- **Run backend tests via Docker, rebuilding first** (no source mount — stale code runs otherwise): `docker compose build app && docker compose run --rm -e PYTHONPATH=. app pytest tests`. For one file: `docker compose run --rm -e PYTHONPATH=. app pytest tests/<file>.py -v` (still rebuild `app` first when code changed).
- **No new infrastructure.** In-process state only — no Redis/DB for the stash. Infra stays ~€5/month.
- **LLM stays best-effort.** Provider errors must never 500 and must fall back to rule-based text. The grounding guard (`is_grounded`) stays in the explanation path.
- **Kill switch:** `DEFER_EXPLANATIONS` (default `true`). `false` restores today's inline behavior exactly.
- **Bilingual:** any new user-facing string needs both `en` and `ru` entries in `frontend/app.js`'s `I18N`.
- **Frontend verification** (Task F): Docker `--build`, browse via the machine's **LAN IP, not localhost**, bump the `?v=` query on `app.js` in `index.html`, and `unrouteAll` any stale Playwright stubs between runs.
- **Net LLM call volume is unchanged** (one call per recommendation request, now triggered by the second fetch).

---

## File Structure

**Create:**
- `backend/app/services/llm/ab.py` — A/B bucket + explainer-provider selection (moved out of `recommendations.py` to avoid a circular import).
- `backend/app/services/explanations.py` — in-process stash + async `resolve`.
- `backend/tests/test_explanations.py` — unit tests for the stash/resolve service and the deferral branch of `build_recommendations`.
- `backend/tests/test_explanations_endpoint.py` — TestClient tests for `POST /api/explanations`.

**Modify:**
- `backend/app/config.py` — add `defer_explanations`, `explanation_stash_ttl_seconds`, `explanation_stash_max_entries`, `rate_limit_explanations`, `llm_explain_max_tokens`.
- `backend/app/services/recommendations.py` — drop the local A/B helpers, add `defer_explanations` param, replace the inline explain call with stash + flag.
- `backend/app/schemas.py` — add `explanations_pending` to `AdventureResponse`; add `ExplanationsRequest`.
- `backend/app/main.py` — register `POST /api/explanations`.
- `backend/app/services/llm/openai_compat.py` — thread an explanation token cap into the request body; tighten the prompt (Task E, secondary).
- `frontend/app.js`, `frontend/styles.css`, `frontend/index.html` — shimmer + second fetch + fill (Task F).

---

## Task A: Move A/B helpers into `llm/ab.py`

Pure refactor so `explanations.py` can reuse the explainer selection without importing `recommendations.py` (which will import `explanations.py`).

**Files:**
- Create: `backend/app/services/llm/ab.py`
- Modify: `backend/app/services/recommendations.py:1-38, 227`
- Test: `backend/tests/test_explanations.py` (first test only)

**Interfaces:**
- Produces: `ab_bucket(anonymous_id: str) -> int`; `explainer_provider(request: AdventureRequest) -> LLMProvider`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_explanations.py`:

```python
from app.services.llm.ab import ab_bucket, explainer_provider


def test_ab_bucket_is_stable_and_binary():
    assert ab_bucket("user-1") == ab_bucket("user-1")
    assert ab_bucket("user-1") in (0, 1)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `docker compose build app && docker compose run --rm -e PYTHONPATH=. app pytest tests/test_explanations.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.llm.ab'`

- [ ] **Step 3: Create `backend/app/services/llm/ab.py`**

```python
from __future__ import annotations

import hashlib

from app.config import settings
from app.schemas import AdventureRequest
from app.services.llm.base import LLMProvider
from app.services.llm.factory import get_llm_provider
from app.services.llm.template import TemplateProvider


def ab_bucket(anonymous_id: str) -> int:
    """Stable 0/1 bucket from the anonymous id (hashlib, not the salted hash())."""
    return int(hashlib.sha1(anonymous_id.encode()).hexdigest(), 16) % 2


def explainer_provider(request: AdventureRequest) -> LLMProvider:
    """The configured LLM provider, unless the A/B control bucket is selected
    (then templates). No-op when A/B is off or no LLM/anonymous_id is present."""
    provider = get_llm_provider()
    if not settings.ab_test_enabled or isinstance(provider, TemplateProvider) or not request.anonymous_id:
        return provider
    return provider if ab_bucket(request.anonymous_id) == 1 else TemplateProvider()
```

- [ ] **Step 4: Delete the old helpers from `recommendations.py`**

Remove `import hashlib` (line 4) and the two functions `_ab_bucket` and `_explainer_provider` (lines 27-38). Add the import near the other `llm` imports:

```python
from app.services.llm.ab import explainer_provider
```

Change the `llm` import line (currently `from app.services.llm import LLMProvider, TemplateProvider, explain_recommendations, get_llm_provider`) to drop the now-unused `get_llm_provider`:

```python
from app.services.llm import LLMProvider, TemplateProvider, explain_recommendations
```

At the old line 227, change `_explainer_provider(request)` to `explainer_provider(request)`.

- [ ] **Step 5: Run the new test plus the full suite to verify nothing broke**

Run: `docker compose build app && docker compose run --rm -e PYTHONPATH=. app pytest tests -q`
Expected: PASS (existing `test_rotation.py` / `test_routing.py` still exercise `build_recommendations`).

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/llm/ab.py backend/app/services/recommendations.py backend/tests/test_explanations.py
git commit -m "refactor: extract A/B explainer selection into llm/ab.py"
```

---

## Task B: `explanations.py` stash + resolve

**Files:**
- Create: `backend/app/services/explanations.py`
- Modify: `backend/app/config.py` (add three settings)
- Test: `backend/tests/test_explanations.py`

**Interfaces:**
- Consumes: `explainer_provider` (Task A); `explain_recommendations(recommendations, request, provider)` from `app.services.llm.service`.
- Produces:
  - `stash(request_id: str, recommendations: list[Recommendation], request: AdventureRequest) -> None`
  - `async resolve(request_id: str) -> list[dict]` — each dict is `{"id": str, "summary": str | None, "why": list[str], "data_confidence_note": str | None}`. One-shot: a second call for the same id returns `[]`.

- [ ] **Step 1: Add config settings**

In `backend/app/config.py`, after the `search_candidate_cache_max_entries` line (line 59), add:

```python
    # Deferred LLM explanations: recommendations are returned immediately and the
    # prose is fetched separately. The finished recommendations are stashed in
    # process, keyed by request_id, for the follow-up /api/explanations call.
    defer_explanations: bool = _env_bool("DEFER_EXPLANATIONS", True)
    explanation_stash_ttl_seconds: int = int(os.getenv("EXPLANATION_STASH_TTL_SECONDS", "300"))
    explanation_stash_max_entries: int = int(os.getenv("EXPLANATION_STASH_MAX_ENTRIES", "512"))
```

- [ ] **Step 2: Write the failing tests**

Append to `backend/tests/test_explanations.py`:

```python
import asyncio

from app.schemas import AdventureRequest, Recommendation, ScoreBreakdown
from app.services import explanations
from app.services.llm import Explanation, ExplanationInput, LLMProvider


def _rec(**over) -> Recommendation:
    base = dict(
        id="r1", title="Old Fort", place_type="fortress", lat=42.4, lon=18.7,
        adventure_score=86,
        score_breakdown=ScoreBreakdown(
            time_fit=100, weather_fit=90, distance_fit=80, safety_fit=85,
            group_fit=80, interest_fit=88, place_quality=80, personal_preference_fit=70,
        ),
        total_minutes=120, travel_minutes=36, activity_minutes=80,
        distance_km=10.0, walking_km=2.0, difficulty="easy",
        description="A history-focused stop.", why=["Fits your time."],
        warnings=[], map_url="https://maps.example/x", source="test",
    )
    base.update(over)
    return Recommendation(**base)


class _FakeProvider(LLMProvider):
    name = "fake"

    def __init__(self, explanations_out):
        self.explanations_out = explanations_out

    async def explain(self, payload: ExplanationInput):
        return self.explanations_out


def _patch_explainer(monkeypatch, provider):
    monkeypatch.setattr("app.services.explanations.explainer_provider", lambda request: provider)


def test_resolve_returns_grounded_explanation(monkeypatch):
    explanations._pending.clear()
    provider = _FakeProvider([
        Explanation(summary="A short fortress walk that fits your plan.",
                    why=["Great views"], data_confidence_note="No live traffic data."),
    ])
    _patch_explainer(monkeypatch, provider)
    req = AdventureRequest(lat=42.4, lon=18.7, anonymous_id="u")
    explanations.stash("req-1", [_rec()], req)

    out = asyncio.run(explanations.resolve("req-1"))
    assert out == [{
        "id": "r1",
        "summary": "A short fortress walk that fits your plan.",
        "why": ["Great views"],
        "data_confidence_note": "No live traffic data.",
    }]


def test_resolve_unknown_request_returns_empty(monkeypatch):
    explanations._pending.clear()
    assert asyncio.run(explanations.resolve("nope")) == []


def test_resolve_is_one_shot(monkeypatch):
    explanations._pending.clear()
    _patch_explainer(monkeypatch, _FakeProvider([Explanation(summary="Scores 86 here.", why=["ok"])]))
    explanations.stash("req-2", [_rec()], AdventureRequest(lat=42.4, lon=18.7))
    first = asyncio.run(explanations.resolve("req-2"))
    assert first and first[0]["summary"].startswith("Scores 86")
    assert asyncio.run(explanations.resolve("req-2")) == []


def test_stash_evicts_oldest_over_max(monkeypatch):
    explanations._pending.clear()
    monkeypatch.setattr("app.services.explanations.settings.explanation_stash_max_entries", 2, raising=False)
    req = AdventureRequest(lat=42.4, lon=18.7)
    for i in range(4):
        explanations.stash(f"r{i}", [_rec()], req)
    assert len(explanations._pending) <= 2
```

- [ ] **Step 3: Run to verify failure**

Run: `docker compose build app && docker compose run --rm -e PYTHONPATH=. app pytest tests/test_explanations.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.explanations'`

- [ ] **Step 4: Create `backend/app/services/explanations.py`**

```python
from __future__ import annotations

import logging
import time
from typing import Any

from app.config import settings
from app.schemas import AdventureRequest, Recommendation
from app.services.llm.ab import explainer_provider
from app.services.llm.service import explain_recommendations

logger = logging.getLogger(__name__)

# {request_id: (expires_at, recommendations, request)}. Holds the finished
# recommendation objects so the follow-up /api/explanations call can ground the
# LLM prose against the exact facts that were returned. In-process and lossy on
# restart by design: a miss just leaves the rule-based text in place.
_pending: dict[str, tuple[float, list[Recommendation], AdventureRequest]] = {}


def stash(request_id: str, recommendations: list[Recommendation], request: AdventureRequest) -> None:
    ttl = settings.explanation_stash_ttl_seconds
    max_entries = settings.explanation_stash_max_entries
    if ttl <= 0 or max_entries <= 0 or not recommendations:
        return
    now = time.time()
    for key, (expires_at, _, _) in list(_pending.items()):
        if expires_at <= now:
            _pending.pop(key, None)
    while len(_pending) >= max_entries:
        _pending.pop(next(iter(_pending)))
    _pending[request_id] = (now + ttl, recommendations, request)


async def resolve(request_id: str) -> list[dict[str, Any]]:
    """Run the (deferred) LLM explanation step for a stashed request and return
    the final prose per card. One-shot: the entry is claimed on lookup so a
    duplicate call returns []. Never raises — explain_recommendations is
    best-effort and substitutes rule-based text on any provider failure."""
    entry = _pending.pop(request_id, None)
    if entry is None:
        return []
    expires_at, recommendations, request = entry
    if expires_at <= time.time():
        return []
    provider = explainer_provider(request)
    explained = await explain_recommendations(recommendations, request, provider)
    return [
        {
            "id": rec.id,
            "summary": rec.summary,
            "why": rec.why,
            "data_confidence_note": rec.data_confidence_note,
        }
        for rec in explained
    ]
```

- [ ] **Step 5: Run to verify pass**

Run: `docker compose build app && docker compose run --rm -e PYTHONPATH=. app pytest tests/test_explanations.py -v`
Expected: PASS (all four new tests + the Task A test).

- [ ] **Step 6: Commit**

```bash
git add backend/app/config.py backend/app/services/explanations.py backend/tests/test_explanations.py
git commit -m "feat: in-process stash + resolver for deferred explanations"
```

---

## Task C: Defer the explanation step in `build_recommendations`

**Files:**
- Modify: `backend/app/schemas.py` (`AdventureResponse`)
- Modify: `backend/app/services/recommendations.py:82-90, 227-245`
- Test: `backend/tests/test_explanations.py`

**Interfaces:**
- Consumes: `stash` (Task B).
- Produces: `build_recommendations(request, provider=None, account_id=None, defer_explanations: bool | None = None)`; `AdventureResponse.explanations_pending: bool`.

- [ ] **Step 1: Add the response field**

In `backend/app/schemas.py`, in `AdventureResponse`, after `data_warnings`:

```python
    # True when the LLM prose is being fetched separately (the client should
    # show a placeholder and call /api/explanations). False when the rule-based
    # text on each card is already final (template provider / A/B control).
    explanations_pending: bool = False
```

- [ ] **Step 2: Write the failing tests**

Append to `backend/tests/test_explanations.py`:

```python
from app.services import recommendations as recs_module
from app.services.llm import TemplateProvider
from app.services.storage import Storage

_TIVAT = dict(
    lat=42.4304, lon=18.6964, available_minutes=300, transport_mode="car",
    use_live_data=False, interests=["history", "fortresses", "viewpoints"],
)


class _RecordingProvider(LLMProvider):
    name = "recording"

    def __init__(self):
        self.called = False

    async def explain(self, payload: ExplanationInput):
        self.called = True
        return [Explanation(summary="Scores 86 here.", why=["ok"]) for _ in payload.recommendations]


def test_defer_sets_pending_and_skips_inline_llm(tmp_path, monkeypatch):
    explanations._pending.clear()
    monkeypatch.setattr("app.services.recommendations.storage", Storage(tmp_path / "a.db"))
    provider = _RecordingProvider()
    resp = asyncio.run(recs_module.build_recommendations(
        AdventureRequest(**_TIVAT, anonymous_id="u", limit=3),
        provider=provider, defer_explanations=True,
    ))
    assert resp.explanations_pending is True
    assert provider.called is False
    assert resp.request_id in explanations._pending


def test_template_provider_is_not_pending(tmp_path, monkeypatch):
    explanations._pending.clear()
    monkeypatch.setattr("app.services.recommendations.storage", Storage(tmp_path / "b.db"))
    resp = asyncio.run(recs_module.build_recommendations(
        AdventureRequest(**_TIVAT, limit=3),
        provider=TemplateProvider(), defer_explanations=True,
    ))
    assert resp.explanations_pending is False


def test_defer_false_runs_inline(tmp_path, monkeypatch):
    explanations._pending.clear()
    monkeypatch.setattr("app.services.recommendations.storage", Storage(tmp_path / "c.db"))
    provider = _RecordingProvider()
    resp = asyncio.run(recs_module.build_recommendations(
        AdventureRequest(**_TIVAT, anonymous_id="u", limit=3),
        provider=provider, defer_explanations=False,
    ))
    assert resp.explanations_pending is False
    assert provider.called is True
```

- [ ] **Step 3: Run to verify failure**

Run: `docker compose build app && docker compose run --rm -e PYTHONPATH=. app pytest tests/test_explanations.py -k "defer or template_provider" -v`
Expected: FAIL — `TypeError: build_recommendations() got an unexpected keyword argument 'defer_explanations'`

- [ ] **Step 4: Implement the deferral**

In `backend/app/services/recommendations.py`, add the import near the other service imports:

```python
from app.services.explanations import stash as stash_explanations
```

Change the signature (line ~82):

```python
async def build_recommendations(
    request: AdventureRequest,
    provider: LLMProvider | None = None,
    account_id: int | None = None,
    defer_explanations: bool | None = None,
) -> AdventureResponse:
```

Replace the inline explain block (current lines ~227-235):

```python
    explainer = provider if provider is not None else _explainer_provider(request)
    stage_started = time.perf_counter()
    recommendations = await explain_recommendations(recommendations, request, explainer)
    logger.info(
        "recommendations_timing request_id=%s stage=explain recommendations=%d duration_ms=%d",
        request_id,
        len(recommendations),
        _elapsed_ms(stage_started),
    )
```

with:

```python
    explainer = provider if provider is not None else explainer_provider(request)
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
```

In the `AdventureResponse(...)` constructor (line ~238), add the field:

```python
        data_warnings=weather_warnings + place_warnings + google_warnings,
        explanations_pending=explanations_pending,
```

- [ ] **Step 5: Run the targeted tests, then the full suite**

Run: `docker compose build app && docker compose run --rm -e PYTHONPATH=. app pytest tests -q`
Expected: PASS. (Existing `test_rotation.py`/`test_routing.py` use the template/default provider, so `explanations_pending` stays `False` and their assertions are unaffected.)

- [ ] **Step 6: Commit**

```bash
git add backend/app/schemas.py backend/app/services/recommendations.py backend/tests/test_explanations.py
git commit -m "feat: defer LLM explanations behind DEFER_EXPLANATIONS flag"
```

---

## Task D: `POST /api/explanations` endpoint

**Files:**
- Modify: `backend/app/config.py` (add `rate_limit_explanations`)
- Modify: `backend/app/schemas.py` (add `ExplanationsRequest`)
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_explanations_endpoint.py`

**Interfaces:**
- Consumes: `resolve` (Task B).
- Produces: `POST /api/explanations` accepting `{request_id}` → `{"request_id": str, "explanations": [...]}`.

- [ ] **Step 1: Add config + schema**

In `backend/app/config.py`, after `rate_limit_parse` (line 87):

```python
    # Follow-up call that loads deferred LLM explanations; at most one per
    # recommendations call, so allow roughly 2x the recommendations budget.
    rate_limit_explanations: str = os.getenv("RATE_LIMIT_EXPLANATIONS", "20/minute;200/day")
```

In `backend/app/schemas.py`, near `ParseTextRequest`:

```python
class ExplanationsRequest(BaseModel):
    request_id: str = Field(..., max_length=64)
```

- [ ] **Step 2: Write the failing test**

Create `backend/tests/test_explanations_endpoint.py`:

```python
from fastapi.testclient import TestClient

from app.main import app
from app.schemas import AdventureRequest, Recommendation, ScoreBreakdown
from app.services import explanations
from app.services.llm import Explanation, ExplanationInput, LLMProvider

client = TestClient(app)


def _rec(**over) -> Recommendation:
    base = dict(
        id="r1", title="Old Fort", place_type="fortress", lat=42.4, lon=18.7,
        adventure_score=86,
        score_breakdown=ScoreBreakdown(
            time_fit=100, weather_fit=90, distance_fit=80, safety_fit=85,
            group_fit=80, interest_fit=88, place_quality=80, personal_preference_fit=70,
        ),
        total_minutes=120, travel_minutes=36, activity_minutes=80,
        distance_km=10.0, walking_km=2.0, difficulty="easy",
        description="A history-focused stop.", why=["Fits your time."],
        warnings=[], map_url="https://maps.example/x", source="test",
    )
    base.update(over)
    return Recommendation(**base)


class _FakeProvider(LLMProvider):
    name = "fake"

    async def explain(self, payload: ExplanationInput):
        return [Explanation(summary="A short fortress walk that fits.", why=["Great views"])]


def test_explanations_endpoint_returns_grounded_prose(monkeypatch):
    explanations._pending.clear()
    monkeypatch.setattr("app.services.explanations.explainer_provider", lambda request: _FakeProvider())
    explanations.stash("req-1", [_rec()], AdventureRequest(lat=42.4, lon=18.7))

    resp = client.post("/api/explanations", json={"request_id": "req-1"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["request_id"] == "req-1"
    assert body["explanations"][0]["summary"] == "A short fortress walk that fits."
    assert body["explanations"][0]["why"] == ["Great views"]


def test_explanations_endpoint_unknown_id_is_empty():
    explanations._pending.clear()
    resp = client.post("/api/explanations", json={"request_id": "missing"})
    assert resp.status_code == 200
    assert resp.json() == {"request_id": "missing", "explanations": []}
```

- [ ] **Step 3: Run to verify failure**

Run: `docker compose build app && docker compose run --rm -e PYTHONPATH=. app pytest tests/test_explanations_endpoint.py -v`
Expected: FAIL — `404 Not Found` (route not registered).

- [ ] **Step 4: Register the endpoint**

In `backend/app/main.py`, extend the schema import block to include `ExplanationsRequest`, and add the service import near the other service imports:

```python
from app.services import explanations as explanations_service
```

Add the route after the `recommendations` endpoint (after line 265):

```python
@app.post("/api/explanations")
@limiter.limit(settings.rate_limit_explanations)
async def explanations(request: Request, payload: ExplanationsRequest) -> dict[str, Any]:
    session = _session(request)
    if session is not None:
        try:
            auth.require_csrf(request, session)
        except auth.AuthError as exc:
            _raise_auth_error(exc)
    items = await explanations_service.resolve(payload.request_id)
    return {"request_id": payload.request_id, "explanations": items}
```

- [ ] **Step 5: Run to verify pass**

Run: `docker compose build app && docker compose run --rm -e PYTHONPATH=. app pytest tests/test_explanations_endpoint.py -v`
Expected: PASS (both tests).

- [ ] **Step 6: Commit**

```bash
git add backend/app/config.py backend/app/schemas.py backend/app/main.py backend/tests/test_explanations_endpoint.py
git commit -m "feat: add POST /api/explanations endpoint"
```

---

## Task E: LLM trims (secondary — bounds tokens and settling time)

Off the critical path now, so this is polish: cap explanation output tokens and shorten the prose. (Disabling Gemini reasoning is a deploy step, not code — see Step 5.)

**Files:**
- Modify: `backend/app/config.py` (add `llm_explain_max_tokens`)
- Modify: `backend/app/services/llm/factory.py`
- Modify: `backend/app/services/llm/openai_compat.py`
- Test: `backend/tests/test_openai_compat.py`

**Interfaces:**
- Produces: `OpenAICompatibleProvider(..., explain_max_tokens: int | None = None)`; explanation request body carries `max_tokens` when set; `parse_situation` body unchanged.

- [ ] **Step 1: Add config**

In `backend/app/config.py`, after `llm_max_explained` (line 117):

```python
    # Cap explanation generation length (output tokens dominate LLM latency/cost).
    llm_explain_max_tokens: int = int(os.getenv("LLM_EXPLAIN_MAX_TOKENS", "700"))
```

- [ ] **Step 2: Write the failing test**

Append to `backend/tests/test_openai_compat.py`:

```python
from app.services.llm.openai_compat import OpenAICompatibleProvider


def test_explain_body_includes_max_tokens():
    provider = OpenAICompatibleProvider(
        base_url="https://api.openai.com/v1", model="gpt-4o-mini", explain_max_tokens=500,
    )
    body = provider._body([{"role": "user", "content": "x"}], "gpt-4o-mini", max_tokens=500)
    assert body["max_tokens"] == 500


def test_body_without_max_tokens_omits_it():
    provider = OpenAICompatibleProvider(base_url="https://api.openai.com/v1", model="gpt-4o-mini")
    body = provider._body([{"role": "user", "content": "x"}], "gpt-4o-mini")
    assert "max_tokens" not in body
```

- [ ] **Step 3: Run to verify failure**

Run: `docker compose build app && docker compose run --rm -e PYTHONPATH=. app pytest tests/test_openai_compat.py -k max_tokens -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'explain_max_tokens'`.

- [ ] **Step 4: Thread the token cap**

In `backend/app/services/llm/openai_compat.py`:

In `__init__`, add the parameter (after `gemini_reasoning_effort`):

```python
        gemini_reasoning_effort: str | None = None,
        explain_max_tokens: int | None = None,
    ):
```

and store it:

```python
        self.explain_max_tokens = explain_max_tokens
```

Change `_body` to accept and apply `max_tokens`:

```python
    def _body(self, messages: list[dict[str, str]], model: str, max_tokens: int | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": 0.2,
        }
        if max_tokens:
            body["max_tokens"] = max_tokens
```

(keep the rest of `_body` unchanged.)

Change `_call_model` to accept `max_tokens` and pass it to `_body`:

```python
    async def _call_model(
        self,
        *,
        client: httpx.AsyncClient,
        model: str,
        messages: list[dict[str, str]],
        max_tokens: int | None = None,
    ) -> str:
        url = f"{self.base_url}/chat/completions"
        body = self._body(messages, model, max_tokens=max_tokens)
```

In `explain`, pass the cap (inside the `for index, model in enumerate(models)` loop, the `_call_model` call):

```python
                    content = await self._call_model(
                        client=client,
                        model=model,
                        messages=messages,
                        max_tokens=self.explain_max_tokens,
                    )
```

(Leave the `parse_situation` `_call_model` call as-is — no `max_tokens`.)

In `backend/app/services/llm/factory.py`, pass the setting into the provider:

```python
    return OpenAICompatibleProvider(
        base_url=base_url,
        model=settings.llm_model,
        api_key=settings.llm_api_key,
        timeout=settings.llm_timeout_seconds,
        fallback_models=settings.llm_fallback_models,
        gemini_reasoning_effort=settings.gemini_reasoning_effort,
        explain_max_tokens=settings.llm_explain_max_tokens,
    )
```

- [ ] **Step 5: Shorten the prompt + document the reasoning-off deploy step**

In `openai_compat.py`, in `_SYSTEM_PROMPT`, change `"max 30 words"` to `"max 24 words"` and `"<2-4 short strings>"` to `"<2-3 short strings>"`. In `parse_explanations`, change `why_raw[:4]` to `why_raw[:3]`.

Deploy note (no code): set `GEMINI_REASONING_EFFORT=` (empty) in the Fly secrets/env so reasoning is off for explanations. The default in `config.py` is already empty; only the deployment currently overrides it to `low`.

- [ ] **Step 6: Run to verify pass, then commit**

Run: `docker compose build app && docker compose run --rm -e PYTHONPATH=. app pytest tests/test_openai_compat.py -v`
Expected: PASS.

```bash
git add backend/app/config.py backend/app/services/llm/factory.py backend/app/services/llm/openai_compat.py backend/tests/test_openai_compat.py
git commit -m "perf: cap explanation tokens and tighten the prompt"
```

---

## Task F: Frontend shimmer + second fetch + fill (variant 1B)

Cards render immediately with rule-based text; the two prose areas shimmer; the second fetch fills them once; any failure reveals the rule-based text. Verified with Playwright (no unit tests).

**Files:**
- Modify: `frontend/app.js`
- Modify: `frontend/styles.css`
- Modify: `frontend/index.html` (cache-bust)

- [ ] **Step 1: Add the shimmer CSS**

In `frontend/styles.css`, near the `.card-loading` rules (~line 443), add:

```css
@keyframes shimmer { 0% { background-position: 200% 0; } 100% { background-position: -200% 0; } }
.is-pending-explanation { position: relative; min-height: 1.1em; color: transparent; }
.is-pending-explanation > * { visibility: hidden; }
.is-pending-explanation::after {
  content: ""; position: absolute; inset: 0; border-radius: 8px;
  background: linear-gradient(90deg, rgba(0,0,0,0.05) 25%, rgba(0,0,0,0.10) 50%, rgba(0,0,0,0.05) 75%);
  background-size: 200% 100%; animation: shimmer 1.3s ease-in-out infinite;
}
@media (prefers-reduced-motion: reduce) {
  .is-pending-explanation::after { animation: none; background: rgba(0,0,0,0.06); }
}
```

- [ ] **Step 2: Add the i18n label (both languages)**

In `frontend/app.js`, in `I18N.en` (near `why_now_title`, line ~452) add:

```javascript
    explanation_loading: 'Writing why this fits…',
```

and in `I18N.ru` (near line ~656):

```javascript
    explanation_loading: 'Пишем, почему подходит…',
```

- [ ] **Step 3: Mark pending items and extract the `why` markup**

In `frontend/app.js`, extend `annotateRequestId` (line ~1125) to flag pending items:

```javascript
function annotateRequestId(data) {
  (data.recommendations || []).forEach((item) => {
    if (!item._request_id) item._request_id = data.request_id;
    if (data.explanations_pending) item._explanations_pending = true;
  });
}
```

Add a shared helper (place it just above `buildCard`, line ~1508):

```javascript
function whyInnerHtml(item, isTop) {
  return `
    <h3>${isTop ? t('why_now_title') : t('why_title')}</h3>
    ${(item.why || []).map((text) => `<div class="item good">✓ ${escapeHtml(text)}</div>`).join('')}
    ${item.data_confidence_note ? `<div class="item">${escapeHtml(item.data_confidence_note)}</div>` : ''}
  `;
}
```

In `buildCard`, replace the inline `.why` assignment (lines ~1572-1576) with:

```javascript
  fragment.querySelector('.why').innerHTML = whyInnerHtml(item, isTop);
```

Then, just before the `details` lookup (line ~1581), add the shimmer-on-pending block:

```javascript
  if (item._explanations_pending) {
    ['.description', '.why'].forEach((sel) => {
      const node = fragment.querySelector(sel);
      if (node) { node.classList.add('is-pending-explanation'); node.setAttribute('aria-busy', 'true'); node.setAttribute('aria-label', t('explanation_loading')); }
    });
  }
```

- [ ] **Step 4: Add the fetch-and-fill functions**

In `frontend/app.js`, add near `postRecommendations` (line ~1112):

```javascript
function applyExplanationToCard(item) {
  const card = carouselEl.querySelector(`.recommendation[data-id="${CSS.escape(item.id)}"]`);
  if (!card) return;
  const isTop = card.classList.contains('is-top');
  const desc = card.querySelector('.description');
  if (desc) {
    desc.textContent = item.summary || item.description;
    desc.classList.remove('is-pending-explanation');
    desc.removeAttribute('aria-busy');
  }
  const why = card.querySelector('.why');
  if (why) {
    why.innerHTML = whyInnerHtml(item, isTop);
    why.classList.remove('is-pending-explanation');
    why.removeAttribute('aria-busy');
  }
}

function clearPendingShimmer(requestId) {
  (lastResponse && lastResponse.recommendations || []).forEach((item) => {
    if ((item._request_id || lastRequestId) === requestId) item._explanations_pending = false;
  });
  carouselEl.querySelectorAll('.is-pending-explanation').forEach((el) => {
    el.classList.remove('is-pending-explanation');
    el.removeAttribute('aria-busy');
  });
}

async function requestExplanations(requestId) {
  if (!requestId) return;
  const generation = searchGeneration;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 15000);
  try {
    const response = await apiFetch('/api/explanations', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ request_id: requestId }),
      cache: 'no-store',
      signal: controller.signal,
    });
    if (!response.ok) throw new Error('explanations_failed');
    const data = await response.json();
    if (generation !== searchGeneration) return;
    const byId = new Map((data.explanations || []).map((e) => [e.id, e]));
    (lastResponse && lastResponse.recommendations || []).forEach((item) => {
      const e = byId.get(item.id);
      if (!e) return;
      if (e.summary != null) item.summary = e.summary;
      if (e.why) item.why = e.why;
      if (e.data_confidence_note != null) item.data_confidence_note = e.data_confidence_note;
      item._explanations_pending = false;
      applyExplanationToCard(item);
    });
  } catch (error) {
    // Best-effort: leave the rule-based text already on the cards.
  } finally {
    clearTimeout(timer);
    if (generation === searchGeneration) clearPendingShimmer(requestId);
  }
}
```

- [ ] **Step 5: Trigger the fetch after a search and after load-more**

In the search success path (line ~1304, right after `renderResults(data);`):

```javascript
    renderResults(data);
    if (data.explanations_pending) requestExplanations(data.request_id);
```

In `loadMoreRecommendations` (line ~1249, right after `const added = appendRecommendations(data);`):

```javascript
    const added = appendRecommendations(data);
    if (data.explanations_pending) requestExplanations(data.request_id);
```

- [ ] **Step 6: Cache-bust the script**

In `frontend/index.html`, find the `app.js?v=...` reference and increment the version number (e.g. `?v=37` → `?v=38`) so browsers reload the changed JS.

- [ ] **Step 7: Verify in a real browser (Playwright)**

Run: `docker compose up --build -d`
Then, against the machine's **LAN IP** (e.g. `http://192.168.1.23:8000`), with an LLM provider configured (`LLM_PROVIDER=gemini`, key set):

1. Run a search. Confirm cards appear in ~0.5 s with a shimmer over the summary line and the "Why now" list; the rest of the card (score, badges, weather, map button) is fully present.
2. Within ~5 s the two areas fill with prose and the shimmer disappears.
3. With `DEFER_EXPLANATIONS=false` (or `LLM_PROVIDER=template`), confirm **no** shimmer and **no** `/api/explanations` request (check the network panel).
4. Stub `/api/explanations` to fail (route returns 500); `unrouteAll` any prior stub first. Confirm the shimmer clears within ~15 s and the rule-based text remains readable.
5. Scroll to trigger load-more; confirm the appended cards shimmer then fill, while the first batch stays filled (no re-shimmer).

- [ ] **Step 8: Commit**

```bash
git add frontend/app.js frontend/styles.css frontend/index.html
git commit -m "feat: shimmer-and-fill deferred explanations in the UI"
```

---

## Task G: Parallelize the independent weather + places calls (optional, minor)

Saves ~60–90 ms by running the origin-weather and Overpass calls concurrently (they are independent). Marginal next to the LLM win; include only if you want the freebie.

**Files:**
- Modify: `backend/app/services/recommendations.py:95-114`

- [ ] **Step 1: Replace the two sequential awaits with a gather**

In `build_recommendations`, replace the `weather` stage and `places` stage blocks (lines ~95-114) with:

```python
    stage_started = time.perf_counter()
    (weather, weather_warnings), (places, place_warnings) = await asyncio.gather(
        get_weather(request.lat, request.lon, request.use_live_data, request.lang),
        get_candidate_places(
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
```

(`asyncio` is already imported at the top of the file.)

- [ ] **Step 2: Run the full suite**

Run: `docker compose build app && docker compose run --rm -e PYTHONPATH=. app pytest tests -q`
Expected: PASS (offline tests use sample data; ordering of the two results is fixed by `gather`).

- [ ] **Step 3: Commit**

```bash
git add backend/app/services/recommendations.py
git commit -m "perf: run origin weather and place search concurrently"
```

---

## Self-Review

**Spec coverage:**
- Decouple / `explanations_pending` / stash / `/api/explanations` → Tasks B, C, D. ✓
- Circular-import avoidance (`llm/ab.py`) → Task A. ✓
- Variant 1B shimmer-then-fill, fallback to rule-based, staleness guard, load-more → Task F. ✓
- LLM trims (token cap, prompt, reasoning-off deploy note) → Task E. ✓
- Parallelize weather+places → Task G. ✓
- Kill switch `DEFER_EXPLANATIONS`, config knobs, rate limit → Tasks B, C, D. ✓
- Non-goal (cold-start Overpass) → intentionally not in this plan. ✓
- Testing (deferral, pending flag, endpoint, unknown id, TTL/eviction, A/B determinism via `explainer_provider`, trims, back-compat inline) → Tasks A–E. ✓

**Placeholder scan:** No TBD/TODO; every code step shows full code and exact insertion points. ✓

**Type consistency:** `stash`/`resolve` signatures match between Tasks B, C, D; `explainer_provider` name consistent A→B; `explanations_pending` field name consistent C→D→F; `whyInnerHtml`/`applyExplanationToCard`/`requestExplanations`/`clearPendingShimmer` defined once and reused. ✓
