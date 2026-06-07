# MVP 0.2 — Spec Review & Scope (Early Personalization + Pluggable LLM)

## Context

MVP 0.1 + 0.1.1 are shipped: location → preferences → OSM/Overpass places → Open-Meteo weather (origin +
per-place arrival forecast with re-ranking) → OSRM routing → Adventure Score v0.1 → rule-based explanations
→ Google/Apple Maps → feedback (with reasons) → analytics events. The stack today is **vanilla HTML/JS served
by FastAPI + SQLite + Open-Meteo + OSRM**, with EN/RU i18n. There is **no LLM, no user identity, no
personalization**.

`docs/Spec.md` is the product spec; its Roadmap defines **Version 0.2 = "Early Personalization."** Several
spec sections are now stale, and its headline AI differentiator (LLM explanations) was never built. This
document reviews the spec and defines a buildable 0.2.

Decisions taken with the user:
- The LLM explanation layer **is** in 0.2, built behind a **provider abstraction + dependency injection**.
  Targets: a local **llama.cpp** server, **OpenAI**, and **cheap-but-good hosted models** — all of which
  speak the OpenAI `/v1/chat/completions` API — plus the existing templates, swappable by config. The model's
  defining requirement is **honesty / factual grounding** (it explains computed data, never invents). No
  Claude/Anthropic dependency.
- 0.2 includes **all four** personalization pieces (anonymous identity, history, Personal Preference Fit,
  refined group inputs).
- `docs/Spec.md` will be **rewritten** to match reality and 0.2.

---

## Part 1 — Spec.md review: improve / add / remove

### Improve (stale vs. what's actually built)

| Spec says | Reality | Fix in Spec |
|---|---|---|
| §14 Frontend = Next.js | Vanilla HTML/JS served by FastAPI (`frontend/`) | Mark Next.js as optional future migration, not a requirement |
| §14 DB = PostgreSQL + PostGIS | SQLite (`storage.py`) | Document current = SQLite; Postgres/PostGIS = aspirational/scale target |
| §6/§13 Weather = OpenWeather | Open-Meteo default, OpenWeather optional (`weather.py`) | Name Open-Meteo as primary |
| §14/§22 AI = OpenAI, "LLM Explanation Service" | Not built; explanations are templates (`scoring.py:_why`) | Replace with a provider-agnostic, OpenAI-compatible LLM layer (local llama.cpp / OpenAI / cheap hosted / template fallback); grounded, no invented data |
| §16 feedback reasons incl. "слишком людно" | Implemented: too_far / too_difficult / bad_weather / not_interesting / inaccurate / other | Reconcile list; drop "too crowded" (unmeasurable) or mark user-reported only |
| §7 group = solo/couple/family/dog | Schema also has `kids`; refined toggles coming in 0.2 | Reconcile to the 0.2 refined-input model |
| Nothing about 0.1.1 | Arrival weather, Apple Maps, analytics events shipped | Fold these into §8/§9.2/§16 and the roadmap |
| Nothing about i18n | EN/RU implemented (`i18n.py`, `app.js`) | Add a localization note |

### Add
- **Provider-agnostic LLM section** (DI; local llama.cpp / OpenAI / cheap hosted, all OpenAI-compatible, +
  template fallback; "LLM explains, never invents data; safety stays rule-based" per §12; honesty/grounding
  is the model-selection criterion).
- **Anonymous identity** (`anonymous_id`) as the personalization foundation (no accounts).
- **Adventure Score v0.2** formula (adds Personal Preference Fit) — see below.
- **Privacy:** "delete my history" control (the spec §23 already promises history deletion).
- **Destination/arrival weather** in §9.2 context and the result card.

### Remove / descope
- **Telegram / Reddit / Facebook scraping** (§13, §18): keep the abstract Community layer, but remove named
  social-network scraping from the near-term roadmap (ToS/legal risk). Flag as "research-only, far future."
- **"Too crowded" feedback reason**: can't be measured in 0.2 — drop or relabel as purely user-reported.
- **Next.js as a hard requirement**: demote to optional.

---

## Part 2 — MVP 0.2 scope

**Theme: Early Personalization + a real (pluggable) AI explanation layer.** Still anonymous, no accounts.

**Included:** pluggable LLM explanations (A) · anonymous identity (B) · recommendation history + delete (C) ·
Personal Preference Fit / Score v0.2 (D) · refined group inputs (E) · Spec.md rewrite (F).

**Deferred to 0.3+:** saved places / full Adventure Memory · photos diary · notifications/daily picks · Event
Impact & live Traffic Fit · Community Intelligence · accounts/login · Postgres/PostGIS migration.

### Frontend: no Next.js in 0.2

0.2 stays on the current vanilla HTML/CSS/JS served by FastAPI (same origin, no build step, no CORS).
Next.js is **not** adopted in 0.2: the app is effectively one screen with user-specific, non-indexable
results, so its main strengths — SSR/SEO, multi-page routing, bundling — add no user-facing value, while it
would add a Node build pipeline, a React rewrite, and (typically) a second deploy with CORS. Everything 0.2
needs (history view, refined-input toggles, LLM `summary`/`why` rendering, preference breakdown) is a handful
of render functions in the existing `app.js`.

Reconsider a frontend framework at **0.3** (Adventure Memory Lite: saved places, daily recs, notifications,
several real screens), when manual DOM becomes the bottleneck. If migrating then, prefer a lightweight SPA
(Vite + React/Preact/Svelte) and choose Next.js only if SSR/SSG/edge rendering is actually needed.

**Suggested build order (separate commits):**
1. **B** anonymous identity (foundation) → 2. **E** refined group inputs → 3. **C** history + delete →
4. **D** Personal Preference Fit (Score v0.2) → 5. **A** pluggable LLM layer → 6. **F** Spec.md rewrite.

---

## Part 3 — Implementation plan

### A. Pluggable LLM explanation layer (dependency injection)

No vendor SDK — every target speaks the **OpenAI `/v1/chat/completions`** API, so one HTTP client (the
project's `httpx` via `net.http_client`) covers local **llama.cpp `llama-server`**, **OpenAI**, and cheap
hosted models (**DeepSeek, Groq, Together, OpenRouter, Mistral, Ollama**) by swapping `base_url` + `model` +
`api_key`.

New package `backend/app/services/llm/`:
- `base.py` — `LLMProvider` Protocol: `async def explain(self, payload: ExplanationInput) -> list[Explanation] | None`
  plus `name`. `ExplanationInput` = user context + the **already-computed** top candidates (score breakdown,
  warnings, data-confidence, missing-data notes). `Explanation` = `{summary, why[], data_confidence_note}`.
- `template.py` — `TemplateProvider`: wraps today's rule-based output (reuse `_why` / `_description` from
  `scoring.py`). Default + offline + fallback provider; no network, always available.
- `openai_compat.py` — `OpenAICompatibleProvider`: POSTs to `{base_url}/chat/completions` with
  `response_format=json_object` (or a tool/function schema) → strict `{summary, why[], data_confidence_note}`.
  Configurable `base_url`, `model`, optional `api_key` (bearer). Works unchanged for llama.cpp/OpenAI/hosted.
- `factory.py` — `get_llm_provider(settings) -> LLMProvider`, selected by `LLM_PROVIDER` (`template` |
  `openai`), with convenience **presets** (`llamacpp`, `openai`, `deepseek`, `groq`, `openrouter`, `ollama`, `gemini`)
  that just preset `base_url`. This is the injection point.

**Honesty / grounding (the core requirement):**
- The prompt passes **only computed facts** (scores, distances, the rule-based warnings, weather summary, and
  explicit "unknown/unavailable" notes for traffic/events). System prompt: rephrase and summarize the provided
  facts only; **never introduce numbers, places, weather, traffic, or claims not in the input**; if a datum is
  missing, say it's unavailable.
- A lightweight **post-check guard** validates the LLM output before use: reject/repair an explanation that
  introduces digits or place names absent from the input; on any violation or parse failure → fall back to the
  `TemplateProvider`. This keeps output trustworthy regardless of which model is configured.
- Model-selection note for the spec: prefer cheap models with **low hallucination + strong instruction-
  following + reliable JSON** (e.g. DeepSeek-V3, Qwen2.5-Instruct, Llama-3.x-Instruct, Mistral) — the grounding
  contract + guard make the layer safe even on small local models.

Wiring & safety:
- `config.py`: add `llm_provider`, `llm_base_url`, `llm_model`, `llm_api_key`, `llm_timeout_seconds`,
  `llm_max_explained` (top-N only, to bound cost/latency), `llm_enabled`. Default `LLM_PROVIDER=template` so
  nothing changes until a server/model is configured.
- `schemas.py`: add `summary: str | None` and `data_confidence_note: str | None` to `Recommendation`; `why`
  may be LLM-rewritten. **Warnings stay rule-based** (spec §12 — LLM must not own safety).
- `recommendations.py`: after the top-N are finalized (post arrival-weather re-rank), call the **injected**
  provider once for the batch (concurrently with the photo fetch via `asyncio.gather`). On
  error/timeout/guard-failure/disabled → keep template output (same graceful-degradation pattern as
  weather/OSM/routing). Bounded timeout protects the <30s KPI. Pass `provider` as a default-injected arg to
  `build_recommendations` so tests can supply a fake.
- `requirements.txt`: **no new dependency** (reuse `httpx`).
- Tests: `FakeProvider` asserts explanations merge in; a raising provider and a guard-violating provider both
  fall back to templates. No network.

**Prompts & model evaluation:**
- **Prompt design.** System prompt = "explain, don't decide; use ONLY the payload facts; never invent
  numbers/places/weather/traffic; mark missing data unavailable; repeat the computed warnings verbatim;
  output strict JSON in `{lang}`; summary ≤ 30 words, 2–4 short `why` bullets." The content message is the
  machine payload (title, type, score breakdown, **computed warnings verbatim**, weather summary,
  travel/activity minutes, distance, `data_confidence`, and an explicit `unknown_fields: [traffic, events,
  crowds]`). Force the shape with `response_format=json_object` (OpenAI/hosted) or a **GBNF / JSON-schema
  grammar** (llama.cpp) so invalid JSON is impossible. Temperature 0–0.3. Add 1–2 few-shot anchors, including
  one where a field is missing and the model correctly answers "unavailable". The **prompt is versioned** — a
  prompt change is a new benchmark candidate.
- **Eval set.** 20–50 frozen `ExplanationInput` fixtures captured from real `/api/recommendations` runs under
  `backend/eval/golden/` — covering sunny/rainy, near/far, family/dog, varied place types, **missing-data**
  cases, and **both EN and RU** — plus an **adversarial slice** that tempts invention (omit weather; mark
  traffic unknown). Generic LLM leaderboards do not answer this task; the golden set does.
- **Metrics (honesty is a gate, not a tradeoff).** Per output: **grounding / no-hallucination** (automatic —
  reuse the runtime guard: every digit and named entity in the output must appear in the input); **format
  valid** (auto: JSON + schema + length + language); **safety preserved** (auto: all computed warnings still
  present, none added); **faithful coverage** of the top score factors (auto-ish); **clarity 1–5** (human
  spot-check + optional LLM-as-judge, anchored by the automatic grounding score); **latency + cost**
  (operational). Run each case 3× at low temp — high variance ⇒ unreliable.
- **Model selection — scoreboard.** Run candidates (local Qwen2.5-7B / Llama-3.1-8B, DeepSeek, gpt-4o-mini, …)
  through the harness → table of `grounding% · format% · safety% · clarity · p50 ms · $/1k`. Rule: **gate
  first** (e.g. grounding ≥ 99%, format = 100%, safety = 100%), **then cheapest/fastest** clearing clarity ≥ 4
  — no matter how fluent a failing model sounds. The runtime guard makes a cheap local default safe.
- **Regression + production.** Keep the harness in `backend/eval/` as a CLI/pytest asserting **aggregate
  thresholds** (not exact strings); `FakeProvider` gives deterministic grader unit tests, a `--live` mode hits
  real models. The long-run signal is an **A/B of LLM- vs template-explained cards** against the existing
  `feedback_submitted` / `maps_opened` analytics keyed by `anonymous_id` (👍 rate, maps-open rate).
- *(Scaffolding `backend/eval/` + the golden-set fixture format is built with Feature A, not before.)*

### B. Anonymous identity (foundation)
- Frontend (`app.js`): generate a UUID in `localStorage` on first load; include `anonymous_id` in the bodies
  of `/api/recommendations`, `/api/feedback`, `/api/events` (extend `requestPayload()` and `track()`).
- `schemas.py`: add optional `anonymous_id` to `AdventureRequest`, `FeedbackRequest`, `AnalyticsEvent`.
- `storage.py`: add a `users` table (`id, anonymous_id UNIQUE, created_at, locale`), upsert on first sight;
  add an `anonymous_id` column to `search_sessions`, `feedback`, `events`. `main.py` passes it through.

### C. Recommendation history + delete
- `storage.py`: `history_for(anonymous_id, limit)` (recent sessions + their recommendations) and
  `delete_user_data(anonymous_id)` (sessions, recommendations, feedback, events).
- `main.py`: `GET /api/history?anonymous_id=…` (recent seen/opened — "opened" derived from
  `recommendation_opened`/`maps_opened` events) and `DELETE /api/history?anonymous_id=…`.
- Frontend: a "Recently seen" section listing past recommendations (title, score, date) + a **"Clear my
  history"** button; EN/RU i18n.

### D. Personal Preference Fit (Adventure Score v0.2)
- `storage.py`: `preference_profile(anonymous_id)` → net up/down counts aggregated by `place_type` and
  `interest` from the `feedback` + `recommendations` tables.
- `scoring.py`: new `_personal_preference_fit(place, profile)` → 0–100, **neutral (~70) on cold start**; reuse
  the existing `PLACE_INTERESTS` / `place.type` mapping. Add an optional `profile` arg to `score_candidate`;
  include PPF in the weighted sum; surface it in `ScoreBreakdown` and add a "why" bullet ("matches places you
  liked before"). Load the profile once per request (`recommendations.py`) by `anonymous_id` and thread it
  through.
- **Score v0.2 weights** (modest PPF so cold-start users are ~unchanged):
  Time 18 · Weather 18 · Distance 13 · Safety 14 · Group 9 · Interest 9 · Place Quality 9 · **Personal
  Preference 10** (= 100).

### E. Refined group inputs
- `schemas.py` (`AdventureRequest`): add `with_dog: bool`, `with_elderly: bool`, `reduced_mobility: bool` (keep
  `children_ages`, `max_walking_km`, `intensity`; keep `group_type` for back-compat or derive it).
- `scoring.py`: extend `_group_fit` / `_safety_fit` — `reduced_mobility` / `with_elderly` penalize
  hard/steep/long-walk options and add warnings; `with_dog` keeps the indoor/museum penalty.
- Frontend: replace the single Group dropdown with toggle chips (children +ages, dog, elderly, reduced
  mobility); EN/RU i18n; map into `requestPayload()`.

### F. Rewrite Spec.md
Apply all Part 1 edits: correct the stack (§14), replace the AI section with the provider-agnostic LLM design,
fold in 0.1.1 features (§8/§9.2/§16), reconcile group types (§7) and feedback reasons (§16), add Score v0.2
(§10), add anonymous identity + "delete my history" (§15/§23), mark 0.1/0.1.1 done and redefine 0.2 in the
Roadmap (§22), and descope named social scraping (§13/§18).

---

## Verification
- **Tests:** `cd backend && .venv/bin/python -m pytest`. New cases: LLM fallback to templates when a provider
  raises; PPF neutral on cold start and shifts ranking with a seeded profile; history + delete round-trip;
  refined-input penalties.
- **LLM (manual):** run with `LLM_PROVIDER=template` (no network) → identical to today; with
  `LLM_PROVIDER=llamacpp LLM_BASE_URL=http://localhost:8080/v1` against a local `llama-server` (and again with
  an OpenAI/DeepSeek/Groq key) → cards show LLM `summary`/`why`; kill the server mid-run → graceful fallback,
  no UI error. Feed a model a leading prompt and confirm the **grounding guard** drops invented facts and falls
  back to templates.
- **Personalization (manual):** open the app, down-vote several "fortress" picks, search again → fortress-type
  options rank lower and the breakdown shows Personal Preference Fit; "Recently seen" lists past results;
  "Clear my history" empties it (`GET /api/history` returns empty after `DELETE`).
- **Run:** `docker compose up --build` (UI + API at `http://localhost:8080`).

## Out of scope (0.3+)
Saved places / full Adventure Memory · photo diary · notifications / daily picks · Event Impact & live Traffic
Fit · Community Intelligence (and any social-network scraping) · accounts/login · Postgres/PostGIS.
