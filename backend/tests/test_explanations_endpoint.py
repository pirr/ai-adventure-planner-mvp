from fastapi.testclient import TestClient

from app.main import app
from app.schemas import AdventureRequest, Recommendation, ScoreBreakdown
from app.services import explanations
from app.services.llm import Explanation, ExplanationInput, LLMProvider

client = TestClient(app)


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

    async def explain(self, payload: ExplanationInput):
        return [Explanation(summary="A short fortress walk that fits.", why=["Great views"])]


def test_explanations_endpoint_returns_grounded_prose(monkeypatch):
    explanations._pending.clear()
    monkeypatch.setattr("app.services.explanations.explainer_provider", lambda request: _FakeProvider())
    explanations.stash("req-1", [_rec()], AdventureRequest(lat=42.4, lon=18.7))

    resp = client.post("/api/explanations", json={"request_id": "req-1"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["request_id"] == "req-1"
    assert body["explanations"][0]["summary"] == "A short fortress walk that fits."
    assert body["explanations"][0]["why"] == ["Great views"]


def test_explanations_endpoint_unknown_id_is_empty():
    explanations._pending.clear()
    resp = client.post("/api/explanations", json={"request_id": "missing"})
    assert resp.status_code == 200
    assert resp.json() == {"request_id": "missing", "explanations": []}
