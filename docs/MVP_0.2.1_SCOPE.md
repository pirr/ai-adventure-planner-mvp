# MVP 0.2.1 — Close the v0.2 gaps (LLM evaluation & hardening)

## Context

All six v0.2 features (A–F) shipped. The parts of the scope that were **described but only scaffolded** are
Feature A's "Prompts & model evaluation" methodology, and the robustness logic later added to
`backend/app/services/llm/openai_compat.py` (retries, fallback models, Gemini handling) has **no tests**.
This milestone closes those gaps on the **`v0.2.1`** branch.

Gaps vs `docs/MVP_0.2_SCOPE.md` (§137–164):
- `backend/eval/` has 2 golden fixtures (scope wanted 20–50 + an adversarial slice); `run.py` reports only
  grounding%/returned%/latency — missing format-valid, safety-preserved, faithful-coverage, 3×-variance, cost.
- No multi-model **scoreboard**; no **LLM-as-judge** clarity; no **production A/B** (LLM vs template).
- No unit tests for the OpenAI-compatible client's retries/backoff, fallback models, `parse_explanations`,
  list-parts content, Gemini `response_format` skip, or rule-based fallback.

---

## Workstream 1 — Expand the eval harness

**Fixtures (`backend/eval/make_golden.py`).** Parameterize `SCENARIOS` to generate ~20 fixtures offline
(`use_live_data=False`, deterministic sample places) across transport walk/car/bike; available 30/120/300;
contexts solo / family+children / dog / elderly / reduced_mobility; interests history-fortresses /
nature-water / food / surprise; **EN + RU**. Write to `golden/`.
**Adversarial slice (`golden/adversarial/`).** Variants with `arrival_weather`, `forecast` and `warnings`
stripped, so a model is tempted to invent weather/risks — the grader checks it says "unavailable" instead.

**Metrics (`backend/eval/run.py`).** Keep grounding (reuse `guard.is_grounded`) and add, all automatic:
- `format_valid` — summary non-empty, `why` a list of 1–4 items, `data_confidence_note` present.
- `safety_preserved` — when `rec.warnings` is non-empty, no safety-negation phrase from a small EN/RU denylist.
- `faithful_coverage` — `why` has ≥2 bullets and references ≥1 input fact (a fact number or a title token).
- `--repeats N` (default 1; 3 for live) → grounded-rate variance per fixture.
- `cost_estimate` — tokens ≈ chars/4 (prompt+output) × a configurable `$/1k`, clearly labeled an estimate.
Print an aggregate summary table; keep `--live` and the template baseline.

## Workstream 2 — Model scoreboard + LLM-judge

- **Scoreboard (`run.py --scoreboard`).** Read `backend/eval/models.json` (committed, **no keys** — each entry
  `{name, base_url, model, api_key_env}`; key pulled from the named env var). Build an
  `OpenAICompatibleProvider` per entry, run the golden set, print one row per model:
  `grounding% · format% · safety% · coverage% · clarity · p50 ms · est $/1k`. README documents the rule:
  **gate** (grounding ≥ 99%, format = 100%, safety = 100%) then cheapest/fastest.
- **LLM-judge (`backend/eval/judge.py`, `--judge`).** Send `(facts, explanation)` to a judge model (config via
  `LLM_JUDGE_BASE_URL/MODEL/API_KEY`, reusing the OpenAI-compat HTTP path) for a clarity score 1–5 as JSON.
  Off by default; clarity is only trusted alongside the automatic grounding gate.

## Workstream 3 — Production A/B (LLM vs template)

- **Bucketing (`recommendations.py` + `config.py`).** Add `ab_test_enabled` (default false). When on and an LLM
  provider is configured, bucket deterministically by `hash(anonymous_id) % 2`: control → force
  `TemplateProvider()`, treatment → the configured provider.
- **Record variant (`storage.py`).** Add an `explainer` column to `search_sessions` (migrate via the existing
  `_ensure_column`); in `save_response` derive `explainer = "llm" if any(r.summary …) else "template"`.
- **Compare (`storage.ab_summary()` + `GET /api/ab`).** Group sessions by `explainer`, join `feedback`/`events`
  by `request_id`, return per variant: sessions, 👍 rate, maps-opened rate, feedback rate. Mirror
  `events_summary()` / `feedback_summary()`.

## Workstream 4 — Robustness unit tests (`backend/tests/test_openai_compat.py`)

Pure functions (no mocks): `parse_explanations` (by-id match, order fallback, word/char limits, fenced JSON,
non-list → all None), `_models()` (filters empty/dupes, order), `_content_from_response` (string and
list-of-parts, errors), `_body()` (Gemini omits `response_format`; non-Gemini includes it; `reasoning_effort`
only when set), `_retry_delay` (honors `Retry-After`).
Network paths via **`httpx.MockTransport`** (monkeypatch `openai_compat.http_client` to yield an
`AsyncClient(transport=MockTransport(handler))`): 429-then-200 succeeds; non-retryable 400 fails over to the
next model; all models failing → `build_rule_based_explanations` when `rule_based_fallback=True`.

*Optional (minor):* extend `storage.preference_profile` to also aggregate by interest, not just `place_type`.

## Verification
- `docker compose run --rm --no-deps app python -m pytest -q` — all tests green.
- `… python -m eval.make_golden` then `… python -m eval.run` shows the new metric table; `golden/` +
  `golden/adversarial/` populated.
- Live (needs a key): `LLM_PROVIDER=gemini … python -m eval.run --live --repeats 3`; `… --scoreboard`.
- A/B: `AB_TEST_ENABLED=true`, a few searches with different `anonymous_id`s, `GET /api/ab` returns per-variant
  rates. All via docker compose (no local venv).
