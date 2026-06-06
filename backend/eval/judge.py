"""Optional LLM-as-judge clarity scoring (1–5). Best-effort and eval-only:
a judge failure just yields no score. Clarity is only trusted alongside the
automatic grounding gate — a fluent but ungrounded explanation still fails."""
from __future__ import annotations

import json

from app.schemas import Recommendation
from app.services.llm.base import Explanation
from app.services.llm.openai_compat import _facts
from app.services.net import http_client

_JUDGE_SYSTEM = (
    "You rate the clarity and usefulness of a short travel explanation on an integer 1-5 scale "
    "(5 = excellent). Judge only clarity, concision and usefulness — NOT factual correctness. "
    'Output JSON only: {"clarity": <1-5>}.'
)


async def judge_clarity(
    base_url: str,
    model: str,
    api_key: str | None,
    rec: Recommendation,
    explanation: Explanation,
    timeout: float = 20.0,
) -> int | None:
    payload = {
        "facts": _facts(rec),
        "explanation": {
            "summary": explanation.summary,
            "why": explanation.why,
            "data_confidence_note": explanation.data_confidence_note,
        },
    }
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": _JUDGE_SYSTEM},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)},
        ],
        "temperature": 0.0,
    }
    if "generativelanguage.googleapis.com" not in base_url:
        body["response_format"] = {"type": "json_object"}
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        async with http_client(timeout) as client:
            response = await client.post(f"{base_url.rstrip('/')}/chat/completions", json=body, headers=headers)
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
        start, end = content.find("{"), content.rfind("}")
        score = json.loads(content[start : end + 1]).get("clarity")
        score = int(score)
        return score if 1 <= score <= 5 else None
    except Exception:  # noqa: BLE001 - judging is best-effort
        return None
