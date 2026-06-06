from __future__ import annotations

from app.config import settings
from app.schemas import AdventureRequest, Recommendation
from app.services.llm.base import ExplanationInput, LLMProvider
from app.services.llm.guard import is_grounded
from app.services.llm.template import TemplateProvider


async def explain_recommendations(
    recommendations: list[Recommendation],
    request: AdventureRequest,
    provider: LLMProvider,
) -> list[Recommendation]:
    """Enrich the top recommendations with grounded LLM explanations.

    Best-effort and safe: the TemplateProvider is a no-op, any provider error
    keeps the rule-based output, and an explanation that fails the grounding
    guard is discarded for that single card (the rest still apply).
    """
    if not recommendations or isinstance(provider, TemplateProvider):
        return recommendations

    top = recommendations[: settings.llm_max_explained]
    try:
        explanations = await provider.explain(
            ExplanationInput(request=request, recommendations=top, lang=request.lang)
        )
    except Exception:  # noqa: BLE001 - explanations are best-effort; degrade to templates
        return recommendations
    if not explanations:
        return recommendations

    for rec, explanation in zip(top, explanations):
        if explanation is None or not is_grounded(explanation, rec):
            continue
        if explanation.summary:
            rec.summary = explanation.summary
        if explanation.why:
            rec.why = explanation.why
        if explanation.data_confidence_note:
            rec.data_confidence_note = explanation.data_confidence_note
    return recommendations
