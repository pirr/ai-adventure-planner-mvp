from __future__ import annotations

import re

from app.schemas import Recommendation
from app.services.llm.base import Explanation

_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")


def _canon(token: str) -> str:
    text = token.replace(",", ".")
    try:
        value = float(text)
    except ValueError:
        return text
    return str(int(value)) if value == int(value) else str(value)


def _numbers(text: str) -> set[str]:
    return {_canon(match) for match in _NUMBER_RE.findall(text)}


def _fact_numbers(rec: Recommendation) -> set[str]:
    b = rec.score_breakdown
    parts: list[str] = [
        str(v)
        for v in (
            rec.adventure_score, rec.total_minutes, rec.travel_minutes, rec.activity_minutes,
            rec.distance_km, rec.walking_km, b.time_fit, b.weather_fit, b.distance_fit,
            b.safety_fit, b.group_fit, b.interest_fit, b.place_quality, b.personal_preference_fit,
        )
    ]
    # Hour equivalents — the model may say "2 hours" for a 120-minute figure.
    for minutes in (rec.total_minutes, rec.travel_minutes, rec.activity_minutes):
        parts.append(str(round(minutes / 60)))
        parts.append(str(minutes // 60))
    if rec.arrival_weather and rec.arrival_weather.temperature_c is not None:
        parts.append(str(round(rec.arrival_weather.temperature_c)))
    # Numbers already present in the title or the computed warnings are allowed.
    parts.append(rec.title)
    parts.extend(rec.warnings)
    return _numbers(" ".join(parts))


def is_grounded(explanation: Explanation, rec: Recommendation) -> bool:
    """True when every number in the explanation appears in the computed facts.

    Catches the main hallucination risk (invented times/distances/temperatures).
    A failure is not an error — the caller falls back to the template output.
    """
    text = " ".join(
        filter(None, [explanation.summary or "", *(explanation.why or []), explanation.data_confidence_note or ""])
    )
    if not text.strip():
        return False
    return _numbers(text) <= _fact_numbers(rec)
