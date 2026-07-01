"""Blind pairwise LLM-judge: is a provider's recommendation set actually worse?

Objective overlap can only say a provider *diverges* from the baseline. This asks
a model which of two de-branded recommendation lists is better for the same trip
brief. Each scenario is judged twice with the lists swapped (A/B and B/A) to
cancel position bias; a verdict only counts when both orderings agree, otherwise
it's `unstable`. Works against any OpenAI-compatible endpoint via `LLM_JUDGE_*`,
including a local llamacpp server (zero API cost). Best-effort, like eval/judge.py.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from app.schemas import AdventureRequest, Recommendation
from app.services.net import http_client
from eval.run import _judge_cfg  # reuse LLM_JUDGE_* env parsing

_SYSTEM = (
    "You compare two short lists of suggested places (A and B) for the SAME trip brief. "
    "Judge which list is the better set of recommendations for this user: relevance to their "
    "interests, variety, and practicality (distance / time fit), and apparent quality. "
    "Do NOT judge writing style or wording. If they are about equally good, answer tie. "
    'Output JSON only: {"winner": "A" | "B" | "tie"}.'
)


def _brief(request: AdventureRequest) -> dict:
    return {
        "transport": request.transport_mode,
        "available_minutes": request.available_minutes,
        "group": request.group_type,
        "interests": request.interests,
        "lang": request.lang,
    }


def _render(recs: list[Recommendation]) -> list[dict]:
    """De-branded fact block per recommendation — no source/provider leaks."""
    items: list[dict] = []
    for rec in recs:
        item = {
            "title": rec.title,
            "type": rec.place_type,
            "distance_km": rec.distance_km,
            "travel_minutes": rec.travel_minutes,
            "why": rec.why[:4],
        }
        if rec.rating is not None:
            item["rating"] = rec.rating
        items.append(item)
    return items


async def _ask(cfg: dict, brief: dict, list_a: list[dict], list_b: list[dict], timeout: float) -> str | None:
    body = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": json.dumps(
                {"brief": brief, "list_A": list_a, "list_B": list_b}, ensure_ascii=False, default=str)},
        ],
        "temperature": 0.0,
    }
    if "generativelanguage.googleapis.com" not in cfg["base_url"]:
        body["response_format"] = {"type": "json_object"}
    headers = {"Content-Type": "application/json"}
    if cfg.get("api_key"):
        headers["Authorization"] = f"Bearer {cfg['api_key']}"
    try:
        async with http_client(timeout) as client:
            response = await client.post(
                f"{cfg['base_url'].rstrip('/')}/chat/completions", json=body, headers=headers)
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
        start, end = content.find("{"), content.rfind("}")
        winner = json.loads(content[start : end + 1]).get("winner")
        return winner if winner in {"A", "B", "tie"} else None
    except Exception:  # noqa: BLE001 - judging is best-effort
        return None


def _resolve(order1: str | None, order2: str | None) -> str:
    """Map two position-swapped verdicts to a stable result.

    order1 had A=baseline, B=provider; order2 had A=provider, B=baseline.
    A clean win/loss requires agreement across both orderings; a flip (position
    bias) or any judge error is reported as `unstable`."""
    v1 = {"A": "baseline", "B": "provider", "tie": "tie"}.get(order1 or "")
    v2 = {"A": "provider", "B": "baseline", "tie": "tie"}.get(order2 or "")
    if v1 is None or v2 is None:
        return "unstable"
    if v1 == v2:
        return v1
    if "tie" in (v1, v2):
        return "tie"
    return "unstable"


async def judge_scenario(
    cfg: dict,
    request: AdventureRequest,
    baseline_recs: list[Recommendation],
    provider_recs: list[Recommendation],
    timeout: float = 30.0,
) -> str:
    """One scenario -> 'provider' | 'baseline' | 'tie' | 'unstable'."""
    brief = _brief(request)
    a, b = _render(baseline_recs), _render(provider_recs)
    order1 = await _ask(cfg, brief, a, b, timeout)  # A=baseline, B=provider
    order2 = await _ask(cfg, brief, b, a, timeout)  # A=provider, B=baseline
    return _resolve(order1, order2)


@dataclass
class JudgeTally:
    provider: int = 0
    baseline: int = 0
    tie: int = 0
    unstable: int = 0

    def add(self, verdict: str) -> None:
        setattr(self, verdict, getattr(self, verdict) + 1)

    @property
    def n(self) -> int:
        return self.provider + self.baseline + self.tie + self.unstable

    @property
    def win_rate(self) -> float | None:
        """Provider wins / decided (ties & unstable excluded). None if undecided."""
        decided = self.provider + self.baseline
        return self.provider / decided if decided else None

    @property
    def unstable_pct(self) -> float:
        return self.unstable / self.n if self.n else 0.0


def judge_config() -> dict | None:
    """LLM_JUDGE_* config, or None if not set (judge silently disabled)."""
    return _judge_cfg(True)
