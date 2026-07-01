"""Pre-launch readiness benchmark: every major city must return live results, fast.

Mirrors exactly what a first-time visitor sees. For a fixed set of major
US / UK / EU / AU cities it POSTs /api/recommendations with `use_live_data=true`
and asserts, per city:

  (1) latency  < --max-seconds   (default 5.0s), and
  (2) results >= --min-results   (default 1) — an empty list is precisely what
      the UI renders as "No live places found" (frontend renderCards()).

This is the executable form of the launch-readiness check in
docs/marketing/2026-06_PROMOTION_PLAN.md ("прогнать 15-20 крупных городов:
везде результаты < 5 сек, без 'No live places found'"). It does NOT mock the
network — the whole point is to exercise live OSM/Overpass, routing and weather
for real coordinates, which is where empty results and slow tails come from.

Run via docker compose (the app must already be serving):

    # local app, rate limiting off so an 18-city sweep isn't throttled
    RATE_LIMIT_ENABLED=false docker compose up -d --build
    docker compose exec app python -m eval.city_benchmark --delay 0

    # deployed app — pace under the live 10/min limit (the default --delay does this)
    docker compose exec app python -m eval.city_benchmark \
        --base-url https://adventure-planner-mvp.fly.dev

    # quick smoke test of the first few cities
    docker compose exec app python -m eval.city_benchmark --max-cities 3 --delay 0

Exits non-zero if any city fails, so it can gate a launch in CI.

Note on rate limits: /api/recommendations is 10/minute;100/day per IP. The
default --delay (6.5s) keeps a full sweep under the per-minute cap; the per-day
cap allows ~5 full sweeps/day against the live app.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

# (name, country, lat, lon) — dense city centres, where live OSM data is richest.
# If a place search comes back empty *here*, it's an infrastructure problem
# (Overpass timeout / mirror down), not genuine sparsity.
CITIES: list[tuple[str, str, float, float]] = [
    ("New York", "US", 40.7128, -74.0060),
    ("Los Angeles", "US", 34.0522, -118.2437),
    ("Chicago", "US", 41.8781, -87.6298),
    ("San Francisco", "US", 37.7749, -122.4194),
    ("Seattle", "US", 47.6062, -122.3321),
    ("London", "UK", 51.5074, -0.1278),
    ("Edinburgh", "UK", 55.9533, -3.1883),
    ("Manchester", "UK", 53.4808, -2.2426),
    ("Paris", "FR", 48.8566, 2.3522),
    ("Berlin", "DE", 52.5200, 13.4050),
    ("Amsterdam", "NL", 52.3676, 4.9041),
    ("Barcelona", "ES", 41.3851, 2.1734),
    ("Rome", "IT", 41.9028, 12.4964),
    ("Vienna", "AT", 48.2082, 16.3738),
    ("Prague", "CZ", 50.0755, 14.4378),
    ("Lisbon", "PT", 38.7223, -9.1393),
    ("Sydney", "AU", -33.8688, 151.2093),
    ("Melbourne", "AU", -37.8136, 144.9631),
]

# Broad, common interests so the benchmark tests live-data *health*, not the
# sparsity of one niche interest. Override with --interests.
DEFAULT_INTERESTS = ["nature", "viewpoints", "history", "food"]


@dataclass
class CityResult:
    name: str
    country: str
    samples_ms: list[float] = field(default_factory=list)  # latency of each repeat
    results: int = 0  # recommendations on the last successful sample
    live: int = 0  # of those, how many had live/mixed data confidence
    status: int = 0  # last HTTP status (0 = transport error)
    error: str = ""  # populated on failure
    ok: bool = False

    @property
    def latency_ms(self) -> float:
        # Report the slowest repeat — readiness means the *worst case* is fast.
        return max(self.samples_ms) if self.samples_ms else float("inf")


def build_payload(args: argparse.Namespace, lat: float, lon: float) -> dict[str, Any]:
    return {
        "lat": lat,
        "lon": lon,
        "available_minutes": args.minutes,
        "transport_mode": args.transport,
        "intensity": args.intensity,
        "interests": args.interests,
        "use_live_data": True,  # the entire point — never benchmark the offline path
        "limit": 5,
        "lang": "en",
    }


async def run_city(
    client: httpx.AsyncClient, args: argparse.Namespace, name: str, country: str, lat: float, lon: float
) -> CityResult:
    res = CityResult(name=name, country=country)
    payload = build_payload(args, lat, lon)
    for attempt in range(args.repeats):
        if attempt and args.delay:
            await asyncio.sleep(args.delay)
        started = time.perf_counter()
        try:
            resp = await client.post("/api/recommendations", json=payload)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            res.error = f"{type(exc).__name__}: {exc}"
            return res
        elapsed_ms = (time.perf_counter() - started) * 1000
        res.samples_ms.append(elapsed_ms)
        res.status = resp.status_code
        if resp.status_code == 429:
            res.error = "rate-limited (429) — raise --delay or set RATE_LIMIT_ENABLED=false"
            return res
        if resp.status_code != 200:
            res.error = f"HTTP {resp.status_code}: {resp.text[:120]}"
            return res
        recs = resp.json().get("recommendations", [])
        res.results = len(recs)
        res.live = sum(1 for r in recs if r.get("data_confidence") in ("live", "mixed"))

    # Pass/fail mirrors the user experience: enough results AND fast enough.
    if res.results < args.min_results:
        res.error = f'empty -> "No live places found" ({res.results} < {args.min_results})'
    elif res.latency_ms >= args.max_seconds * 1000:
        res.error = f"slow ({res.latency_ms / 1000:.1f}s >= {args.max_seconds:.1f}s)"
    else:
        res.ok = True
    return res


async def run(args: argparse.Namespace) -> list[CityResult]:
    cities = CITIES[: args.max_cities] if args.max_cities else CITIES
    # Client timeout sits above --max-seconds so a slow city is measured (and
    # flagged), not aborted, until it's clearly hung.
    timeout = httpx.Timeout(max(args.max_seconds * 3, 30.0))
    results: list[CityResult] = []
    async with httpx.AsyncClient(base_url=args.base_url.rstrip("/"), timeout=timeout) as client:
        for i, (name, country, lat, lon) in enumerate(cities):
            if i and args.delay:
                await asyncio.sleep(args.delay)
            res = await run_city(client, args, name, country, lat, lon)
            results.append(res)
            _print_row(res)
    return results


def _print_row(res: CityResult) -> None:
    icon = "PASS" if res.ok else "FAIL"
    latency = f"{res.latency_ms / 1000:6.2f}s" if res.samples_ms else "   -  "
    detail = res.error if res.error else f"{res.results} results ({res.live} live)"
    print(f"  [{icon}] {res.name + ', ' + res.country:<20} {latency}  {detail}")


def summarize(results: list[CityResult], args: argparse.Namespace) -> int:
    print("\n" + "=" * 64)
    passed = [r for r in results if r.ok]
    failed = [r for r in results if not r.ok]
    samples = [ms for r in results for ms in r.samples_ms]
    print(f"Cities: {len(results)}   PASS: {len(passed)}   FAIL: {len(failed)}")
    if samples:
        p50 = statistics.median(samples)
        p95 = sorted(samples)[min(len(samples) - 1, int(round(0.95 * (len(samples) - 1))))]
        # "slowest" only ranks cities that actually returned; a city that hung
        # past the client timeout has no sample and is counted separately.
        measured = [r for r in results if r.samples_ms]
        slowest = max(measured, key=lambda r: r.latency_ms)
        timeouts = sum(1 for r in results if not r.samples_ms)
        extra = f"  timeouts={timeouts}" if timeouts else ""
        print(f"Latency  p50={p50 / 1000:.2f}s  p95={p95 / 1000:.2f}s  "
              f"slowest={slowest.name} {slowest.latency_ms / 1000:.2f}s{extra}")
    if failed:
        print("\nFailures:")
        for r in failed:
            print(f"  - {r.name}, {r.country}: {r.error or 'unknown'}")
    verdict = "READY" if not failed else "NOT READY"
    print(f"\nLaunch readiness: {verdict} "
          f"(target: every city < {args.max_seconds:.0f}s with >= {args.min_results} result)")
    print("=" * 64)

    if args.json:
        payload = {
            "base_url": args.base_url,
            "max_seconds": args.max_seconds,
            "min_results": args.min_results,
            "ready": not failed,
            "cities": [
                {
                    "name": r.name, "country": r.country, "ok": r.ok,
                    "latency_ms": None if not r.samples_ms else round(r.latency_ms, 1),
                    "results": r.results, "live": r.live, "status": r.status, "error": r.error,
                }
                for r in results
            ],
        }
        with open(args.json, "w") as fh:
            json.dump(payload, fh, indent=2)
        print(f"Wrote {args.json}")

    return 0 if not failed else 1


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--base-url", default="http://localhost:8000", help="App base URL (default: %(default)s)")
    p.add_argument("--max-seconds", type=float, default=5.0, help="Latency budget per city (default: %(default)s)")
    p.add_argument("--min-results", type=int, default=1, help="Min recommendations to pass (default: %(default)s)")
    p.add_argument("--delay", type=float, default=6.5,
                   help="Seconds between requests; default respects the live 10/min limit (default: %(default)s)")
    p.add_argument("--repeats", type=int, default=1, help="Requests per city; latency uses the worst (default: %(default)s)")
    p.add_argument("--minutes", type=int, default=240, help="available_minutes in the request (default: %(default)s)")
    p.add_argument("--transport", default="car", choices=["walk", "car", "bike"], help="transport_mode (default: %(default)s)")
    p.add_argument("--intensity", default="easy", choices=["easy", "medium", "active"], help="intensity (default: %(default)s)")
    p.add_argument("--interests", nargs="+", default=DEFAULT_INTERESTS, help="interest ids (default: %(default)s)")
    p.add_argument("--max-cities", type=int, default=0, help="Only test the first N cities (0 = all)")
    p.add_argument("--json", default="", help="Write a machine-readable report to this path")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    print(f"City readiness benchmark -> {args.base_url}")
    print(f"Budget: < {args.max_seconds:.0f}s, >= {args.min_results} result | "
          f"profile: {args.transport}, {args.minutes}min, interests={args.interests}\n")
    results = asyncio.run(run(args))
    return summarize(results, args)


if __name__ == "__main__":
    sys.exit(main())
