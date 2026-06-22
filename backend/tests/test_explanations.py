import asyncio
from types import SimpleNamespace

from app.schemas import AdventureRequest, Recommendation, ScoreBreakdown
from app.services import explanations
from app.services.llm import Explanation, ExplanationInput, LLMProvider
from app.services.llm.ab import ab_bucket, explainer_provider


def test_ab_bucket_is_stable_and_binary():
    assert ab_bucket("user-1") == ab_bucket("user-1")
    assert ab_bucket("user-1") in (0, 1)


def _rec(**over) -> Recommendation:
    base = dict(
        id="r1", title="Old Fort", place_type="fortress", lat=42.4, lon=18.7,
        adventure_score=86,
        score_breakdown=ScoreBreakdown(
            time_fit=100, weather_fit=90, distance_fit=80, safety_fit=85,
            group_fit=80, interest_fit=88, place_quality=80, personal_preference_fit=70,
        ),
        total_minutes=120, travel_minutes=36, activity_minutes=80,
        distance_km=10.0, walking_km=2.0, difficulty="easy",
        description="A history-focused stop.", why=["Fits your time."],
        warnings=[], map_url="https://maps.example/x", source="test",
    )
    base.update(over)
    return Recommendation(**base)


class _FakeProvider(LLMProvider):
    name = "fake"

    def __init__(self, explanations_out):
        self.explanations_out = explanations_out

    async def explain(self, payload: ExplanationInput):
        return self.explanations_out


def _patch_explainer(monkeypatch, provider):
    monkeypatch.setattr("app.services.explanations.explainer_provider", lambda request: provider)


def test_resolve_returns_grounded_explanation(monkeypatch):
    explanations._pending.clear()
    provider = _FakeProvider([
        Explanation(summary="A short fortress walk that fits your plan.",
                    why=["Great views"], data_confidence_note="No live traffic data."),
    ])
    _patch_explainer(monkeypatch, provider)
    req = AdventureRequest(lat=42.4, lon=18.7, anonymous_id="u")
    explanations.stash("req-1", [_rec()], req)

    out = asyncio.run(explanations.resolve("req-1"))
    assert out == [{
        "id": "r1",
        "summary": "A short fortress walk that fits your plan.",
        "why": ["Great views"],
        "data_confidence_note": "No live traffic data.",
    }]


def test_resolve_unknown_request_returns_empty(monkeypatch):
    explanations._pending.clear()
    assert asyncio.run(explanations.resolve("nope")) == []


def test_resolve_is_one_shot(monkeypatch):
    explanations._pending.clear()
    _patch_explainer(monkeypatch, _FakeProvider([Explanation(summary="Scores 86 here.", why=["ok"])]))
    explanations.stash("req-2", [_rec()], AdventureRequest(lat=42.4, lon=18.7))
    first = asyncio.run(explanations.resolve("req-2"))
    assert first and first[0]["summary"].startswith("Scores 86")
    assert asyncio.run(explanations.resolve("req-2")) == []


def test_stash_evicts_oldest_over_max(monkeypatch):
    explanations._pending.clear()
    # settings is a frozen dataclass, so swap the whole reference rather than
    # mutating an attribute on it.
    monkeypatch.setattr(
        "app.services.explanations.settings",
        SimpleNamespace(explanation_stash_ttl_seconds=300, explanation_stash_max_entries=2),
    )
    req = AdventureRequest(lat=42.4, lon=18.7)
    for i in range(4):
        explanations.stash(f"r{i}", [_rec()], req)
    assert len(explanations._pending) <= 2
