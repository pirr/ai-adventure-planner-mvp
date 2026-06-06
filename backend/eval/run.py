"""Grade LLM explanations against golden fixtures.

Honesty is a gate: the headline is the share of explanations that are
*grounded* (introduce no number absent from the computed facts). Also reports
format-valid, safety-preserved and faithful-coverage, plus latency and an
optional cost estimate.

    cd backend
    python -m eval.run                              # template baseline (offline)
    LLM_PROVIDER=gemini LLM_MODEL=gemini-2.5-flash LLM_API_KEY=... \
        python -m eval.run --live --repeats 3       # grade a real model

Capture fixtures first with:  python -m eval.make_golden
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
from pathlib import Path

from app.schemas import AdventureRequest, Recommendation
from app.services.llm import ExplanationInput, get_llm_provider
from app.services.llm.openai_compat import build_messages
from app.services.llm.template import TemplateProvider
from eval import metrics

GOLDEN = Path(__file__).parent / "golden"
logger = logging.getLogger(__name__)

_METRIC_KEYS = ("total", "returned", "grounded", "format_valid", "safety", "coverage")


def load_fixtures(include_adversarial: bool = True) -> list[tuple[str, AdventureRequest, list[Recommendation]]]:
    paths = sorted(GOLDEN.glob("*.json"))
    if include_adversarial:
        paths += sorted((GOLDEN / "adversarial").glob("*.json"))
    fixtures = []
    for path in paths:
        data = json.loads(path.read_text())
        request = AdventureRequest(**data["request"])
        recs = [Recommendation(**rec) for rec in data["recommendations"]]
        label = path.stem + (" [adv]" if path.parent.name == "adversarial" else "")
        fixtures.append((label, request, recs))
    return fixtures


def _grade(explanations, recs) -> dict[str, int]:
    counts = {key: 0 for key in _METRIC_KEYS}
    for rec, explanation in zip(recs, explanations):
        counts["total"] += 1
        if explanation is None:
            continue
        counts["returned"] += 1
        counts["grounded"] += metrics.is_grounded(explanation, rec)
        counts["format_valid"] += metrics.format_valid(explanation)
        counts["safety"] += metrics.safety_preserved(explanation, rec)
        counts["coverage"] += metrics.faithful_coverage(explanation, rec)
    return counts


def _prompt_chars(request: AdventureRequest, recs: list[Recommendation]) -> int:
    messages = build_messages(ExplanationInput(request=request, recommendations=recs, lang=request.lang))
    return len(json.dumps(messages, ensure_ascii=False))


def _output_chars(explanations) -> int:
    total = 0
    for explanation in explanations:
        if explanation is None:
            continue
        total += len(explanation.summary or "") + len(explanation.data_confidence_note or "")
        total += sum(len(item) for item in (explanation.why or []))
    return total


async def evaluate(provider, repeats: int = 1, price_per_1k: float = 0.0) -> dict[str, int]:
    fixtures = load_fixtures()
    if not fixtures:
        print(f"No fixtures in {GOLDEN}. Run: python -m eval.make_golden")
        return {}

    agg = {key: 0 for key in _METRIC_KEYS}
    latencies: list[float] = []
    cost = 0.0
    per_fixture_grounded: dict[str, set[float]] = {}

    for name, request, recs in fixtures:
        for _ in range(repeats):
            started = time.perf_counter()
            try:
                explanations = await provider.explain(
                    ExplanationInput(request=request, recommendations=recs, lang=request.lang)
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("%s: provider error: %s", name, exc.__class__.__name__)
                continue
            latencies.append((time.perf_counter() - started) * 1000)
            if explanations is None:
                continue
            counts = _grade(explanations, recs)
            for key in _METRIC_KEYS:
                agg[key] += counts[key]
            if counts["total"]:
                per_fixture_grounded.setdefault(name, set()).add(round(counts["grounded"] / counts["total"], 3))
            if price_per_1k:
                cost += metrics.estimate_cost(_prompt_chars(request, recs), _output_chars(explanations), price_per_1k)
            for rec, explanation in zip(recs, explanations):
                if explanation is not None and not metrics.is_grounded(explanation, rec):
                    print(f"  NOT GROUNDED [{name}] {rec.title}: {explanation.summary!r}")

    _print_summary(provider, fixtures, repeats, agg, latencies, cost, price_per_1k, per_fixture_grounded)
    return agg


def _pct(num: int, den: int) -> str:
    return f"{(100 * num / den):.0f}%" if den else "-"


def _print_summary(provider, fixtures, repeats, agg, latencies, cost, price, per_fixture) -> None:
    total = agg["total"]
    print("\n=== LLM explanation eval ===")
    print(f"provider : {provider.name}  model: {getattr(provider, 'model', '-')}")
    print(f"fixtures : {len(fixtures)} x {repeats} repeats   options graded: {total}")
    if not total:
        print("(no explanations returned — template/disabled provider)")
        return
    print(f"returned : {_pct(agg['returned'], total)}")
    print(f"grounded : {_pct(agg['grounded'], total)}   <-- honesty gate")
    print(f"format   : {_pct(agg['format_valid'], total)}")
    print(f"safety   : {_pct(agg['safety'], total)}")
    print(f"coverage : {_pct(agg['coverage'], total)}")
    if latencies:
        print(f"latency  : p50 {sorted(latencies)[len(latencies) // 2]:.0f} ms")
    if price:
        print(f"cost est : ${cost:.4f} total (~${price}/1k tokens)")
    if repeats > 1:
        unstable = [name for name, rates in per_fixture.items() if len(rates) > 1]
        print(f"variance : {len(unstable)} fixture(s) with inconsistent grounding across repeats")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Use the configured LLM_PROVIDER instead of templates.")
    parser.add_argument("--repeats", type=int, default=1, help="Run each fixture N times (variance check).")
    parser.add_argument("--price-per-1k", type=float, default=0.0, help="$/1k tokens for the cost estimate.")
    args = parser.parse_args()
    provider = get_llm_provider() if args.live else TemplateProvider()
    if args.live and isinstance(provider, TemplateProvider):
        print("LLM_PROVIDER=template — set LLM_PROVIDER/LLM_BASE_URL to grade a real model.")
    asyncio.run(evaluate(provider, repeats=args.repeats, price_per_1k=args.price_per_1k))


if __name__ == "__main__":
    main()
