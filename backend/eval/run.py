"""Grade LLM explanations against golden fixtures.

Honesty is a gate: the headline metric is the share of explanations that are
*grounded* (introduce no number absent from the computed facts). Run offline
against the templates (no-op baseline) or against a real model with --live.

    cd backend
    .venv/bin/python -m eval.run                 # template baseline
    LLM_PROVIDER=llamacpp LLM_BASE_URL=http://localhost:8080/v1 \
        .venv/bin/python -m eval.run --live      # grade a real model

Capture fixtures first with:  .venv/bin/python -m eval.make_golden
"""
from __future__ import annotations

import logging
import argparse
import asyncio
import json
import time
from pathlib import Path

from app.schemas import AdventureRequest, Recommendation
from app.services.llm import ExplanationInput, get_llm_provider, is_grounded
from app.services.llm.template import TemplateProvider

GOLDEN = Path(__file__).parent / "golden"
logger = logging.getLogger(__name__)


def load_fixtures() -> list[tuple[str, AdventureRequest, list[Recommendation]]]:
    fixtures = []
    for path in sorted(GOLDEN.glob("*.json")):
        data = json.loads(path.read_text())
        request = AdventureRequest(**data["request"])
        recs = [Recommendation(**rec) for rec in data["recommendations"]]
        fixtures.append((path.name, request, recs))
    return fixtures


async def evaluate(provider) -> None:
    fixtures = load_fixtures()
    if not fixtures:
        print(f"No fixtures in {GOLDEN}. Run: .venv/bin/python -m eval.make_golden")
        return

    total = returned = grounded = 0
    latencies: list[float] = []
    for name, request, recs in fixtures:
        started = time.perf_counter()
        try:
            explanations = await provider.explain(ExplanationInput(request=request, recommendations=recs, lang=request.lang))
            logger.debug(f"LLM explanation: {explanations}")
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"  {name}: provider error: {exc.__class__.__name__}")
            continue
        latencies.append((time.perf_counter() - started) * 1000)
        if explanations is None:
            print(f"  {name}: no-op (template / disabled)")
            continue
        for rec, explanation in zip(recs, explanations):
            total += 1
            if explanation is None:
                continue
            returned += 1
            if is_grounded(explanation, rec):
                grounded += 1
            else:
                print(f"  {name}: NOT GROUNDED -> {rec.title}: {explanation.summary!r}")

    print("\n=== LLM explanation eval ===")
    print(f"provider : {provider.name}  model: {getattr(provider, 'model', '-')}")
    print(f"fixtures : {len(fixtures)}  options graded: {total}")
    if total:
        print(f"returned : {returned}/{total} ({100 * returned / total:.0f}%)")
        print(f"grounded : {grounded}/{total} ({100 * grounded / total:.0f}%)   <-- honesty gate")
    if latencies:
        print(f"latency  : p50 {sorted(latencies)[len(latencies) // 2]:.0f} ms")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Use the configured LLM_PROVIDER instead of templates.")
    args = parser.parse_args()
    provider = get_llm_provider() if args.live else TemplateProvider()
    if args.live and isinstance(provider, TemplateProvider):
        print("LLM_PROVIDER=template — set LLM_PROVIDER/LLM_BASE_URL to grade a real model.")
    asyncio.run(evaluate(provider))


if __name__ == "__main__":
    main()
