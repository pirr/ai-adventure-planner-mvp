# LLM explanation eval

A task-specific harness for the explanation layer. Generic model leaderboards
don't tell you whether a model invents facts about *your* recommendations — this
does, against frozen fixtures of your own computed outputs. (Run via docker
compose; `python` below = inside the container.)

## Golden fixtures

`golden/*.json` — each is `{request, recommendations}` captured from a real run
(20 across transport / time / group context / interests / EN+RU). `golden/adversarial/*.json`
are the same with weather, forecast and warnings stripped, to test that a model
says "unavailable" instead of inventing them. Regenerate with:

```bash
python -m eval.make_golden
```

## Running

```bash
python -m eval.run                                   # template baseline (offline)

LLM_PROVIDER=gemini LLM_MODEL=gemini-2.5-flash LLM_API_KEY=... \
  python -m eval.run --live --repeats 3              # grade a real model, 3x for variance

python -m eval.run --live --price-per-1k 0.30        # add a rough cost estimate
```

## Metrics

Per explanation, all automatic:
- **grounded** — introduces no number absent from the computed facts (the same
  `is_grounded` guard used at runtime). **The gate.**
- **format** — non-empty summary + a `why` list of 1–4 items.
- **safety** — when the card has computed warnings, the text doesn't negate them.
- **coverage** — ≥2 `why` bullets that reference a real input fact.
- **latency** p50, and an optional **cost** estimate (`--price-per-1k`).
- **variance** (`--repeats N`) — fixtures whose grounding is inconsistent across runs.

Clarity (1–5) is not automatic — see the LLM-judge (`--judge`) and the model
**scoreboard** (`--scoreboard`).

## City readiness benchmark (separate tool)

`eval.city_benchmark` is not about the LLM layer — it's a pre-launch check that a
first-time visitor in any major city gets live results, fast. It POSTs
`/api/recommendations` (`use_live_data=true`) for 18 US/UK/EU/AU cities and fails
any that exceed the latency budget or come back empty (the "No live places found"
state). It needs the app **serving** (it makes real HTTP calls), and exits
non-zero if any city fails, so it can gate a launch.

```bash
# local app, rate limiting off so the sweep isn't throttled
RATE_LIMIT_ENABLED=false docker compose up -d --build
docker compose exec app python -m eval.city_benchmark --delay 0

# deployed app — the default --delay paces under the live 10/min limit
docker compose exec app python -m eval.city_benchmark \
  --base-url https://adventure-planner-mvp.fly.dev --json city_readiness.json
```

Note: the live app caches Overpass per area, so the *first* hit on a cold city
is slow (or times out) and warms within a request or two — re-run, or pre-warm,
before reading the numbers as steady-state.

## Model selection

Treat grounding/format/safety as a **gate, not a tradeoff**: drop any model below
the threshold (e.g. grounding ≥ 99%, format = 100%, safety = 100%) no matter how
fluent it sounds, then pick the cheapest/fastest survivor. The runtime guard means
a model that occasionally slips still degrades safely to templates.
