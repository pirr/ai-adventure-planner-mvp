# Provider quality benchmark

How much worse would recommendations get if discovery moved off the current
OSM + Google pipeline onto a cheaper hosted Places API? This harness answers that
with numbers. The **baseline/reference is the current OSM+Google output** — the
metrics measure how far each cheap provider drifts from it, and an optional
LLM-judge says whether the drift is actually *worse* or just *different*.

Providers compared (cheap hosted APIs): **Geoapify**, **LocationIQ**. Self-hosted
options (Overture/Foursquare) are out of scope for now.

## How it works

Each provider has an adapter (`providers/`) that returns `PlaceCandidate`s — the
same shape OSM/Google produce. Every provider's candidates are ranked through the
**production scoring** (`provider_orchestrator.rank_offline`): real
`score_candidate` weights, deterministic `fallback_route` instead of live OSRM,
and a fixed neutral weather so the only thing that varies is the place data. That
makes runs reproducible; provider responses are also cached under
`./data/provider_cache/` so re-runs don't re-bill.

Two arms per cheap provider:
- **provider_only** — the cheap provider alone (no Google).
- **provider_plus_google** — Google kept as an enricher (ratings/photos) on the
  top pool, exactly as production does. Needs `GOOGLE_PLACES_API_KEY`.

The baseline is a single fixed reference (its Google enrichment is intrinsic), so
it has no arm split.

## Running

Build first — the container has **no source mount**, so new eval code needs a
rebuild:

```bash
RATE_LIMIT_ENABLED=false docker compose up -d --build
```

```bash
# Offline wiring smoke (uses the response cache if present, else a provider key)
GEOAPIFY_API_KEY=... docker compose exec app \
  python -m eval.provider_benchmark --smoke --providers geoapify

# Objective sweep, both arms, cost column + JSON report
GEOAPIFY_API_KEY=... LOCATIONIQ_API_KEY=... docker compose exec app \
  python -m eval.provider_benchmark --max-cities 6 \
  --arms provider_only provider_plus_google --price --json provider_bench.json

# With the pairwise LLM-judge on a subset. A local llamacpp server costs nothing
# and runs offline; from INSIDE the container use host.docker.internal, not
# localhost (localhost is the container).
LLM_JUDGE_BASE_URL=http://host.docker.internal:8080/v1 LLM_JUDGE_MODEL=local-model \
  docker compose exec app python -m eval.provider_benchmark \
  --judge --judge-cities 6 --max-cities 6

# Category-coverage audit (where a provider's categories fail to map to app types)
docker compose exec app python -m eval.providers.category_audit --max-cities 6

# Unit tests (mock HTTP — no network)
docker compose exec app pytest \
  tests/test_provider_matching.py tests/test_provider_metrics.py tests/test_provider_adapters.py -v
```

Keys: `GEOAPIFY_API_KEY`, `LOCATIONIQ_API_KEY`. Judge: `LLM_JUDGE_BASE_URL`,
`LLM_JUDGE_MODEL`, optional `LLM_JUDGE_API_KEY`.

## Reading the numbers

Per provider × arm, averaged over (city × profile); the baseline row is `ref`.

| column | meaning |
|---|---|
| `n_cand`, `dens` | raw candidates found, and per-km² density |
| `rec@k` | share of the baseline's top-K the provider also has in its top-K |
| `recPool` | same, but against the provider's whole pool (discovery vs ranking gap) |
| `Jacc` | set overlap of the two top-K lists |
| `RBO` | rank-biased overlap — do they *order* the shared places alike (1−p^k for identical) |
| `tau` | Kendall τ on the matched subset (≥3 matches) |
| `%rate`/`%phot`/`%cat`/`%oh` | top-K carrying rating / photo / a mapped (non-fallback) type / opening hours |
| `cover` | share of requested interests the pool can satisfy |
| `dedup` | share of the pool that is duplicate points |
| `p50/p95ms` | discovery latency (only measured on live, non-cached fetches) |
| `$/1k` | discovery cost per 1000 calls (from `providers/pricing.json`) |
| judge `win%` | provider wins ÷ decided, blind A/B vs baseline, position-swapped; `unstable%` flags flips |

**Verdict heuristic.** A provider is "cheap but fine" when `rec@k` ≳ 0.7, judge
`win%` ≳ 45%, at far lower cost/latency; "too degraded" when `rec@k` ≲ 0.4 and
`win%` ≲ 30%. Compare a provider's `provider_only` vs `provider_plus_google` rows
to see exactly what Google enrichment buys back.

## Caveats

- **No ground truth.** Overlap is agreement-*with-Google*; the judge is there
  precisely because a provider can be different-but-good.
- **Category coverage.** Geoapify/LocationIQ map curated categories, not raw OSM
  tags; run the audit to see the fallback rate.
- **Free tiers.** A full sweep can approach Geoapify/LocationIQ free quotas
  (the harness caches and warns). LocationIQ Nearby fans out to several calls per
  scenario.
- **+Google cost / budget.** The `provider_plus_google` arm (and the baseline)
  spend real Google budget; per-scenario `anonymous_id`s avoid the per-user cap,
  but the global `GOOGLE_PLACES_DAILY_LIMIT` (default 800) still applies — raise
  it for large sweeps.
- **RU/Cyrillic** names lower name-match recall; the city list is Latin-script.
