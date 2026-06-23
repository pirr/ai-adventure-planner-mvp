import asyncio
from types import SimpleNamespace

from app.schemas import AdventureRequest
from app.services import explanations
from app.services import recommendations as recs_module
from app.services.llm import Explanation, ExplanationInput, LLMProvider
from app.services.storage import Storage

# Tivat with live data off, so build_recommendations runs fully offline on the
# bundled sample places (no Overpass / OSRM / weather network calls).
_TIVAT = dict(
    lat=42.4304, lon=18.6964, available_minutes=300, transport_mode="car",
    use_live_data=False, interests=["history", "fortresses", "viewpoints"],
)

_DEFAULT_FLAGS = dict(anon_disable_llm_explanations=True, anon_fast_weather=True)


# --- the policy in isolation -------------------------------------------------

def test_experience_tier_anonymous_is_lite(monkeypatch):
    monkeypatch.setattr(recs_module, "settings", SimpleNamespace(**_DEFAULT_FLAGS))
    assert recs_module._experience_tier(None) == recs_module.ExperienceTier(
        llm_explanations=False, destination_forecast=False, cache_origin_weather=True
    )


def test_experience_tier_account_is_full(monkeypatch):
    monkeypatch.setattr(recs_module, "settings", SimpleNamespace(**_DEFAULT_FLAGS))
    assert recs_module._experience_tier(7) == recs_module.ExperienceTier(
        llm_explanations=True, destination_forecast=True, cache_origin_weather=False
    )


def test_experience_tier_flags_off_restore_full_for_anonymous(monkeypatch):
    monkeypatch.setattr(
        recs_module, "settings",
        SimpleNamespace(anon_disable_llm_explanations=False, anon_fast_weather=False),
    )
    assert recs_module._experience_tier(None) == recs_module.ExperienceTier(
        llm_explanations=True, destination_forecast=True, cache_origin_weather=False
    )


# --- the policy applied in build_recommendations -----------------------------

class _RecordingProvider(LLMProvider):
    name = "recording"

    def __init__(self):
        self.called = False

    async def explain(self, payload: ExplanationInput):
        self.called = True
        return [Explanation(summary="Scores 86 here.", why=["ok"]) for _ in payload.recommendations]


def test_anonymous_skips_llm_and_keeps_rule_based(tmp_path, monkeypatch):
    explanations._pending.clear()
    monkeypatch.setattr("app.services.recommendations.storage", Storage(tmp_path / "anon.db"))
    provider = _RecordingProvider()
    # Even with an LLM configured, an anonymous request must not reach it.
    monkeypatch.setattr("app.services.recommendations.explainer_provider", lambda request: provider)
    resp = asyncio.run(recs_module.build_recommendations(
        AdventureRequest(**_TIVAT, anonymous_id="u", limit=3), account_id=None,
    ))
    assert resp.explanations_pending is False
    assert provider.called is False
    assert resp.recommendations and all(rec.why for rec in resp.recommendations)


def test_account_uses_llm_explainer(tmp_path, monkeypatch):
    explanations._pending.clear()
    monkeypatch.setattr("app.services.recommendations.storage", Storage(tmp_path / "acct.db"))
    provider = _RecordingProvider()
    monkeypatch.setattr("app.services.recommendations.explainer_provider", lambda request: provider)
    resp = asyncio.run(recs_module.build_recommendations(
        AdventureRequest(**_TIVAT, anonymous_id="u", limit=3),
        account_id=42, defer_explanations=False,
    ))
    assert provider.called is True
