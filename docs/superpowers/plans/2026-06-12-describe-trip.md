# "Describe your trip" (MVP 0.4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Free-text + voice situation input: a launcher field parses "I have 3 hours, with my dog" into the structured search via the existing LLM provider and auto-searches like a vibe preset.

**Architecture:** New `POST /api/parse-request` endpoint asks the configured LLM provider (new `parse_situation()` method) to map text onto a `ParsedSituation` schema (pydantic constraints = grounding guard). The frontend launcher gets a describe field (+ Web Speech API mic) that merges parsed fields over `smartNowPreset()` defaults and reuses the preset path (`applyPreset` → `commitSearch`). Daily budgets reuse the MVP 0.3 `api_usage` limiter, generalized to `reserve_api_calls(api, …)`. Feature is hidden whenever the provider is the `TemplateProvider` (reported by new `GET /api/features`).

**Tech Stack:** FastAPI + pydantic v1-style validators, sqlite (`storage.py`), OpenAI-compatible chat completions (JSON mode), vanilla JS frontend (`mood.js` launcher, `app.js` payload), Web Speech API.

**Spec:** `docs/MVP_0.4_SCOPE.md`. Read it before starting.

**Conventions for every task:**
- Run tests via docker compose, never a local venv:
  - Full suite: `docker compose run --rm --no-deps app python -m pytest -q`
  - Targeted: `docker compose run --rm --no-deps app python -m pytest -q -k parse`
- Frontend has no unit-test runner; frontend tasks are verified with Playwright against `docker compose up --build` using the LAN IP (not localhost) and a bumped `?v=` cache-bust query.
- Commit after each task (messages given per task).

---

### Task 0: Branch setup

The working tree may contain an uncommitted bugfix (collapsed-cards sync in `frontend/app.js` + `frontend/index.html`). It must not mix into this feature.

- [ ] **Step 1: Commit the pending bugfix (if still uncommitted)**

```bash
git status --short   # expect: M frontend/app.js, M frontend/index.html
git add frontend/app.js frontend/index.html
git commit -m "Fix collapsed card strip not following the selected place"
```

If `git status` shows a clean tree, skip the commit.

- [ ] **Step 2: Create the feature branch**

```bash
git checkout -b feature/describe-trip
```

---

### Task 1: Config settings

**Files:**
- Modify: `backend/app/config.py` (after the existing `llm_*` block, ~line 49)
- Modify: `.env.example`

- [ ] **Step 1: Add settings**

In `backend/app/config.py`, directly under `llm_fallback_models`:

```python
    # Free-text situation parsing ("Describe your trip"). Requires a real LLM
    # provider; with the TemplateProvider the feature reports disabled
    # regardless of this flag.
    llm_parse_enabled: bool = os.getenv("LLM_PARSE_ENABLED", "true").lower() == "true"
    # App-side daily budgets for parse calls (same pattern as Google
    # enrichment). 0 disables the feature. Global is the real backstop:
    # anonymous_id is client-supplied.
    llm_parse_daily_limit: int = int(os.getenv("LLM_PARSE_DAILY_LIMIT", "500"))
    llm_parse_user_daily_limit: int = int(os.getenv("LLM_PARSE_USER_DAILY_LIMIT", "30"))
```

- [ ] **Step 2: Add to `.env.example`** (next to the other `LLM_*` vars), keeping its commenting style:

```bash
# Free-text "Describe your trip" parsing (needs a real LLM_PROVIDER above)
#LLM_PARSE_ENABLED=true
#LLM_PARSE_DAILY_LIMIT=500
#LLM_PARSE_USER_DAILY_LIMIT=30
```

- [ ] **Step 3: Sanity check + commit**

```bash
docker compose run --rm --no-deps app python -c "from app.config import settings; print(settings.llm_parse_enabled, settings.llm_parse_daily_limit)"
# Expected: True 500
git add backend/app/config.py .env.example
git commit -m "Add config for free-text parse feature (MVP 0.4)"
```

---

### Task 2: `ParsedSituation` / `ParseTextRequest` schemas

**Files:**
- Modify: `backend/app/schemas.py` (after `AdventureRequest`)
- Test: `backend/tests/test_parse_request.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_parse_request.py`:

```python
import pytest
from pydantic import ValidationError

from app.schemas import INTEREST_IDS, ParsedSituation, ParseTextRequest


# --- ParsedSituation validation ---------------------------------------------

def test_all_fields_default_to_none_and_is_empty():
    parsed = ParsedSituation()
    assert parsed.is_empty()
    assert not ParsedSituation(available_minutes=60).is_empty()


def test_out_of_range_minutes_rejected():
    with pytest.raises(ValidationError):
        ParsedSituation(available_minutes=10)
    with pytest.raises(ValidationError):
        ParsedSituation(available_minutes=1000)


def test_children_ages_dropped_outside_0_18():
    parsed = ParsedSituation(children_ages=[5, 8, 44, -1])
    assert parsed.children_ages == [5, 8]
    assert ParsedSituation(children_ages=[99]).children_ages is None


def test_interests_whitelisted_and_lowercased():
    parsed = ParsedSituation(interests=["History", "beaches", "surprise me"])
    assert parsed.interests == ["history", "surprise me"]
    assert ParsedSituation(interests=["beaches"]).interests is None


def test_unknown_enum_values_rejected():
    with pytest.raises(ValidationError):
        ParsedSituation(transport_mode="train")


def test_interest_ids_match_the_ui():
    assert INTEREST_IDS == {"history", "fortresses", "viewpoints", "nature", "water", "food", "surprise me"}


# --- ParseTextRequest --------------------------------------------------------

def test_parse_text_request_length_limits():
    with pytest.raises(ValidationError):
        ParseTextRequest(text="hi")
    with pytest.raises(ValidationError):
        ParseTextRequest(text="x" * 501)
    assert ParseTextRequest(text="two hours with kids").lang == "en"
```

- [ ] **Step 2: Run to verify failure**

```bash
docker compose run --rm --no-deps app python -m pytest -q -k parse
```
Expected: ImportError (`INTEREST_IDS` etc. don't exist).

- [ ] **Step 3: Implement in `backend/app/schemas.py`**

After the `AdventureRequest` class (keep the repo's v1 `@validator` style):

```python
# Canonical interest ids — must match the data-interest values in
# frontend/index.html. Used by the UI, the parse whitelist and the LLM prompt.
INTEREST_IDS = {"history", "fortresses", "viewpoints", "nature", "water", "food", "surprise me"}


class ParseTextRequest(BaseModel):
    text: str = Field(..., min_length=3, max_length=500)
    lang: Lang = "en"
    anonymous_id: str | None = Field(default=None, max_length=64)


class ParsedSituation(BaseModel):
    """Subset of AdventureRequest a parser may set. Every field optional:
    None means 'not mentioned' and the frontend keeps its default."""

    available_minutes: int | None = Field(default=None, ge=30, le=720)
    transport_mode: TransportMode | None = None
    group_type: GroupType | None = None
    children_ages: list[int] | None = None
    with_dog: bool | None = None
    with_elderly: bool | None = None
    reduced_mobility: bool | None = None
    intensity: Intensity | None = None
    interests: list[str] | None = None
    max_walking_km: float | None = Field(default=None, ge=0, le=30)

    @validator("children_ages")
    def keep_plausible_ages(cls, value: Any) -> list[int] | None:
        if value is None:
            return None
        ages = [int(v) for v in value if isinstance(v, (int, float)) and 0 <= int(v) <= 18]
        return ages or None

    @validator("interests", pre=True)
    def whitelist_interests(cls, value: Any) -> list[str] | None:
        if value is None:
            return None
        if isinstance(value, str):
            value = [value]
        kept = [str(v).strip().lower() for v in value if str(v).strip().lower() in INTEREST_IDS]
        return kept or None

    def is_empty(self) -> bool:
        return all(v is None for v in self.dict().values())
```

- [ ] **Step 4: Run tests**

```bash
docker compose run --rm --no-deps app python -m pytest -q -k parse
```
Expected: all PASS. Also run the full suite once (`python -m pytest -q`) — no regressions.

- [ ] **Step 5: Commit**

```bash
git add backend/app/schemas.py backend/tests/test_parse_request.py
git commit -m "Add ParsedSituation/ParseTextRequest schemas with grounding validators"
```

---

### Task 3: Generalize the daily-budget limiter

**Files:**
- Modify: `backend/app/services/storage.py:321-356` (`reserve_google_calls`)
- Modify: `backend/app/services/google_places.py:129` (call site)
- Test: `backend/tests/test_google_places.py:155-190` (existing budget tests)

- [ ] **Step 1: Update the existing budget tests to the new API**

In `backend/tests/test_google_places.py`, the limits move from patched settings to call arguments. Replace `_budget_store` and the five budget tests:

```python
def _budget_store(tmp_path):
    return Storage(tmp_path / "budget.db")


def _reserve(store, anonymous_id, requested, daily=100, per_user=50):
    return store.reserve_api_calls(
        "google", anonymous_id, requested, daily_limit=daily, user_daily_limit=per_user
    )


def test_reserve_clamps_to_the_tighter_limit(tmp_path):
    store = _budget_store(tmp_path)
    assert _reserve(store, "u", 5, daily=10, per_user=3) == 3


def test_users_share_the_global_budget(tmp_path):
    store = _budget_store(tmp_path)
    assert _reserve(store, "a", 3, daily=5, per_user=5) == 3
    assert _reserve(store, "b", 3, daily=5, per_user=5) == 2  # only 2 left globally


def test_reserve_requires_an_anonymous_id(tmp_path):
    store = _budget_store(tmp_path)
    assert _reserve(store, None, 5) == 0


def test_budget_resets_on_a_new_day(tmp_path, monkeypatch):
    store = _budget_store(tmp_path)
    assert _reserve(store, "u", 2, daily=2, per_user=2) == 2
    assert _reserve(store, "u", 1, daily=2, per_user=2) == 0
    monkeypatch.setattr(Storage, "_usage_day", staticmethod(lambda: "2099-01-01"))
    assert _reserve(store, "u", 1, daily=2, per_user=2) == 1


def test_budgets_are_independent_per_api(tmp_path):
    store = _budget_store(tmp_path)
    assert store.reserve_api_calls("google", "u", 2, daily_limit=2, user_daily_limit=2) == 2
    # "google" budget drained; "parse" budget untouched
    assert store.reserve_api_calls("google", "u", 1, daily_limit=2, user_daily_limit=2) == 0
    assert store.reserve_api_calls("parse", "u", 1, daily_limit=2, user_daily_limit=2) == 1
```

Keep `test_old_usage_rows_are_pruned` as-is except: change its `store.reserve_google_calls(...)` call (the one that triggers pruning) to `_reserve(store, ...)` with the same arguments, and drop the `monkeypatch` fixture from any test that no longer uses it.

- [ ] **Step 2: Run to verify failure**

```bash
docker compose run --rm --no-deps app python -m pytest -q backend/tests/test_google_places.py 2>/dev/null || docker compose run --rm --no-deps app python -m pytest -q -k google
```
Expected: FAIL with `AttributeError: 'Storage' object has no attribute 'reserve_api_calls'`.

- [ ] **Step 3: Rename + generalize in `storage.py`**

Replace `reserve_google_calls` (keep `_usage_day` untouched):

```python
    def reserve_api_calls(
        self,
        api: str,
        anonymous_id: str | None,
        requested: int,
        *,
        daily_limit: int,
        user_daily_limit: int,
    ) -> int:
        """Grant up to `requested` calls of `api` within today's budgets.

        Returns min(requested, global remaining, user remaining) and records
        the grant against both counters in one transaction, so concurrent
        requests can't overdraw. Budgets are independent per `api` (scope is
        prefixed). Requests without an anonymous_id get nothing: they can't be
        rate-limited individually, so they don't get to spend the budget.
        Failed calls still count (never under-counts spend).
        """
        if requested <= 0 or not anonymous_id:
            return 0
        day = self._usage_day()
        with self._connect() as conn:
            counters = ((f"{api}:global", ""), (f"{api}:user", anonymous_id))
            used: dict[str, int] = {}
            for scope, key in counters:
                row = conn.execute(
                    "SELECT count FROM api_usage WHERE day=? AND scope=? AND key=?",
                    (day, scope, key),
                ).fetchone()
                used[scope] = row["count"] if row else 0
            granted = min(
                requested,
                max(0, daily_limit - used[f"{api}:global"]),
                max(0, user_daily_limit - used[f"{api}:user"]),
            )
            if granted > 0:
                for scope, key in counters:
                    conn.execute(
                        "INSERT INTO api_usage (day, scope, key, count) VALUES (?, ?, ?, ?) "
                        "ON CONFLICT(day, scope, key) DO UPDATE SET count = count + excluded.count",
                        (day, scope, key, granted),
                    )
            # Counters are only read for "today", so old rows are dead weight.
            conn.execute("DELETE FROM api_usage WHERE day < date(?, '-7 days')", (day,))
        return granted
```

(Old `"global"`/`"user"` rows from 0.3 deployments age out via the 7-day prune; counters effectively reset once on deploy, which is acceptable.)

- [ ] **Step 4: Update the call site in `google_places.py:129`**

```python
    granted = storage.reserve_api_calls(
        "google",
        anonymous_id,
        len(misses),
        daily_limit=settings.google_places_daily_limit,
        user_daily_limit=settings.google_places_user_daily_limit,
    )
```

Note: `google_places.py` reads `settings` through its module global (tests patch `app.services.google_places.settings`), so the limits keep coming from the patched object there.

- [ ] **Step 5: Run the full suite**

```bash
docker compose run --rm --no-deps app python -m pytest -q
```
Expected: all PASS (including untouched `enrich_places` budget tests — if any of them monkeypatch `reserve_google_calls` by name, update that monkeypatch target to `reserve_api_calls`).

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/storage.py backend/app/services/google_places.py backend/tests/test_google_places.py
git commit -m "Generalize daily budget limiter to reserve_api_calls(api, ...)"
```

---

### Task 4: `parse_situation` on the provider base

**Files:**
- Modify: `backend/app/services/llm/base.py`
- Test: `backend/tests/test_parse_request.py` (append)

- [ ] **Step 1: Write the failing test** (append to `test_parse_request.py`):

```python
import asyncio

from app.services.llm.template import TemplateProvider


def test_template_provider_cannot_parse():
    assert asyncio.run(TemplateProvider().parse_situation("two hours on foot", "en")) is None
```

- [ ] **Step 2: Run to verify failure**

```bash
docker compose run --rm --no-deps app python -m pytest -q -k parse
```
Expected: FAIL — `AttributeError: ... no attribute 'parse_situation'`.

- [ ] **Step 3: Implement in `base.py`**

Add to the imports: `from app.schemas import AdventureRequest, ParsedSituation, Recommendation`, then add to the `LLMProvider` class:

```python
    async def parse_situation(self, text: str, lang: str) -> ParsedSituation | None:
        """Map a free-text trip description onto AdventureRequest fields.
        None means 'cannot parse' — also the default, so the TemplateProvider
        (and any provider that doesn't override this) reports no support."""
        return None
```

- [ ] **Step 4: Run tests** (same command). Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/llm/base.py backend/tests/test_parse_request.py
git commit -m "Add parse_situation to the LLM provider interface"
```

---

### Task 5: Decouple `_call_model` from explanations

`OpenAICompatibleProvider._call_model` / `._body` take an `ExplanationInput` today; parse needs the same HTTP/retry/fallback machinery with different messages.

**Files:**
- Modify: `backend/app/services/llm/openai_compat.py:406-424` (`_body`), `:475-606` (`_call_model`), `:608-652` (`explain`)
- Test: `backend/tests/test_openai_compat.py:109-121` (the `_body` tests)

- [ ] **Step 1: Update the `_body` tests to pass messages**

In `backend/tests/test_openai_compat.py`, add `build_messages` to the existing `from app.services.llm.openai_compat import ...` line, then in `test_body_skips_response_format_for_gemini` and `test_body_includes_response_format_for_non_gemini` wrap every `_payload(...)` argument to `_body` with `build_messages(...)`, e.g.:

```python
    body = gemini._body(build_messages(_payload([_rec()])), "gemini-2.5-flash")
```

- [ ] **Step 2: Run to verify failure**

```bash
docker compose run --rm --no-deps app python -m pytest -q -k openai_compat
```
Expected: FAIL (the implementation still calls `build_messages` internally; bodies won't match / type error).

- [ ] **Step 3: Refactor the implementation**

- `_body` signature: `def _body(self, messages: list[dict[str, str]], model: str) -> dict[str, Any]:` and inside replace `"messages": build_messages(payload),` with `"messages": messages,`.
- `_call_model` signature: replace the `payload: ExplanationInput` keyword parameter with `messages: list[dict[str, str]]`; replace `body = self._body(payload, model)` with `body = self._body(messages, model)`; in the first `logger.info(...)` replace the `recommendation_count=%s` field + `len(payload.recommendations)` arg with `message_count=%s` + `len(messages)`.
- In `explain()`, before the `async with http_client(...)` block add `messages = build_messages(payload)`, and change the call to `self._call_model(client=client, model=model, messages=messages)`.

- [ ] **Step 4: Run tests**

```bash
docker compose run --rm --no-deps app python -m pytest -q -k openai_compat
```
Expected: all PASS (the retry/fallback tests exercise `explain()` end-to-end and confirm the refactor).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/llm/openai_compat.py backend/tests/test_openai_compat.py
git commit -m "Refactor _call_model to take messages, decoupling it from explanations"
```

---### Task 6: Implement `parse_situation` in `OpenAICompatibleProvider`

**Files:**
- Modify: `backend/app/services/llm/openai_compat.py`
- Test: `backend/tests/test_parse_request.py` (append)

- [ ] **Step 1: Write the failing tests** (append to `test_parse_request.py`):

```python
from app.services.llm.openai_compat import OpenAICompatibleProvider, build_parse_messages


def _provider(**kwargs):
    return OpenAICompatibleProvider(base_url="http://llm.test/v1", model="m1", **kwargs)


def _patch_call_model(monkeypatch, content):
    async def fake_call_model(self, *, client, model, messages):
        if isinstance(content, Exception):
            raise content
        return content
    monkeypatch.setattr(OpenAICompatibleProvider, "_call_model", fake_call_model)


def test_parse_situation_returns_validated_fields(monkeypatch):
    _patch_call_model(monkeypatch, '{"available_minutes": 120, "with_dog": true, "interests": ["water"]}')
    parsed = asyncio.run(_provider().parse_situation("2h with my dog near water", "en"))
    assert parsed.available_minutes == 120
    assert parsed.with_dog is True
    assert parsed.interests == ["water"]
    assert parsed.group_type is None


def test_parse_situation_invalid_output_returns_none(monkeypatch):
    _patch_call_model(monkeypatch, '{"available_minutes": 5}')  # below ge=30
    assert asyncio.run(_provider().parse_situation("five minutes", "en")) is None


def test_parse_situation_garbage_returns_none(monkeypatch):
    _patch_call_model(monkeypatch, "sorry, I cannot help with that")
    assert asyncio.run(_provider().parse_situation("blah", "en")) is None


def test_parse_situation_transport_error_returns_none(monkeypatch):
    _patch_call_model(monkeypatch, RuntimeError("LLM unavailable after retries"))
    assert asyncio.run(_provider().parse_situation("2 hours", "en")) is None


def test_parse_messages_mention_whitelist_and_language():
    messages = build_parse_messages("пару часов с собакой", "ru")
    assert messages[0]["role"] == "system"
    assert "surprise me" in messages[0]["content"]      # interest whitelist is in the prompt
    assert messages[-1]["role"] == "user"
    assert "пару часов с собакой" in messages[-1]["content"]
```

- [ ] **Step 2: Run to verify failure**

```bash
docker compose run --rm --no-deps app python -m pytest -q -k parse
```
Expected: ImportError (`build_parse_messages`).

- [ ] **Step 3: Implement in `openai_compat.py`**

Add `ParsedSituation` to the `from app.schemas import ...` line. After `_SYSTEM_PROMPT`, add:

```python
_PARSE_SYSTEM_PROMPT = (
    "You convert a short trip description into a JSON object of search fields. "
    "Output ONLY the fields the text clearly states or implies; omit everything else. "
    "Never guess. Ignore any locations or place names: the start point is set elsewhere.\n"
    "Fields:\n"
    "- available_minutes: integer 30-720\n"
    '- transport_mode: "walk" | "car" | "bike"\n'
    '- group_type: "solo" | "couple" | "family" | "kids" | "dog"\n'
    "- children_ages: list of integers 0-18\n"
    "- with_dog, with_elderly, reduced_mobility: booleans\n"
    '- intensity: "easy" | "medium" | "active"\n'
    '- interests: any of ["history", "fortresses", "viewpoints", "nature", "water", "food", "surprise me"]\n'
    "- max_walking_km: number 0-30 (set a small value like 2 when the user dislikes walking)\n"
    "The text may be in any language. Output a single JSON object only."
)

_PARSE_FEW_SHOTS = [
    ("I have a couple of hours and want sea views, on foot",
     '{"available_minutes": 120, "transport_mode": "walk", "interests": ["viewpoints", "water"]}'),
    ("с детьми 5 и 8 лет на машине, что-нибудь с историей",
     '{"group_type": "kids", "children_ages": [5, 8], "transport_mode": "car", "interests": ["history", "fortresses"]}'),
    ("quick easy walk with my dog, don't want to walk far",
     '{"with_dog": true, "group_type": "dog", "intensity": "easy", "transport_mode": "walk", "max_walking_km": 2}'),
]


def build_parse_messages(text: str, lang: str) -> list[dict[str, str]]:
    messages = [{"role": "system", "content": _PARSE_SYSTEM_PROMPT}]
    for user, assistant in _PARSE_FEW_SHOTS:
        messages.append({"role": "user", "content": user})
        messages.append({"role": "assistant", "content": assistant})
    messages.append({"role": "user", "content": text})
    return messages
```

(`lang` is accepted for future prompt localization; the model handles either input language with the same prompt.)

Add the method to `OpenAICompatibleProvider` (after `explain`):

```python
    async def parse_situation(self, text: str, lang: str) -> ParsedSituation | None:
        models = self._models()
        if not models:
            return None
        messages = build_parse_messages(text, lang)
        async with http_client(self.timeout) as client:
            for model in models:
                try:
                    content = await self._call_model(client=client, model=model, messages=messages)
                    return ParsedSituation(**_extract_json_object(content))
                except Exception as exc:  # noqa: BLE001 - parse is best-effort; None = "couldn't understand"
                    logger.warning(
                        "LLM parse failed: provider=%s model=%s error_type=%s error=%r",
                        self._provider_label, model, type(exc).__name__, exc,
                    )
        return None
```

- [ ] **Step 4: Run tests**

```bash
docker compose run --rm --no-deps app python -m pytest -q -k parse
```
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/llm/openai_compat.py backend/tests/test_parse_request.py
git commit -m "Implement free-text situation parsing in the OpenAI-compatible provider"
```

---

### Task 7: `/api/features` + `/api/parse-request` endpoints

**Files:**
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_parse_request.py` (append)

- [ ] **Step 1: Write the failing tests** (append):

```python
from fastapi.testclient import TestClient

from app.main import app
from app.services.llm.base import LLMProvider

client = TestClient(app)


class FakeParseProvider(LLMProvider):
    def __init__(self, result=None, error=None):
        self.result, self.error, self.calls = result, error, 0

    async def parse_situation(self, text, lang):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


class FakeStorage:
    def __init__(self, grant=1):
        self.grant = grant

    def reserve_api_calls(self, api, anonymous_id, requested, *, daily_limit, user_daily_limit):
        return self.grant


def _enable(monkeypatch, provider, grant=1):
    monkeypatch.setattr("app.main.get_llm_provider", lambda: provider)
    monkeypatch.setattr("app.main.storage", FakeStorage(grant))


def test_features_off_with_template_provider():
    # default settings: LLM_PROVIDER=template
    assert client.get("/api/features").json() == {"parse": False}
    res = client.post("/api/parse-request", json={"text": "two hours", "anonymous_id": "u"})
    assert res.status_code == 404


def test_features_on_with_real_provider(monkeypatch):
    _enable(monkeypatch, FakeParseProvider())
    assert client.get("/api/features").json() == {"parse": True}


def test_parse_request_returns_parsed_fields(monkeypatch):
    provider = FakeParseProvider(result=ParsedSituation(available_minutes=90, with_dog=True))
    _enable(monkeypatch, provider)
    res = client.post("/api/parse-request", json={"text": "1.5h with dog", "anonymous_id": "u"})
    assert res.status_code == 200
    body = res.json()["parsed"]
    assert body["available_minutes"] == 90 and body["with_dog"] is True
    assert provider.calls == 1


def test_parse_request_nothing_recognized(monkeypatch):
    _enable(monkeypatch, FakeParseProvider(result=ParsedSituation()))  # all-None
    res = client.post("/api/parse-request", json={"text": "asdf qwer", "anonymous_id": "u"})
    assert res.status_code == 200 and res.json()["parsed"] is None


def test_parse_request_none_from_provider(monkeypatch):
    _enable(monkeypatch, FakeParseProvider(result=None))
    res = client.post("/api/parse-request", json={"text": "asdf qwer", "anonymous_id": "u"})
    assert res.status_code == 200 and res.json()["parsed"] is None


def test_parse_request_provider_error_is_502(monkeypatch):
    _enable(monkeypatch, FakeParseProvider(error=RuntimeError("boom")))
    res = client.post("/api/parse-request", json={"text": "two hours", "anonymous_id": "u"})
    assert res.status_code == 502


def test_parse_request_budget_exhausted_is_429_and_skips_provider(monkeypatch):
    provider = FakeParseProvider(result=ParsedSituation(available_minutes=90))
    _enable(monkeypatch, provider, grant=0)
    res = client.post("/api/parse-request", json={"text": "two hours", "anonymous_id": "u"})
    assert res.status_code == 429
    assert provider.calls == 0
```

- [ ] **Step 2: Run to verify failure**

```bash
docker compose run --rm --no-deps app python -m pytest -q -k parse
```
Expected: FAIL — 404s from missing routes / ImportError if `fastapi.testclient` needs `httpx` (it's already a dependency).

- [ ] **Step 3: Implement in `main.py`**

Extend imports:

```python
from fastapi import FastAPI, HTTPException
from app.schemas import AdventureRequest, AnalyticsEvent, FeedbackRequest, ParseTextRequest, VisitedRequest
from app.services.llm.factory import get_llm_provider
from app.services.llm.template import TemplateProvider
```

Add after the `/health` route:

```python
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
```

- [ ] **Step 4: Run the full suite**

```bash
docker compose run --rm --no-deps app python -m pytest -q
```
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/main.py backend/tests/test_parse_request.py
git commit -m "Add /api/features and /api/parse-request endpoints with daily budget"
```

---

### Task 8: `request_text` plumbing (frontend)

**Files:**
- Modify: `frontend/app.js` (`requestPayload`, ~line 769; module top ~line 27)
- Modify: `frontend/mood.js` (`choosePreset` ~line 197, `applyStaged` ~line 366)

- [ ] **Step 1: app.js — pending text variable**

Near the other module state (`let lastViewedCardId = null;` block):

```js
// Free-text description that produced the current search; rides along as
// request_text so the explanation LLM sees the user's own words.
let pendingRequestText = null;
window.setRequestText = (text) => { pendingRequestText = text || null; };
```

In `requestPayload()`, replace `request_text: null,` with `request_text: pendingRequestText,`.

(Deliberate detail: `loadMoreResults` / "Show others" reuse `requestPayload` for the *same* situation, so the text correctly stays for pagination; it's cleared only when a *new* manual search is committed — next step.)

- [ ] **Step 2: mood.js — clear on manual searches**

In `choosePreset(p)`, first line: `if (window.setRequestText) window.setRequestText(null);`
In `applyStaged()`, first line: `if (window.setRequestText) window.setRequestText(null);`

- [ ] **Step 3: Verify + commit**

Rebuild and check no behavioral change (`docker compose up -d --build app`, run a preset search from the LAN IP, confirm the request body has `request_text: null` via `browser_network_requests`).

```bash
git add frontend/app.js frontend/mood.js
git commit -m "Plumb request_text from the describe flow into search payloads"
```

---

### Task 9: Describe field UI + parse flow (frontend)

**Files:**
- Modify: `frontend/mood.js` (LX strings ~line 9; `buildLauncher` ~line 208; `buildFilterBar` ~line 415)
- Modify: `frontend/mood.css` (launcher styles)

- [ ] **Step 1: LX strings**

Add to `LX.en`:

```js
      describe_ph: "Describe it: time, company, mood…",
      describe_fail: "Couldn’t understand that — try different words or pick a vibe",
      describe_mic: "Dictate",
```

Add to `LX.ru`:

```js
      describe_ph: "Опишите: время, компания, настроение…",
      describe_fail: "Не получилось понять — попробуйте другими словами или выберите настроение",
      describe_mic: "Надиктовать",
```

- [ ] **Step 2: Feature flag fetch**

Near the top of the mood.js IIFE state:

```js
  var featureParse = false;
  fetch("/api/features")
    .then(function (r) { return r.json(); })
    .then(function (f) { featureParse = !!(f && f.parse); if (featureParse) buildLauncher(); })
    .catch(function () {});
```

- [ ] **Step 3: Describe box in `buildLauncher()`**

After the greet-row block (before the best-now button html):

```js
    if (featureParse) {
      html += '<div class="describe-box" id="describeBox">';
      html += '  <input id="describeInput" maxlength="500" autocomplete="off" placeholder="' + lx("describe_ph") + '">';
      if (window.SpeechRecognition || window.webkitSpeechRecognition) {
        html += '<button type="button" class="describe-mic" id="describeMic" title="' + lx("describe_mic") + '">' + icon("mic") + "</button>";
      }
      html += '  <button type="button" class="describe-go" id="describeGo">' + icon("arrow-right") + "</button>";
      html += "</div>";
      html += '<p class="describe-err hidden" id="describeErr">' + lx("describe_fail") + "</p>";
    }
```

In the wiring section at the bottom of `buildLauncher()` (next to the `bestNowBtn` listener):

```js
    var dGo = $("describeGo"); if (dGo) dGo.addEventListener("click", submitDescribe);
    var dIn = $("describeInput");
    if (dIn) dIn.addEventListener("keydown", function (e) { if (e.key === "Enter") submitDescribe(); });
    wireMic();
```

- [ ] **Step 4: Parse flow**

Add near `choosePreset`:

```js
  // ---- "Describe your trip": parse free text into a preset-shaped search ---
  var PARSED_FACET = {
    available_minutes: "time", transport_mode: "transport", group_type: "crew",
    children_ages: "crew", with_dog: "crew", with_elderly: "crew",
    reduced_mobility: "crew", intensity: "effort", interests: "interest",
  };
  var aiSetFacets = [];

  function parsedToPreset(parsed) {
    var p = smartNowPreset();   // missing fields keep time-of-day defaults
    if (parsed.available_minutes != null) p.time = parsed.available_minutes;
    if (parsed.transport_mode) p.transport = parsed.transport_mode;
    if (parsed.group_type) p.crew = parsed.group_type;
    if (parsed.intensity) p.intensity = parsed.intensity;
    if (parsed.interests) p.interests = parsed.interests;
    if (parsed.children_ages) p.childrenAges = parsed.children_ages;
    if (parsed.max_walking_km != null) p.maxWalkingKm = parsed.max_walking_km;
    if (parsed.with_dog != null) p.withDog = parsed.with_dog;
    if (parsed.with_elderly != null) p.withElderly = parsed.with_elderly;
    if (parsed.reduced_mobility != null) p.reducedMobility = parsed.reduced_mobility;
    return p;
  }

  function describeFail() {
    var err = $("describeErr"); if (err) err.classList.remove("hidden");
    var box = $("describeBox"); if (box) box.classList.remove("busy");
  }

  function submitDescribe() {
    var input = $("describeInput"); if (!input) return;
    var text = input.value.trim();
    if (text.length < 3) return;
    var err = $("describeErr"); if (err) err.classList.add("hidden");
    var box = $("describeBox"); if (box) box.classList.add("busy");
    fetch("/api/parse-request", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text: text,
        lang: currentLang,
        anonymous_id: typeof anonymousId === "function" ? anonymousId() : null,
      }),
    })
      .then(function (r) { if (!r.ok) throw new Error("parse http " + r.status); return r.json(); })
      .then(function (data) {
        var parsed = data && data.parsed;
        if (!parsed) { describeFail(); return; }
        if (box) box.classList.remove("busy");
        aiSetFacets = [];
        Object.keys(parsed).forEach(function (k) {
          var facet = PARSED_FACET[k];
          if (parsed[k] != null && facet && aiSetFacets.indexOf(facet) === -1) aiSetFacets.push(facet);
        });
        if (window.setRequestText) window.setRequestText(text);
        currentMood = null;                  // custom situation, not a vibe
        applyPreset(parsedToPreset(parsed));
        commitSearch();
        buildFilterBar();
      })
      .catch(function () { describeFail(); });
  }
```

- [ ] **Step 5: `.ai-set` highlight in `buildFilterBar()`**

Change the fchip line to include the class:

```js
      html += '<button type="button" class="fchip' + (facetChanged(f) ? " changed" : "") +
        (aiSetFacets.indexOf(f.key) !== -1 ? " ai-set" : "") +
        '" data-facet="' + f.key + '"><span class="fk">' + lx(f.label) + "</span><b>" + facetValue(f) + "</b>" + icon("chevron-down") + "</button>";
```

And at the end of `buildFilterBar()` (after `refreshIcons();`):

```js
    if (aiSetFacets.length) {
      aiSetFacets = [];   // one-shot: highlight only right after a parse
      bar.querySelectorAll(".fchip.ai-set").forEach(function (el) {
        el.addEventListener("animationend", function () { el.classList.remove("ai-set"); }, { once: true });
      });
    }
```

- [ ] **Step 6: Styles in `mood.css`**

```css
/* ---- "Describe your trip" (free-text + voice) --------------------------- */
.describe-box { display: flex; gap: 8px; align-items: center; margin: 4px 0 14px; }
.describe-box input { flex: 1; min-width: 0; border: 1.5px solid var(--sand-200, #e7deca); border-radius: 14px;
  padding: 12px 14px; font: inherit; background: #fff; }
.describe-box input:focus { outline: none; border-color: var(--clay-500, #c4683f); }
.describe-mic, .describe-go { flex: 0 0 auto; width: 44px; height: 44px; border: none; border-radius: 13px;
  display: grid; place-items: center; cursor: pointer; background: var(--sand-200, #e7deca); }
.describe-go { background: var(--clay-500, #c4683f); color: #fff; }
.describe-mic.listening { background: var(--clay-500, #c4683f); color: #fff; animation: mic-pulse 1.2s infinite; }
.describe-box.busy .describe-go { opacity: 0.5; pointer-events: none; }
.describe-err { margin: -8px 0 10px; font-size: 13px; color: #a4441f; }
.describe-err.hidden { display: none; }
@keyframes mic-pulse { 50% { transform: scale(1.08); } }
.fchip.ai-set { animation: ai-pulse 1.3s ease 2; }
@keyframes ai-pulse { 50% { box-shadow: 0 0 0 4px rgba(196, 104, 63, 0.35); } }
```

Adjust the custom-property fallbacks to the palette actually defined in `styles.css`/`mood.css` (check `:root` there; if the variable names differ, use the existing ones).

- [ ] **Step 7: Stub `wireMic` (real implementation is Task 10)**

```js
  function wireMic() {}
```

- [ ] **Step 8: Verify with Playwright + commit**

Bump `?v=` for `mood.js`, `mood.css` (and `app.js` if not yet bumped this branch) in `index.html`. `docker compose up -d --build app`. From the LAN IP:

- Default (template provider): describe box absent, presets work.
- Restart with a stub: temporarily set `LLM_PROVIDER=openrouter`-style real provider in `.env` *or* verify the enabled state by stubbing in the browser:
  `await page.route('/api/features', r => r.fulfill({ json: { parse: true } }))` and
  `await page.route('/api/parse-request', r => r.fulfill({ json: { parsed: { available_minutes: 180, with_dog: true, transport_mode: 'walk' } } }))`
  before loading the page; then type a description, press Enter, and confirm: a search fires, the filter bar shows *3 hours / walk / crew* values with the pulse, and the recommendations request body carries `request_text` (via `browser_network_requests`).
- Error path: re-route `/api/parse-request` to fulfill with `{ json: { parsed: null } }` → inline fail message, no search request.

```bash
git add frontend/mood.js frontend/mood.css frontend/index.html
git commit -m "Add describe-your-trip field: parse, prefill, auto-search, AI highlight"
```

---

### Task 10: Voice input (Web Speech API)

**Files:**
- Modify: `frontend/mood.js` (replace the `wireMic` stub)

- [ ] **Step 1: Implement `wireMic`**

```js
  function wireMic() {
    var btn = $("describeMic"); if (!btn) return;
    var SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    var rec = null;
    btn.addEventListener("click", function () {
      if (rec) { rec.stop(); return; }   // tap again = stop listening
      rec = new SR();
      rec.lang = currentLang === "ru" ? "ru-RU" : "en-US";
      rec.interimResults = true;
      rec.onresult = function (e) {
        var text = Array.prototype.map.call(e.results, function (r) { return r[0].transcript; }).join(" ").trim();
        var input = $("describeInput"); if (input) input.value = text;
        if (e.results[e.results.length - 1].isFinal) submitDescribe();
      };
      rec.onend = function () { rec = null; btn.classList.remove("listening"); };
      rec.onerror = function () { rec = null; btn.classList.remove("listening"); };
      btn.classList.add("listening");
      rec.start();
    });
  }
```

- [ ] **Step 2: Verify + commit**

Bump `?v=` for `mood.js`. Rebuild. In Chromium (Playwright): the mic button renders when `/api/features` is stubbed to `{parse: true}` (the `webkitSpeechRecognition` global exists in Chromium; actual dictation needs a real microphone — manual check on a phone over the cloudflared tunnel is the realistic test). In Firefox the button must be absent.

```bash
git add frontend/mood.js frontend/index.html
git commit -m "Add voice dictation to the describe field via Web Speech API"
```

---

### Task 11: Final verification + docs

- [ ] **Step 1: Full backend suite**

```bash
docker compose run --rm --no-deps app python -m pytest -q
```
Expected: all PASS without any LLM key.

- [ ] **Step 2: Eval unchanged**

```bash
docker compose run --rm --no-deps app python -m eval.run
```
Expected: same numbers as on `main` (parse feature can't affect scoring).

- [ ] **Step 3: Template-provider e2e**

`docker compose up -d --build app`; from the LAN IP via Playwright: no describe box, preset search works end-to-end, `request_text: null` in the request body.

- [ ] **Step 4: Real-key happy path (manual, needs `LLM_PROVIDER`/`LLM_API_KEY` in `.env`)**

- EN UI: *"2 hours with my kids by car, something with water"* → search fires; filter bar pulses time/crew/transport/interest; recommendations request carries the text and parsed fields.
- RU UI: *"3 часа с собакой пешком"* → same, with `with_dog: true`.
- Nonsense (*"asdf qwer"*) → inline fail message, no search.
- Budget: restart with `LLM_PARSE_DAILY_LIMIT=1`; second parse → fail message; recommendations unaffected.

- [ ] **Step 5: Commit any fixes, then hand off**

Use the superpowers:finishing-a-development-branch skill (merge/PR decision belongs to the user — the repo's convention is a PR per milestone, see `feature/google-places-enrichment` → PR #4).

---

## Self-review notes

- Spec coverage: WS1→Task 1, WS2→Task 2, WS3→Tasks 4–6, WS4→Tasks 3+7, WS5→Tasks 8–10, WS6→Tasks 2–7 tests, Verification→Task 11. One deliberate deviation from WS5: "Show others"/load-more *keeps* `request_text` because it paginates the same situation; clearing happens on preset tap and filter Apply (the spec's intent — no stale text on a *new* manual search).
- Existing-test impact is confined to `test_google_places.py` (budget API rename, Task 3) and `test_openai_compat.py` (`_body` signature, Task 5); both tasks update them explicitly.
