from __future__ import annotations

from app.services.llm.base import Explanation, ExplanationInput, LLMProvider


class TemplateProvider(LLMProvider):
    """Default, offline provider. The rule-based `why`/`description` are already
    on each recommendation, so it returns None to mean 'keep them as-is'. Always
    available, never calls the network — also the fallback for every other
    provider when it errors or fails the grounding guard."""

    name = "template"

    async def explain(self, payload: ExplanationInput) -> list[Explanation | None] | None:
        return None
