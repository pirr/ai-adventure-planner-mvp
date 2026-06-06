from __future__ import annotations

import json
from typing import Any

from app.schemas import Recommendation
from app.services.llm.base import Explanation, ExplanationInput, LLMProvider
from app.services.net import http_client

_SYSTEM_PROMPT = (
    "You write short, honest explanations for already-chosen travel recommendations. "
    "You are given computed facts for each option. Rules: use ONLY these facts; never invent "
    "numbers, place names, opening hours, weather, traffic, distances or travel times that are not "
    "in the input; if something is unknown, say it is unavailable; do not change or soften the "
    "safety warnings. Be concise and concrete. Respond in the requested language. "
    'Output a single JSON object: {"explanations": [{"id": <id>, "summary": <string, max 30 words>, '
    '"why": [<2-4 short strings>], "data_confidence_note": <short string>}]} with one entry per '
    "option, in the same order. Output JSON only."
)


def _facts(rec: Recommendation) -> dict[str, Any]:
    b = rec.score_breakdown
    facts: dict[str, Any] = {
        "id": rec.id,
        "title": rec.title,
        "place_type": rec.place_type,
        "adventure_score": rec.adventure_score,
        "total_minutes": rec.total_minutes,
        "travel_minutes": rec.travel_minutes,
        "activity_minutes": rec.activity_minutes,
        "distance_km": rec.distance_km,
        "walking_km": rec.walking_km,
        "difficulty": rec.difficulty,
        "score_breakdown": b.dict(),
        "warnings": rec.warnings,
        "data_confidence": rec.data_confidence,
    }
    if rec.arrival_weather is not None:
        facts["weather_on_arrival"] = {
            "summary": rec.arrival_weather.summary,
            "temperature_c": rec.arrival_weather.temperature_c,
        }
    return facts


def build_messages(payload: ExplanationInput) -> list[dict[str, str]]:
    request = payload.request
    content = {
        "language": payload.lang,
        "user_context": {
            "available_minutes": request.available_minutes,
            "transport_mode": request.transport_mode,
            "group_type": request.group_type,
            "children_ages": request.children_ages,
            "with_dog": request.with_dog,
            "with_elderly": request.with_elderly,
            "reduced_mobility": request.reduced_mobility,
            "interests": request.interests,
        },
        "unknown_fields": ["live_traffic", "events", "crowds"],
        "options": [_facts(rec) for rec in payload.recommendations],
    }
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(content, ensure_ascii=False)},
    ]


def parse_explanations(content: str, count: int) -> list[Explanation | None]:
    start, end = content.find("{"), content.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in LLM response")
    data = json.loads(content[start : end + 1])
    raw = data.get("explanations") or []
    results: list[Explanation | None] = []
    for item in raw[:count]:
        if not isinstance(item, dict):
            results.append(None)
            continue
        why = item.get("why")
        results.append(
            Explanation(
                summary=item.get("summary") or None,
                why=[str(w) for w in why] if isinstance(why, list) else None,
                data_confidence_note=item.get("data_confidence_note") or None,
            )
        )
    while len(results) < count:
        results.append(None)
    return results


class OpenAICompatibleProvider(LLMProvider):
    """Talks to any OpenAI-compatible /v1/chat/completions endpoint: a local
    llama.cpp `llama-server`, Ollama, OpenAI, or cheap hosted models."""

    name = "openai"

    def __init__(self, base_url: str, model: str, api_key: str | None = None, timeout: float = 20.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout

    async def explain(self, payload: ExplanationInput) -> list[Explanation | None] | None:
        if not payload.recommendations:
            return None
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        body = {
            "model": self.model,
            "messages": build_messages(payload),
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }
        async with http_client(self.timeout) as client:
            response = await client.post(f"{self.base_url}/chat/completions", json=body, headers=headers)
            response.raise_for_status()
            data = response.json()
        content = data["choices"][0]["message"]["content"]
        return parse_explanations(content, len(payload.recommendations))
