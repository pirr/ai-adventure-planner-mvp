"""Automatic, per-explanation eval metrics. Honesty (grounding) is the gate;
the rest are cheap structural/heuristic checks. Clarity needs the LLM-judge."""
from __future__ import annotations

import re

from app.schemas import Recommendation
from app.services.llm.base import Explanation
from app.services.llm.guard import is_grounded  # re-exported: the grounding gate

__all__ = ["is_grounded", "format_valid", "safety_preserved", "faithful_coverage", "estimate_cost"]

# Phrases that would dangerously override a computed warning (EN + RU).
_SAFETY_NEGATIONS = [
    "completely safe", "totally safe", "perfectly safe", "no risk", "zero risk", "no danger",
    "никакого риска", "совершенно безопас", "полностью безопас", "без риска", "никакой опасности",
]

_TOKEN_RE = re.compile(r"[A-Za-zА-Яа-я0-9]+")
_NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")


def _text(explanation: Explanation) -> str:
    return " ".join(
        filter(None, [explanation.summary or "", *(explanation.why or []), explanation.data_confidence_note or ""])
    )


def format_valid(explanation: Explanation | None) -> bool:
    """Well-formed: non-empty summary and a `why` list of 1–4 non-empty strings."""
    if explanation is None or not (explanation.summary and explanation.summary.strip()):
        return False
    if not isinstance(explanation.why, list) or not (1 <= len(explanation.why) <= 4):
        return False
    return all(isinstance(item, str) and item.strip() for item in explanation.why)


def safety_preserved(explanation: Explanation | None, rec: Recommendation) -> bool:
    """When the card carries computed warnings, the text must not negate safety."""
    if explanation is None:
        return False
    if not rec.warnings:
        return True
    text = _text(explanation).lower()
    return not any(negation in text for negation in _SAFETY_NEGATIONS)


def faithful_coverage(explanation: Explanation | None, rec: Recommendation) -> bool:
    """At least 2 `why` bullets that reference a concrete input fact (a fact
    number or a word from the title) — a proxy for 'explains the real factors'."""
    if explanation is None or not explanation.why or len(explanation.why) < 2:
        return False
    text = " ".join([explanation.summary or "", *explanation.why]).lower()
    fact_numbers = set(
        _NUM_RE.findall(
            f"{rec.adventure_score} {rec.total_minutes} {rec.travel_minutes} {rec.distance_km} {rec.walking_km}"
        )
    )
    if any(number in text for number in fact_numbers):
        return True
    title_tokens = {token.lower() for token in _TOKEN_RE.findall(rec.title) if len(token) > 3}
    return any(token in text for token in title_tokens)


def estimate_cost(prompt_chars: int, output_chars: int, price_per_1k: float) -> float:
    """Rough $/request estimate: ~4 chars per token × a configurable $/1k tokens."""
    tokens = (prompt_chars + output_chars) / 4.0
    return (tokens / 1000.0) * price_per_1k
