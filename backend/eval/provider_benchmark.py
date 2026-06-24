"""Compare cheap hosted place providers against the OSM+Google baseline.

For each (city x profile), every provider's candidates are ranked through the
production scoring offline (see provider_orchestrator) and measured against the
baseline's ranked output. Each cheap provider is run in two arms — on its own,
and with Google kept as an enricher — so the table shows both how far it drifts
from the baseline and how much Google buys back. Optionally an LLM-judge gives a
blind win-rate vs the baseline.

Run via docker compose (build first — no source mount):

    RATE_LIMIT_ENABLED=false docker compose up -d --build

    # offline wiring smoke (uses ./data/provider_cache if present, else a key)
    GEOAPIFY_API_KEY=... docker compose exec app \
        python -m eval.provider_benchmark --smoke --providers geoapify

    # objective sweep, both arms, cost + JSON report
    GEOAPIFY_API_KEY=... LOCATIONIQ_API_KEY=... docker compose exec app \
        python -m eval.provider_benchmark --max-cities 6 --price --json provider_bench.json

    # with the pairwise LLM-judge (local llamacpp = zero API cost; use
    # host.docker.internal from inside the container, not localhost)
    LLM_JUDGE_BASE_URL=http://host.docker.internal:8080/v1 LLM_JUDGE_MODEL=local-model \
        docker compose exec app python -m eval.provider_benchmark --judge --judge-cities 6 --max-cities 6

See eval/PROVIDER_BENCHMARK.md for how to read the numbers.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from app.config import settings
from app.schemas import AdventureRequest, PlaceCandidate
from app.services import google_places
from app.services.places import _clone_candidates, radius_for_request
from app.services.scoring import normalize_interest
from eval import provider_metrics as pm
from eval.city_benchmark import CITIES
from eval.provider_judge import JudgeTally, judge_config, judge_scenario
from eval.provider_orchestrator import rank_offline, to_recommendations
from eval.providers.baseline import OsmGoogleBaselineProvider
from eval.providers.geoapify import GeoapifyProvider
from eval.providers.locationiq import LocationIQProvider

BASELINE_NAME = "osm_google"
ARMS = ["provider_only", "provider_plus_google"]
PROVIDERS = {"geoapify": GeoapifyProvider, "locationiq": LocationIQProvider}

# (id, transport, available_minutes, interests) — a small spread of trip shapes.
PROFILES = [
    ("car_history", "car", 240, ["history", "nature", "food"]),
    ("walk_views", "walk", 120, ["viewpoints", "history"]),
    ("bike_food", "bike", 120, ["food", "drinks"]),
    ("car_fortress", "car", 300, ["fortresses", "water"]),
]
SMOKE_PROFILES = PROFILES[:1]

CACHE_DIR = settings.data_dir / "provider_cache"
PRICING = json.loads((Path(__file__).parent / "providers" / "pricing.json").read_text())


@dataclass
class Acc:
    """Per-(provider, arm) metric samples across scenarios."""

    n_cand: list[float] = field(default_factory=list)
    density: list[float] = field(default_factory=list)
    recall: list[float] = field(default_factory=list)
    recall_pool: list[float] = field(default_factory=list)
    jaccard: list[float] = field(default_factory=list)
    rbo: list[float] = field(default_factory=list)
    kendall: list[float] = field(default_factory=list)
    rating: list[float] = field(default_factory=list)
    photo: list[float] = field(default_factory=list)
    category: list[float] = field(default_factory=list)
    opening_hours: list[float] = field(default_factory=list)
    coverage: list[float] = field(default_factory=list)
    dedup: list[float] = field(default_factory=list)
    latency_ms: list[float] = field(default_factory=list)
    judge: JudgeTally = field(default_factory=JudgeTally)


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _dump(candidate: PlaceCandidate) -> dict:
    # pydantic v2 prefers model_dump; the codebase still runs v1-style models.
    return candidate.model_dump() if hasattr(candidate, "model_dump") else candidate.dict()


# --- response cache (avoid re-billing / re-hitting providers across runs) -----

def _cache_path(provider: str, lat: float, lon: float, radius_km: float, interests: list[str]) -> Path:
    norm = "-".join(sorted(normalize_interest(str(i)) for i in interests)) or "none"
    key = f"{round(lat, 3)}_{round(lon, 3)}_{round(radius_km, 1)}_{norm}"
    return CACHE_DIR / provider / f"{key}.json"


async def _fetch(provider, lat, lon, radius_km, interests, anonymous_id, use_cache):
    """Return (candidates, latency_ms | None). latency is None on a cache hit."""
    path = _cache_path(provider.name, lat, lon, radius_km, interests)
    if use_cache and path.exists():
        data = json.loads(path.read_text())
        return [PlaceCandidate(**d) for d in data], None
    started = time.perf_counter()
    candidates = await provider.fetch(lat, lon, radius_km, interests, "en", anonymous_id)
    latency_ms = (time.perf_counter() - started) * 1000
    # Don't cache an empty result: a missing key or a transient miss would then
    # mask real data on the next run. Cache only a non-empty fetch.
    if candidates:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps([_dump(c) for c in candidates], ensure_ascii=False))
    return candidates, latency_ms


# --- one scenario -------------------------------------------------------------

async def _run_scenario(city, profile, providers, arms, k, n, use_cache, judge_cfg, repeats, acc):
    name, country, lat, lon = city
    pid, transport, minutes, interests = profile
    request = AdventureRequest(
        lat=lat, lon=lon, available_minutes=minutes, transport_mode=transport,
        interests=interests, limit=k, lang="en", use_live_data=True,
    )
    radius_km = radius_for_request(minutes, transport)
    base_anon = f"bench-baseline-{name}-{pid}"

    baseline = OsmGoogleBaselineProvider()
    base_pool, base_ms = await _fetch(baseline, lat, lon, radius_km, interests, base_anon, use_cache)
    base_scored = await rank_offline(_clone_candidates(base_pool), request, enrich=True, anonymous_id=base_anon)
    base_topk = [c.place for c in base_scored[:k]]
    base_recs = to_recommendations(base_scored, n)
    _record_baseline(acc, base_pool, base_topk, radius_km, interests, base_ms)

    for provider in providers:
        pool, latency_ms = await _fetch(provider, lat, lon, radius_km, interests, base_anon, use_cache)
        for arm in arms:
            anon = f"bench-{provider.name}-{arm}-{name}-{pid}"
            scored = await rank_offline(
                _clone_candidates(pool), request,
                enrich=(arm == "provider_plus_google"), anonymous_id=anon,
            )
            topk = [c.place for c in scored[:k]]
            a = acc[(provider.name, arm)]
            a.n_cand.append(len(pool))
            a.density.append(pm.density(len(pool), radius_km))
            a.recall.append(pm.recall_at_k(topk, base_topk))
            a.recall_pool.append(pm.recall_at_k(pool, base_topk))
            a.jaccard.append(pm.jaccard(topk, base_topk))
            a.rbo.append(pm.rank_overlap(topk, base_topk))
            tau = pm.kendall_tau(topk, base_topk)
            if tau is not None:
                a.kendall.append(tau)
            rich = pm.richness(topk)
            a.rating.append(rich["rating"]); a.photo.append(rich["photo"])
            a.category.append(rich["mapped_category"]); a.opening_hours.append(rich["opening_hours"])
            a.coverage.append(pm.interest_coverage(pool, interests))
            a.dedup.append(pm.dedup_rate(pool))
            if latency_ms is not None:
                a.latency_ms.append(latency_ms)
            if judge_cfg:
                provider_recs = to_recommendations(scored, n)
                for _ in range(repeats):
                    if provider_recs and base_recs:
                        a.judge.add(await judge_scenario(judge_cfg, request, base_recs, provider_recs))


def _record_baseline(acc, pool, topk, radius_km, interests, latency_ms):
    a = acc[(BASELINE_NAME, "baseline")]
    a.n_cand.append(len(pool))
    a.density.append(pm.density(len(pool), radius_km))
    a.recall.append(1.0); a.recall_pool.append(1.0); a.jaccard.append(1.0); a.rbo.append(1.0)
    rich = pm.richness(topk)
    a.rating.append(rich["rating"]); a.photo.append(rich["photo"])
    a.category.append(rich["mapped_category"]); a.opening_hours.append(rich["opening_hours"])
    a.coverage.append(pm.interest_coverage(pool, interests))
    a.dedup.append(pm.dedup_rate(pool))
    if latency_ms is not None:
        a.latency_ms.append(latency_ms)


# --- reporting ----------------------------------------------------------------

def _cost_per_1k(provider: str) -> float | None:
    entry = PRICING.get(provider)
    return entry["usd_per_query"] * 1000 if entry else None


def _fmt(value: float | None, spec: str = ".2f") -> str:
    return format(value, spec) if value is not None else "-"


def _pct(value: float | None) -> str:
    return f"{value * 100:.0f}%" if value is not None else "-"


def _row(label: str, a: Acc, show_price: bool, is_ref: bool) -> str:
    cells = [
        f"{label:<13}",
        f"{_fmt(_mean(a.n_cand), '.0f'):>6}",
        f"{_fmt(_mean(a.density), '.2f'):>6}",
        f"{_fmt(_mean(a.recall)):>6}",
        f"{_fmt(_mean(a.recall_pool)):>8}",
        f"{_fmt(_mean(a.jaccard)):>6}",
        f"{_fmt(_mean(a.rbo)):>6}",
        f"{_fmt(_mean(a.kendall)):>6}",
        f"{_pct(_mean(a.rating)):>6}",
        f"{_pct(_mean(a.photo)):>6}",
        f"{_pct(_mean(a.category)):>5}",
        f"{_pct(_mean(a.opening_hours)):>4}",
        f"{_pct(_mean(a.coverage)):>6}",
        f"{_fmt(_mean(a.dedup)):>6}",
        f"{_fmt(pm.percentile(a.latency_ms, 0.5), '.0f') if a.latency_ms else '-':>7}",
        f"{_fmt(pm.percentile(a.latency_ms, 0.95), '.0f') if a.latency_ms else '-':>7}",
    ]
    if show_price:
        name = BASELINE_NAME if is_ref else label.rstrip("*").strip()
        cells.append(f"{'ref' if is_ref else _fmt(_cost_per_1k(name), '.2f'):>6}")
    return "  ".join(cells)


def _header(show_price: bool) -> str:
    cols = (f"{'provider':<13}  {'n_cand':>6}  {'dens':>6}  {'rec@k':>6}  {'recPool':>8}  "
            f"{'Jacc':>6}  {'RBO':>6}  {'tau':>6}  {'%rate':>6}  {'%phot':>6}  {'%cat':>5}  "
            f"{'%oh':>4}  {'cover':>6}  {'dedup':>6}  {'p50ms':>7}  {'p95ms':>7}")
    if show_price:
        cols += f"  {'$/1k':>6}"
    return cols


def _print_report(acc, providers, arms, show_price, judge_on):
    for arm in arms:
        print(f"\n=== Provider benchmark (arm: {arm}) ===")
        print(_header(show_price))
        print(_row(f"{BASELINE_NAME}*", acc[(BASELINE_NAME, "baseline")], show_price, is_ref=True))
        for provider in providers:
            print(_row(provider.name, acc[(provider.name, arm)], show_price, is_ref=False))
        print("(* baseline = OSM+Google reference; rec/Jacc/RBO are vs this row)")

    if judge_on:
        print("\n=== Pairwise LLM-judge vs baseline (provider wins / decided) ===")
        print(f"{'provider':<13}  {'arm':<20}  {'win%':>6}  {'wins':>5}  {'loss':>5}  {'tie':>5}  {'unstable%':>9}  {'n':>4}")
        for arm in arms:
            for provider in providers:
                t = acc[(provider.name, arm)].judge
                if t.n == 0:
                    continue
                print(f"{provider.name:<13}  {arm:<20}  {_pct(t.win_rate):>6}  {t.provider:>5}  "
                      f"{t.baseline:>5}  {t.tie:>5}  {_pct(t.unstable_pct):>9}  {t.n:>4}")


def _check_free_tier(providers, n_scenarios, repeats):
    """Warn when a sweep risks blowing a provider's free daily quota."""
    for provider in providers:
        entry = PRICING.get(provider.name)
        if not entry or entry.get("free_daily_quota", 0) <= 0:
            continue
        # Geoapify ~1 call/scenario; LocationIQ fans out to several. Use a rough upper bound.
        per = 6 if provider.name == "locationiq" else 1
        calls = n_scenarios * per
        if calls > entry["free_daily_quota"]:
            print(f"WARNING: {provider.name} ~{calls} calls > free tier {entry['free_daily_quota']}/day "
                  f"(cache reuses across runs; lower --max-cities or pace requests).")


def _to_json(acc, providers, arms, judge_on) -> dict:
    def block(a: Acc) -> dict:
        out = {
            "n_cand": _mean(a.n_cand), "density": _mean(a.density),
            "recall_at_k": _mean(a.recall), "recall_pool": _mean(a.recall_pool),
            "jaccard": _mean(a.jaccard), "rbo": _mean(a.rbo), "kendall_tau": _mean(a.kendall),
            "pct_rating": _mean(a.rating), "pct_photo": _mean(a.photo),
            "pct_mapped_category": _mean(a.category), "pct_opening_hours": _mean(a.opening_hours),
            "interest_coverage": _mean(a.coverage), "dedup_rate": _mean(a.dedup),
            "latency_p50_ms": pm.percentile(a.latency_ms, 0.5) if a.latency_ms else None,
            "latency_p95_ms": pm.percentile(a.latency_ms, 0.95) if a.latency_ms else None,
        }
        if judge_on and a.judge.n:
            out["judge"] = {
                "win_rate": a.judge.win_rate, "provider_wins": a.judge.provider,
                "baseline_wins": a.judge.baseline, "ties": a.judge.tie,
                "unstable_pct": a.judge.unstable_pct, "n": a.judge.n,
            }
        return out

    result: dict = {"baseline": block(acc[(BASELINE_NAME, "baseline")]), "arms": {}}
    for arm in arms:
        result["arms"][arm] = {p.name: block(acc[(p.name, arm)]) for p in providers}
    return result


async def _run(args) -> None:
    from collections import defaultdict

    cities = CITIES[: args.max_cities] if args.max_cities else CITIES
    if args.smoke:
        cities, profiles, arms, judge_on = cities[:1], SMOKE_PROFILES, ["provider_only"], False
    else:
        profiles = SMOKE_PROFILES if args.profiles == "smoke" else PROFILES
        arms = args.arms
        judge_on = args.judge
    providers = [PROVIDERS[name]() for name in args.providers]
    judge_cfg = judge_config() if judge_on else None
    judge_on = judge_on and judge_cfg is not None

    print(f"Provider benchmark: {len(cities)} cities x {len(profiles)} profiles x {len(providers)} providers "
          f"x {len(arms)} arms  (k={args.k}, n={args.n})")
    if not google_places.enabled():
        print("NOTE: no GOOGLE_PLACES_API_KEY — baseline is OSM-only and the +Google arm is a no-op.")
    _check_free_tier(providers, len(cities) * len(profiles), args.repeats)

    acc: dict = defaultdict(Acc)
    judge_cities = set(range(args.judge_cities)) if judge_on else set()
    for ci, city in enumerate(cities):
        for profile in profiles:
            scenario_judge = judge_cfg if (judge_on and ci in judge_cities) else None
            await _run_scenario(city, profile, providers, arms, args.k, args.n,
                                not args.no_cache, scenario_judge, args.repeats, acc)
        print(f"  done {city[0]}, {city[1]}")

    _print_report(acc, providers, arms, args.price, judge_on)
    if args.json:
        Path(args.json).write_text(json.dumps(_to_json(acc, providers, arms, judge_on), indent=2, default=str))
        print(f"\nWrote {args.json}")


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--providers", nargs="+", default=list(PROVIDERS), choices=list(PROVIDERS))
    p.add_argument("--arms", nargs="+", default=ARMS, choices=ARMS)
    p.add_argument("--max-cities", type=int, default=0, help="Only the first N cities (0 = all)")
    p.add_argument("--profiles", choices=["all", "smoke"], default="all")
    p.add_argument("--k", type=int, default=5, help="Top-K compared against the baseline")
    p.add_argument("--n", type=int, default=5, help="Recommendation-set size shown to the judge")
    p.add_argument("--judge", action="store_true", help="Enable the pairwise LLM-judge (needs LLM_JUDGE_* env)")
    p.add_argument("--judge-cities", type=int, default=6, help="Run the judge on the first N cities only")
    p.add_argument("--repeats", type=int, default=1, help="Judge repeats per scenario (variance)")
    p.add_argument("--price", action="store_true", help="Show the $/1k cost column from providers/pricing.json")
    p.add_argument("--no-cache", action="store_true", help="Ignore the response cache and hit providers live")
    p.add_argument("--smoke", action="store_true", help="1 city x 1 profile, provider_only, no judge")
    p.add_argument("--json", default="", help="Write a machine-readable report to this path")
    return p.parse_args(argv)


def main(argv=None) -> None:
    asyncio.run(_run(parse_args(argv)))


if __name__ == "__main__":
    main()
