# Deferred LLM explanations — cut recommendation latency ~10×

**Date:** 2026-06-22
**Status:** Approved design, pending implementation plan
**Related:** `docs/superpowers/specs/2026-06-09-in-card-loading-design.md`

## Context

The goal is to support 50+ users with fewer "it's slow" complaints, without
raising infrastructure spend above ~€50/month (today it is ~€5/month: one
512 MB Fly Machine that scales to zero).

Real production telemetry (`recommendations_timing` log lines, Frankfurt
region, warm cache) shows where a request's ~5.5 s actually goes:

| Stage | Duration |
|---|---|
| weather (origin) | 60–89 ms |
| places (Overpass) | **2–3 ms** (cache hit) |
| prefilter | 4–13 ms |
| routing (OSRM) | 63–104 ms |
| destination_forecasts | 52–60 ms |
| google_enrichment | 52–61 ms |
| **explain (LLM)** | **4921–5361 ms** |
| **total** | **5450–6208 ms** |

**Finding:** the single batched LLM explanation call
(`openai_compat.py:explain` — one request writes all cards' prose) is ~90% of
the wall-clock time. Everything else combined is ~300–500 ms. Overpass is *not*
the bottleneck here; its in-process cache is doing its job.

The explanation prose only feeds **two small areas of each card**
(`frontend/app.js:buildCard`):

- `.description` — the one-line summary under the title (`item.summary`)
- `.why` — the "Why now" bullet list (`item.why` + `data_confidence_note`)

Every other element (title, photo, Google rating, Adventure Score, score
breakdown, badges, arrival weather, map links, warnings, feedback/want-to-visit
buttons) is computed without the LLM and is ready in ~0.5 s. Crucially,
`scoring.to_recommendation` already fills rule-based `description` and `why`
*before* the LLM runs; `explain_recommendations` only **overwrites**
`summary`/`why`/`data_confidence_note` when the model output passes the
grounding guard.

So the user waits ~5 s for prose layered on top of an answer that was already
complete and usable in half a second.

## Goals

- **Perceived time-to-cards (p50) < 1 s** on a warm Machine (down from ~5.5 s).
- AI prose **settles into the two card areas in < ~4 s** after the cards render.
- **No infrastructure change** (stays ~€5/month); LLM call volume unchanged or
  lower (a user who leaves before prose loads costs zero LLM calls).
- **Graceful**: any LLM failure/timeout/expiry leaves the rule-based text in
  place — the card is never broken or empty.

## Non-goals (explicit, to keep this a single plan)

- **Cold-start Overpass latency.** Scale-to-zero wipes the in-process Overpass
  cache, so the *first* request after idle still pays ~8 s on Overpass. That is
  real and grows with more users, but it is a separate concern from the measured
  LLM bottleneck. It gets its own spec (persistent/cross-restart place cache, or
  `min_machines_running = 1`). Called out here so it is not forgotten.
- **Streaming / SSE.** Considered and rejected in favor of the simpler
  two-phase fetch below (variant "1B").
- Any change to scoring, ranking, or which places are returned.

## Approach: decouple explanations, shimmer-then-fill ("1B")

`/api/recommendations` stops blocking on the LLM. It returns the ranked cards
immediately with their rule-based text, plus a flag saying prose is coming. The
frontend renders the full cards, shows a subtle shimmer over **only** the
`.description` and `.why` areas, and makes a second call to a new
`/api/explanations` endpoint that runs the existing LLM explanation step and
returns the prose. The shimmering areas fill in once (with a gentle fade); they
never mutate under the user's eyes, and they fall back to the already-shown
rule-based text if the LLM does not deliver.

### UX timeline

```
t ≈ 0.5 s — full card, shimmer on two areas      t ≈ 3–4 s — fills once, fade-in
┌───────────────────────────────────────┐        ┌───────────────────────────────────────┐
│ [photo]                 ★ 4.6 (1,204)  │        │ [photo]                 ★ 4.6 (1,204)  │
│ Old Town Fortress                      │        │ Old Town Fortress                      │
│ ░░░░ writing why this fits ░░░░        │  ───▶  │ A sunset-friendly fortress walk that   │
│ Best fit now · Score 87                │        │ fits your 3h with room to spare.       │
│ [45m][travel 18][walk 0.6km][easy]     │        │ Best fit now · Score 87                │
│ ☀ 22°C on arrival   [ Start route → ]  │        │ [45m][travel 18][walk 0.6km][easy]     │
│ Why now                                │        │ ☀ 22°C on arrival   [ Start route → ]  │
│ ░░░░░░░░░░░░░░░░░░░░░░░░                │        │ Why now                                │
│ ░░░░░░░░░░░░░░░░░░░░                    │        │ ✓ You arrive ~18 min out, well within 3h│
└───────────────────────────────────────┘        │ ✓ Clear and 22°C when you get there     │
                                                  └───────────────────────────────────────┘
```

Only `.description` and `.why` shimmer; everything else is live and tappable at
0.5 s.

## Architecture & data flow

```
POST /api/recommendations
  build_recommendations(..., defer_explanations=True)
    ├─ run pipeline → `recommendations` list (rule-based summary/why already set)
    ├─ explainer = provider or _explainer_provider(request)   # A/B-bucketed
    ├─ if recommendations and not TemplateProvider:
    │     explanations.stash(request_id, recommendations, request)   # in-proc, TTL
    │     response.explanations_pending = True
    └─ else: response.explanations_pending = False             # no shimmer, no 2nd call
  save_response(...)                                           # persists rule-based text
  → returns fast (~0.5 s)

Frontend renders cards. If explanations_pending: shimmer + POST /api/explanations.

POST /api/explanations { request_id, lang, anonymous_id }
  explanations.resolve(request_id)
    ├─ pop/peek stash by request_id  (miss/expired → return [])
    ├─ explainer = _explainer_provider(request)   # deterministic from anonymous_id
    ├─ explain_recommendations(recs, request, explainer)   # existing code, grounding guard
    └─ return [{id, summary, why, data_confidence_note} for grounded recs]
  → frontend fills the two areas, clears shimmer on all pending cards
```

Net LLM calls per recommendation request: still **one** (now triggered by the
second fetch), so cost is unchanged — and a user who navigates away before the
second fetch fires saves that call.

## Backend changes

### 1. `app/services/explanations.py` (new) — in-process stash + resolver

Mirrors the existing `places.py` `_candidate_cache` pattern (TTL dict, expired
sweep on write, max-entries cap).

- `stash(request_id: str, recommendations: list[Recommendation], request: AdventureRequest) -> None`
  — store `(expires_at, recommendations, request)` keyed by `request_id`.
- `resolve(request_id: str) -> list[dict]` — look up the stash (return `[]` on
  miss/expired), recompute `explainer = _explainer_provider(request)`, call the
  **existing** `explain_recommendations(recs, request, explainer)`, then return
  one entry per recommendation that ended up with grounded prose:
  `{"id", "summary", "why", "data_confidence_note"}`. Idempotent within the TTL
  (safe to retry).
- Config: `EXPLANATION_STASH_TTL_SECONDS` (default `300`),
  `EXPLANATION_STASH_MAX_ENTRIES` (default `512`).

`_explainer_provider` is deterministic from `anonymous_id`, so the second call
selects the same A/B bucket as the first — no need to stash the provider.

**Avoid a circular import:** `recommendations.py` will import `explanations.py`
(to `stash`), so `explanations.py` must not import `_explainer_provider` from
`recommendations.py` at module load. Either move `_explainer_provider` (and its
`_ab_bucket` helper) into a small shared module (e.g. `app/services/llm/ab.py`)
that both import, or import it lazily inside `resolve`. Relocating is cleaner.

### 2. `app/services/recommendations.py` — stop blocking on the LLM

- Add parameter `defer_explanations: bool = True` to `build_recommendations`.
- Replace the inline `await explain_recommendations(...)` (lines ~227–235) with:
  - compute `explainer = provider if provider is not None else _explainer_provider(request)`
  - **if `defer_explanations` and `recommendations` and not `TemplateProvider`:**
    `explanations.stash(request_id, recommendations, request)` and set the new
    response flag `explanations_pending = True`.
  - **else (back-compat / eval):** keep today's behavior — `await
    explain_recommendations(...)` inline, `explanations_pending = False`.
- Keep the `stage=total` timing log; it will now exclude the LLM wait.
- Kill-switch: `DEFER_EXPLANATIONS` config (default `true`). When `false`,
  always take the inline branch (exact current behavior).

### 3. `app/services/weather.py` call site — parallelize the two independent calls (minor)

In `build_recommendations`, run `get_weather(...)` and `get_candidate_places(...)`
concurrently via `asyncio.gather` instead of sequentially (they are independent;
saves ~60–90 ms). Keep the existing per-stage timing logs by timing the gather.

### 4. `app/schemas.py` — response flag

Add `explanations_pending: bool = False` to `AdventureResponse`. Default keeps
any non-deferred caller correct.

### 5. `app/main.py` — new endpoint

```python
@app.post("/api/explanations")
@limiter.limit(settings.rate_limit_explanations)
async def explanations(request: Request, payload: ExplanationsRequest) -> dict:
    items = await explanations_service.resolve(payload.request_id)
    return {"request_id": payload.request_id, "explanations": items}
```

- New `ExplanationsRequest` schema: `{ request_id: str, lang: str = "en",
  anonymous_id: str | None = None }`.
- Unknown/expired `request_id` → `200` with `{"explanations": []}` (no error
  branch on the client; shimmer simply clears).
- New config `rate_limit_explanations` (default `"20/minute;200/day"` — roughly
  2× the recommendations limit, since it is at most one call per recs call).
- No auth requirement; mirror the light CSRF-only-if-session handling used by
  the other POSTs if a session is present.

### 6. `app/services/llm/openai_compat.py` — trims (secondary, recommended)

Now that the explanation call is off the critical path, these reduce token cost
and how fast the prose settles:

- Thread an optional `explain_max_tokens` (config `LLM_EXPLAIN_MAX_TOKENS`,
  default `700`) into `_body` so the explanation request caps generation. The
  `parse_situation` path is unchanged.
- Drop Gemini `reasoning_effort` for explanations in production (set
  `GEMINI_REASONING_EFFORT` empty/`none`). Code already only sends it when set.
- Tighten `_SYSTEM_PROMPT` to **2–3** `why` bullets and a ~24-word summary cap.

These are optional polish; the decoupling above is the core win.

## Frontend changes (`frontend/app.js`, `frontend/styles.css`)

1. **Response handling:** read `explanations_pending` from the recommendations
   response; stash it alongside `lastRequestId`.
2. **Shimmer:** in `buildCard`, when prose is pending for this request, add a
   `is-pending-explanation` class to the `.description` and `.why` nodes (CSS
   shimmer skeleton + an accessible "writing why this fits…" label).
3. **`fetchExplanations(requestId)`:** `POST /api/explanations` with
   `{ request_id, lang, anonymous_id }` (+ CSRF header if logged in). On
   response, for each entry find the card by `data-id` and, if it still belongs
   to the current `lastRequestId`:
   - if `summary` present, replace `.description` text;
   - replace `.why` bullets (and `data_confidence_note` line) when present;
   - fade in.
   Then remove the shimmer class from **all** pending cards of that request
   (including any with no entry — they keep rule-based text).
4. **Failure/empty/timeout:** if the call errors, returns `[]`, or exceeds a
   client timeout (~15 s), clear the shimmer and keep rule-based text.
5. **Staleness guard:** ignore an explanations response whose `request_id` no
   longer matches the displayed results (user searched again).
6. **Load-more:** the `loadMoreRecommendations` path uses the same endpoint and
   its own `request_id`; trigger `fetchExplanations` for the appended cards too.

## Error handling & edge cases

- **Template provider / A/B control bucket / LLM disabled:** `explanations_pending
  = false` → no shimmer, no second call.
- **Machine stopped between the two calls:** stash lost → `/api/explanations`
  returns `[]` → shimmer clears, rule-based stays. (Calls are seconds apart on a
  warm Machine, so this is rare.)
- **Ungrounded model output for a card:** that card is omitted from the response
  list → keeps rule-based text.
- **All models fail:** `explain_recommendations` already substitutes
  rule-based explanations (with an honest "AI explanation unavailable" note);
  returned as-is, shimmer clears.
- **Persistence:** `save_response` stores the rule-based text (accepted; the AI
  prose is a client-side enhancement). Feedback/analytics key off the stable
  pre-LLM `recommendation_id`, so they are unaffected.

## Testing

Backend (pytest in the shipped image — rebuild first, no source mount):

- `/api/recommendations` returns `explanations_pending=True` with rule-based
  text and **does not call the LLM inline** (mock provider asserts not invoked).
- `explanations_pending=False` for template provider and for the A/B control
  bucket.
- `/api/explanations` with a valid stashed `request_id` returns grounded prose;
  with an unknown/expired id returns `{"explanations": []}`.
- Stash TTL + max-entries eviction unit test.
- A/B determinism: same `anonymous_id` → same bucket across both endpoints.
- Trims: explanation request body carries `max_tokens`; `reasoning_effort`
  omitted when unset; `parse_situation` body unchanged.
- Back-compat: `build_recommendations(defer_explanations=False)` and the eval
  harness still apply explanations inline.

Frontend (Playwright; Docker `--build`, LAN IP not localhost, bump `?v=`,
`unrouteAll` stale stubs):

- After a search, cards render; `.description`/`.why` shimmer, then fill once;
  shimmer removed on all cards.
- LLM-off mode: no shimmer, no `/api/explanations` request.
- Stubbed `/api/explanations` failure → shimmer clears, rule-based text remains.
- Load-more path resolves explanations for appended cards.

## Success criteria (verification)

- Re-pull `recommendations_timing`: `stage=total` p50 < 1 s on a warm Machine.
- Browser: cards interactive in < 1 s; prose settles in < ~4 s.
- Infra unchanged (~€5/month); LLM call volume unchanged or lower.
- Forced LLM failure leaves usable rule-based cards.

## Rollout

1. Ship behind `DEFER_EXPLANATIONS=true` (default). Setting it `false` instantly
   reverts to today's inline behavior — a zero-risk kill switch.
2. After verifying latency in production, apply the optional LLM trims
   (`LLM_EXPLAIN_MAX_TOKENS`, clear `GEMINI_REASONING_EFFORT`).
3. Open the follow-up spec for cold-start Overpass latency.
