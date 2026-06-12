from __future__ import annotations

from dataclasses import dataclass

from app.schemas import AdventureRequest, ParsedSituation, Recommendation


@dataclass
class Explanation:
    """LLM-produced explanation for one recommendation. Any field may be None."""

    summary: str | None = None
    why: list[str] | None = None
    data_confidence_note: str | None = None


@dataclass
class ExplanationInput:
    """Everything the model is allowed to see: the request plus already-computed
    recommendations (scores, warnings, weather, distances). No live lookups."""

    request: AdventureRequest
    recommendations: list[Recommendation]
    lang: str = "en"


class LLMProvider:
    """Base provider. ``explain`` returns one Explanation (or None) per input
    recommendation, in order — or None to mean 'no enrichment, keep templates'."""

    name = "base"

    async def explain(self, payload: ExplanationInput) -> list[Explanation | None] | None:
        raise NotImplementedError

    async def parse_situation(self, text: str, lang: str) -> ParsedSituation | None:
        """Map a free-text trip description onto AdventureRequest fields.
        None means 'cannot parse' — also the default, so the TemplateProvider
        (and any provider that doesn't override this) reports no support."""
        return None
