# MVP 0.4 — "Describe your trip": free-text + voice situation input

## Context

Today the situation is entered through vibe presets (`mood.js` launcher) or, indirectly, the
hidden wizard inputs that presets write into via `applyPreset()` before `commitSearch()` fires.
This milestone adds the most direct expression of the product vision — the user *describes* the
situation in their own words:

> "I have 3 hours, with my dog, don't want to drive far"

and the app turns that into a search. A new **`POST /api/parse-request`** endpoint asks the
already-integrated LLM provider (`backend/app/services/llm/`) to map free text onto the
structured `AdventureRequest` fields; the frontend fills the hidden wizard inputs exactly the way
presets do and **auto-searches immediately** (same gesture as tapping a vibe). The existing
filter bar on the results sheet is the confirmation/correction surface; chips whose values came
from parsing get a brief highlight so the user sees what was understood.

Decisions that shape the design:

- **Feature-flagged by provider.** With `TemplateProvider` (no real LLM configured) the feature
  is off: a new `GET /api/features` reports `{"parse": false}` and the frontend never renders the
  field. No degraded keyword-parser mode.
- **Auto-search like presets.** Parse → fill inputs → `commitSearch()`. Missing fields keep the
  time-of-day defaults from `smartNowPreset()`; no pre-search confirmation step.
- **Voice via the browser.** A mic button uses the Web Speech API (`webkitSpeechRecognition`)
  with the current UI language (`en-US`/`ru-RU`); the button hides where the API is unsupported
  (Firefox). Text input always works. No server-side speech.
- **The LLM never sets location.** Origin comes from the map / GPS only. The parser returns only
  the whitelisted situation fields below.
- **Cost-bounded.** Parse calls draw from app-side daily budgets (global + per-`anonymous_id`),
  reusing the `api_usage` table and reserve pattern from MVP 0.3.
- **The raw text rides along.** `AdventureRequest.request_text` already exists (currently always
  `null` from the frontend); the describe text is sent with the search so the downstream
  explanation LLM sees the user's own words.

Branch: `feature/describe-trip` off `main`. Backend-first.

---

## Workstream 1 — Config (`backend/app/config.py`)

New settings, following the existing env-var pattern:

```python
# Free-text situation parsing. Requires a real LLM provider; with the
# TemplateProvider the feature reports disabled regardless of this flag.
llm_parse_enabled: bool = os.getenv("LLM_PARSE_ENABLED", "true").lower() == "true"
# App-side daily budgets for parse calls (same pattern as Google enrichment).
# 0 disables the feature. Global stays the real backstop: anonymous_id is
# client-supplied.
llm_parse_daily_limit: int = int(os.getenv("LLM_PARSE_DAILY_LIMIT", "500"))
llm_parse_user_daily_limit: int = int(os.getenv("LLM_PARSE_USER_DAILY_LIMIT", "30"))
```

Timeout reuses `llm_timeout_seconds`. Add the new vars (commented defaults) to `.env.example` /
compose environment.

## Workstream 2 — Schemas (`backend/app/schemas.py`)

```python
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
```

- `children_ages` items clamped to 0–18 with a validator (mirror `parseChildrenAges` in app.js).
- `interests` validator lowercases and **whitelists** against the canonical ids used by the UI:
  `{"history", "fortresses", "viewpoints", "nature", "water", "food", "surprise me"}` (the
  `data-interest` values in `index.html`); unknown entries are dropped, an emptied list becomes
  `None`. Define the set once in `schemas.py` and reuse it in the parse prompt.
- Endpoint response: `{"parsed": ParsedSituation | None}`. `parsed: null` means "nothing
  recognized" — the frontend treats it the same as an error toast, no search fires.

## Workstream 3 — Provider extension (`services/llm/base.py`, `openai_compat.py`, `template.py`)

- `LLMProvider` gains `async def parse_situation(self, text: str, lang: str) -> ParsedSituation | None`,
  default implementation returns `None` (so `TemplateProvider` needs no change).
- `OpenAICompatibleProvider.parse_situation`: one JSON-mode chat call (temperature 0, small
  `max_tokens`), reusing the request plumbing `explain()` already has (base_url, fallback models,
  Gemini quirks). System prompt:
  - lists every field with its allowed values/ranges and the interest whitelist;
  - instructs *omit any key the text does not mention* — no guessing defaults;
  - 3–4 few-shot pairs covering EN and RU, including: relative time ("a couple of hours" → 120),
    kids with ages ("с детьми 5 и 8 лет" → `children_ages: [5, 8]`, `group_type: "kids"`), a dog
    ("with my dog" → `with_dog: true`, `group_type: "dog"`), and reluctance to walk ("don't want
    to walk much" → `max_walking_km: 2`).
  - Location mentions are explicitly ignored (origin comes from the map).
- Output is validated through `ParsedSituation` (`model_validate` in a `try/except` → `None`).
  The pydantic constraints + interest whitelist are the grounding guard; out-of-range or unknown
  values never reach the client. All-`None` result → return `None`.

## Workstream 4 — Endpoints + budget (`main.py`, `storage.py`)

- **`GET /api/features`** → `{"parse": bool}`: true iff `settings.llm_parse_enabled`, the daily
  limits are > 0, and `get_llm_provider()` is not a `TemplateProvider`. Static per process; the
  frontend fetches it once at load.
- **`POST /api/parse-request`** (body `ParseTextRequest`):
  1. Feature disabled → `404 {"detail": "parse_disabled"}` (the frontend never shows the field,
     so this only guards direct calls).
  2. Reserve budget (below); `granted == 0` → `429 {"detail": "parse_budget_exhausted"}`.
  3. `provider.parse_situation(text, lang)` wrapped in `try/except` and the existing timeout →
     on exception `502 {"detail": "parse_failed"}`; on success `{"parsed": …}` (possibly `null`).
- **Budget:** generalize the MVP 0.3 limiter. Rename `Storage.reserve_google_calls` →
  `reserve_api_calls(api: str, anonymous_id: str | None, requested: int, daily_limit: int,
  user_daily_limit: int) -> int`, writing `scope = f"{api}:global" / f"{api}:user"` rows in the
  existing `api_usage` table (no schema change; old `"global"`/`"user"` rows age out via the
  7-day prune). `google_places.py` passes `api="google"` with its limits; the parse endpoint
  passes `api="parse"`, `requested=1`. Same rule as 0.3: requests without an `anonymous_id` get
  `0` — they can't be rate-limited individually.

## Workstream 5 — Frontend: describe field (`mood.js`, `app.js`, `index.html`, `mood.css`)

- On load (non-blocking) `fetch('/api/features')` → cache `{parse}`; default `false` on any error.
- **Launcher UI** (`buildLauncher()` in mood.js): when enabled, render between the greet row and
  the best-now button:
  - text input `#describeInput`, placeholder `lx("describe_ph")` — *"Describe it: time, company,
    mood…"* / *"Опишите: время, компания, настроение…"* — `maxlength=500`, Enter submits;
  - mic button `#describeMic`, rendered only when
    `window.SpeechRecognition || window.webkitSpeechRecognition` exists; tap toggles dictation
    (`lang` = `en-US`/`ru-RU` from `currentLang`, `interimResults` streamed into the input,
    auto-submit on final result); a `.listening` class pulses while active;
  - submit button (arrow icon) + a `.describe-busy` spinner state during the round-trip.
- **Submit flow:**
  1. `POST /api/parse-request` with `{text, lang: currentLang, anonymous_id}` (reuse the
     `anonymousId()` helper via a small `window.` export from app.js).
  2. On HTTP error or `parsed == null`: show `lx("describe_fail")` — *"Couldn't understand that —
     try different words or pick a vibe"* — inline under the field; keep the text for editing. No
     search fires.
  3. On success: start from `smartNowPreset()`, overwrite with non-`null` parsed fields
     (`children_ages` joins to the comma string `applyPreset` expects), then
     `applyPreset(merged); commitSearch(); buildFilterBar();` — the preset path, plus remember
     which filter-bar facets came from parsing and add an `.ai-set` class to those chips
     (CSS pulse animation, class removed on `animationend`). This is the "defaults + highlight"
     decision mapped onto the auto-search flow.
- **`request_text` pass-through (app.js):** `requestPayload()` replaces the hardcoded
  `request_text: null` with a module variable `pendingRequestText` (exported setter
  `window.setRequestText`), set by the describe flow before `commitSearch()` and cleared whenever
  a search is committed from any other path (filter Apply, preset tap, Show others), so a stale
  description never rides along with a manual search.
- New `LX` strings (en + ru): `describe_ph`, `describe_fail`, `describe_mic` (button title).
- Bump the `?v=` cache-bust queries in `index.html` (app.js, mood.js, and mood.css if versioned).

## Workstream 6 — Tests (`backend/tests/test_parse_request.py`, new)

Monkeypatch style as in `test_google_places.py`; no live HTTP.

- `ParsedSituation` validation: out-of-range minutes rejected, ages clamped/dropped, unknown
  interests dropped (and emptied list → `None`), `"surprise me"` survives the whitelist.
- `/api/parse-request` with a fake provider returning a valid dict → 200 with the parsed fields;
  provider raising → 502; provider output failing `ParsedSituation` validation or all-`None` →
  200 `{"parsed": null}` (degrades to the same "couldn't understand" UX, per Workstream 3).
- Template provider (default settings) → `/api/features` reports `parse: false` and
  `/api/parse-request` → 404.
- Budget: `reserve_api_calls` grants clamp on the tighter of global/user limits and scopes are
  independent per `api` (a drained `"google"` budget doesn't block `"parse"`); `anonymous_id=None`
  → 0; endpoint returns 429 and **does not call the provider** when the grant is 0 (assert via a
  counting fake).
- Regression: `google_places.py` budget tests still pass against the renamed method.

---

## Verification

- `docker compose run --rm --no-deps app python -m pytest -q` — all green without any LLM key.
- Template-provider e2e: `docker compose up --build`, open from the LAN IP via Playwright — the
  describe field is absent, presets and search unchanged.
- With a real `LLM_PROVIDER`/`LLM_API_KEY` in `.env`: type *"3 часа с собакой пешком"* (RU UI) and
  *"2 hours with my kids by car, something with water"* (EN UI) → search fires, the filter bar
  shows the parsed time/crew/effort with the `.ai-set` pulse, and the request body in
  `browser_network_requests` carries `request_text` plus the parsed fields.
- Nonsense input ("asdf qwer") → inline fail message, no search request issued.
- Mic button: present in Chromium, absent in Firefox; dictation fills the field and auto-submits.
- Budget cutoff: restart with `LLM_PARSE_DAILY_LIMIT=1`; second parse from a fresh profile → the
  inline fail message, and the recommendations flow is unaffected.
- All via docker compose (no local venv); bump `?v=` before checking.
