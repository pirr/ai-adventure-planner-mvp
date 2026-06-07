"""Grade LLM explanations against golden fixtures.

Honesty is a gate: the headline is the share of explanations that are *grounded*
(introduce no number absent from the computed facts). Also reports format / safety
/ coverage, latency, optional cost and optional LLM-judge clarity; and a
multi-model scoreboard. (Run via docker compose; `python` = inside the container.)

    python -m eval.run                                   # template baseline (offline)
    LLM_PROVIDER=gemini LLM_MODEL=gemini-2.5-flash LLM_API_KEY=... \
        python -m eval.run --live --repeats 3            # grade a real model
    python -m eval.run --scoreboard --price-per-1k 0.30  # compare eval/models.json
    LLM_JUDGE_BASE_URL=... LLM_JUDGE_MODEL=... LLM_JUDGE_API_KEY=... \
        python -m eval.run --live --judge                # + clarity 1-5

Capture fixtures first with:  python -m eval.make_golden
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import time
from pathlib import Path

from app.schemas import AdventureRequest, Recommendation
from app.services.llm import ExplanationInput, get_llm_provider
from app.services.llm.openai_compat import OpenAICompatibleProvider, build_messages
from app.services.llm.template import TemplateProvider
from eval import judge as judge_mod
from eval import metrics

GOLDEN = Path(__file__).parent / "golden"
MODELS = Path(__file__).parent / "models.json"
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


def _prompt_chars(request, recs) -> int:
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


async def collect(provider, repeats=1, price_per_1k=0.0, judge_cfg=None, verbose=False) -> dict:
    fixtures = load_fixtures()
    agg = {key: 0 for key in _METRIC_KEYS}
    latencies: list[float] = []
    clarity: list[int] = []
    cost = 0.0
    per_fixture: dict[str, set[float]] = {}

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
                per_fixture.setdefault(name, set()).add(round(counts["grounded"] / counts["total"], 3))
            if price_per_1k:
                cost += metrics.estimate_cost(_prompt_chars(request, recs), _output_chars(explanations), price_per_1k)
            for rec, explanation in zip(recs, explanations):
                if explanation is None:
                    continue
                if verbose and not metrics.is_grounded(explanation, rec):
                    print(f"  NOT GROUNDED [{name}] {rec.title}: {explanation.summary!r}")
                if judge_cfg:
                    score = await judge_mod.judge_clarity(rec=rec, explanation=explanation, **judge_cfg)
                    if score is not None:
                        clarity.append(score)

    return {
        "fixtures": len(fixtures), "agg": agg, "latencies": latencies,
        "cost": cost, "clarity": clarity, "per_fixture": per_fixture,
    }


def _pct(num: int, den: int) -> str:
    return f"{(100 * num / den):.0f}%" if den else "-"


def _p50(latencies) -> str:
    return f"{sorted(latencies)[len(latencies) // 2]:.0f}" if latencies else "-"


def _clarity_avg(scores) -> str:
    return f"{sum(scores) / len(scores):.1f}" if scores else "-"


async def evaluate(provider, repeats=1, price_per_1k=0.0, judge_cfg=None) -> None:
    result = await collect(provider, repeats, price_per_1k, judge_cfg, verbose=True)
    agg = result["agg"]
    total = agg["total"]
    print("\n=== LLM explanation eval ===")
    print(f"provider : {provider.name}  model: {getattr(provider, 'model', '-')}")
    print(f"fixtures : {result['fixtures']} x {repeats} repeats   options graded: {total}")
    if not total:
        print("(no explanations returned — template/disabled provider)")
        return
    print(f"returned : {_pct(agg['returned'], total)}")
    print(f"grounded : {_pct(agg['grounded'], total)}   <-- honesty gate")
    print(f"format   : {_pct(agg['format_valid'], total)}")
    print(f"safety   : {_pct(agg['safety'], total)}")
    print(f"coverage : {_pct(agg['coverage'], total)}")
    print(f"latency  : p50 {_p50(result['latencies'])} ms")
    if judge_cfg:
        print(f"clarity  : {_clarity_avg(result['clarity'])} / 5 (LLM-judge)")
    if price_per_1k:
        print(f"cost est : ${result['cost']:.4f} total (~${price_per_1k}/1k tokens)")
    if repeats > 1:
        unstable = [name for name, rates in result["per_fixture"].items() if len(rates) > 1]
        print(f"variance : {len(unstable)} fixture(s) with inconsistent grounding")


async def scoreboard(repeats=1, price_per_1k=0.0, judge_cfg=None) -> None:
    entries = json.loads(MODELS.read_text())
    print("\n=== Model scoreboard ===")
    header = f"{'model':<20} {'ground':>7} {'format':>7} {'safety':>7} {'cover':>7} {'clarity':>8} {'p50ms':>7}"
    if price_per_1k:
        header += f" {'$est':>9}"
    print(header)
    for entry in entries:
        api_key = os.getenv(entry["api_key_env"]) if entry.get("api_key_env") else None
        # No rule-based fallback here: a failing model should show blank, not be
        # masked by the template output.
        provider = OpenAICompatibleProvider(
            base_url=entry["base_url"], model=entry["model"], api_key=api_key, rule_based_fallback=False
        )
        try:
            result = await collect(provider, repeats, price_per_1k, judge_cfg)
        except Exception as exc:  # noqa: BLE001
            print(f"{entry['name']:<20} error: {exc.__class__.__name__}")
            continue
        agg = result["agg"]
        total = agg["total"] or 1
        row = (
            f"{entry['name']:<20} {_pct(agg['grounded'], total):>7} {_pct(agg['format_valid'], total):>7} "
            f"{_pct(agg['safety'], total):>7} {_pct(agg['coverage'], total):>7} "
            f"{_clarity_avg(result['clarity']):>8} {_p50(result['latencies']):>7}"
        )
        if price_per_1k:
            row += f" {'$' + format(result['cost'], '.4f'):>9}"
        print(row)
    print("\nGate first (grounding/format/safety), then pick the cheapest/fastest survivor.")


def _judge_cfg(enabled: bool):
    if not enabled:
        return None
    base_url, model = os.getenv("LLM_JUDGE_BASE_URL", ""), os.getenv("LLM_JUDGE_MODEL", "")
    if not base_url or not model:
        print("--judge needs LLM_JUDGE_BASE_URL and LLM_JUDGE_MODEL; skipping clarity.")
        return None
    return {"base_url": base_url, "model": model, "api_key": os.getenv("LLM_JUDGE_API_KEY")}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Use the configured LLM_PROVIDER instead of templates.")
    parser.add_argument("--scoreboard", action="store_true", help="Compare every model in eval/models.json.")
    parser.add_argument("--judge", action="store_true", help="Add LLM-judge clarity (needs LLM_JUDGE_* env).")
    parser.add_argument("--repeats", type=int, default=1, help="Run each fixture N times (variance check).")
    parser.add_argument("--price-per-1k", type=float, default=0.0, help="$/1k tokens for the cost estimate.")
    args = parser.parse_args()
    judge_cfg = _judge_cfg(args.judge)

    if args.scoreboard:
        asyncio.run(scoreboard(args.repeats, args.price_per_1k, judge_cfg))
        return

    provider = get_llm_provider() if args.live else TemplateProvider()
    if args.live and isinstance(provider, TemplateProvider):
        print("LLM_PROVIDER=template — set LLM_PROVIDER/LLM_BASE_URL to grade a real model.")
    asyncio.run(evaluate(provider, args.repeats, args.price_per_1k, judge_cfg))


if __name__ == "__main__":
    main()
