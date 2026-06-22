from __future__ import annotations

import logging
import time
from typing import Any

from app.config import settings
from app.schemas import AdventureRequest, Recommendation
from app.services.llm.ab import explainer_provider
from app.services.llm.service import explain_recommendations

logger = logging.getLogger(__name__)

# {request_id: (expires_at, recommendations, request)}. Holds the finished
# recommendation objects so the follow-up /api/explanations call can ground the
# LLM prose against the exact facts that were returned. In-process and lossy on
# restart by design: a miss just leaves the rule-based text in place.
_pending: dict[str, tuple[float, list[Recommendation], AdventureRequest]] = {}


def stash(request_id: str, recommendations: list[Recommendation], request: AdventureRequest) -> None:
    ttl = settings.explanation_stash_ttl_seconds
    max_entries = settings.explanation_stash_max_entries
    if ttl <= 0 or max_entries <= 0 or not recommendations:
        return
    now = time.time()
    for key, (expires_at, _, _) in list(_pending.items()):
        if expires_at <= now:
            _pending.pop(key, None)
    while len(_pending) >= max_entries:
        _pending.pop(next(iter(_pending)))
    _pending[request_id] = (now + ttl, recommendations, request)


async def resolve(request_id: str) -> list[dict[str, Any]]:
    """Run the (deferred) LLM explanation step for a stashed request and return
    the final prose per card. One-shot: the entry is claimed on lookup so a
    duplicate call returns []. Never raises — explain_recommendations is
    best-effort and substitutes rule-based text on any provider failure."""
    entry = _pending.pop(request_id, None)
    if entry is None:
        return []
    expires_at, recommendations, request = entry
    if expires_at <= time.time():
        return []
    provider = explainer_provider(request)
    explained = await explain_recommendations(recommendations, request, provider)
    return [
        {
            "id": rec.id,
            "summary": rec.summary,
            "why": rec.why,
            "data_confidence_note": rec.data_confidence_note,
        }
        for rec in explained
    ]
