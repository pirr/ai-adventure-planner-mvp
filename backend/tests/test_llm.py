import asyncio

from app.config import Settings
from app.schemas import AdventureRequest, Recommendation, ScoreBreakdown
from app.services.llm import (
    Explanation,
    ExplanationInput,
    LLMProvider,
    OpenAICompatibleProvider,
    TemplateProvider,
    explain_recommendations,
    get_llm_provider,
)


def _rec(**over) -> Recommendation:
    base = dict(
        id="r1",
        title="Old Fort",
        place_type="fortress",
        lat=42.4,
        lon=18.7,
        adventure_score=86,
        score_breakdown=ScoreBreakdown(
            time_fit=100, weather_fit=90, distance_fit=80, safety_fit=85,
            group_fit=80, interest_fit=88, place_quality=80, personal_preference_fit=70,
        ),
        total_minutes=120,
        travel_minutes=36,
        activity_minutes=80,
        distance_km=10.0,
        walking_km=2.0,
        difficulty="easy",
        description="A history-focused stop.",
        why=["Fits your time."],
        warnings=[],
        map_url="https://maps.example/x",
        source="test",
    )
    base.update(over)
    return Recommendation(**base)


class FakeProvider(LLMProvider):
    name = "fake"

    def __init__(self, explanations):
        self.explanations = explanations

    async def explain(self, payload: ExplanationInput):
        return self.explanations


class BrokenProvider(LLMProvider):
    name = "broken"

    async def explain(self, payload: ExplanationInput):
        raise RuntimeError("boom")


def _run(provider, rec=None):
    rec = rec or _rec()
    return asyncio.run(explain_recommendations([rec], AdventureRequest(lat=42.4, lon=18.7), provider))


def test_grounded_explanation_is_merged():
    provider = FakeProvider([
        Explanation(summary="A short fortress walk that fits your plan.", why=["Great views"], data_confidence_note="No live traffic data."),
    ])
    out = _run(provider)
    assert out[0].summary.startswith("A short fortress")
    assert out[0].why == ["Great views"]
    assert out[0].data_confidence_note == "No live traffic data."


def test_invented_number_is_rejected():
    # 47 and 999 are not in the computed facts -> guard rejects, template kept.
    provider = FakeProvider([Explanation(summary="Open until 47 with 999 steps.", why=["x"])])
    out = _run(provider)
    assert out[0].summary is None
    assert out[0].why == ["Fits your time."]


def test_grounded_number_is_allowed():
    # 86 (score) and 2 (walking_km) are in the facts.
    provider = FakeProvider([Explanation(summary="Scores 86 with about 2 km of walking.", why=["ok"])])
    out = _run(provider)
    assert out[0].summary.startswith("Scores 86")


def test_provider_error_falls_back_to_template():
    out = _run(BrokenProvider())
    assert out[0].summary is None
    assert out[0].why == ["Fits your time."]


def test_template_provider_is_noop():
    out = _run(TemplateProvider())
    assert out[0].summary is None
    assert out[0].why == ["Fits your time."]


def test_gemini_preset_resolves_to_openai_compatible():
    provider = get_llm_provider(Settings(llm_provider="gemini", llm_model="gemini-2.5-flash", llm_api_key="x"))
    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.base_url == "https://generativelanguage.googleapis.com/v1beta/openai"
    assert provider.model == "gemini-2.5-flash"
    assert provider.api_key == "x"


def test_disabled_falls_back_to_template():
    provider = get_llm_provider(Settings(llm_provider="gemini", llm_enabled=False))
    assert isinstance(provider, TemplateProvider)
