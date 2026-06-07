from app.schemas import Recommendation, ScoreBreakdown
from app.services.llm.base import Explanation
from eval import metrics


def _rec(warnings=None, title="Old Fort", score=86) -> Recommendation:
    return Recommendation(
        id="r1", title=title, place_type="fortress", lat=42.4, lon=18.7,
        adventure_score=score,
        score_breakdown=ScoreBreakdown(
            time_fit=100, weather_fit=90, distance_fit=80, safety_fit=85,
            group_fit=80, interest_fit=88, place_quality=80, personal_preference_fit=70,
        ),
        total_minutes=120, travel_minutes=36, activity_minutes=80, distance_km=10.0, walking_km=2.0,
        difficulty="easy", description="x", why=["Fits your time."], warnings=warnings or [],
        map_url="https://maps.example/x", source="test",
    )


def test_format_valid():
    assert metrics.format_valid(Explanation(summary="ok", why=["a", "b"]))
    assert not metrics.format_valid(None)
    assert not metrics.format_valid(Explanation(summary="", why=["a"]))
    assert not metrics.format_valid(Explanation(summary="ok", why=[]))
    assert not metrics.format_valid(Explanation(summary="ok", why=["a", "b", "c", "d", "e"]))


def test_safety_preserved():
    rec = _rec(warnings=["Trail may be slippery after rain."])
    assert not metrics.safety_preserved(Explanation(summary="It is completely safe.", why=["x"]), rec)
    assert metrics.safety_preserved(Explanation(summary="A short walk with views.", why=["x"]), rec)
    # no warnings -> nothing to preserve
    assert metrics.safety_preserved(Explanation(summary="Anything goes.", why=["x"]), _rec(warnings=[]))


def test_faithful_coverage():
    rec = _rec(title="Sunset Viewpoint", score=86)
    assert metrics.faithful_coverage(Explanation(summary="Scores 86.", why=["Great", "Short walk"]), rec)
    assert metrics.faithful_coverage(Explanation(summary="The viewpoint is nice.", why=["a", "b"]), rec)
    assert not metrics.faithful_coverage(Explanation(summary="Nice place.", why=["one bullet only"]), rec)
    assert not metrics.faithful_coverage(Explanation(summary="Generic.", why=["totally", "vague"]), rec)


def test_estimate_cost():
    # 4000 chars ~= 1000 tokens; at $2/1k -> ~$2.0
    assert round(metrics.estimate_cost(4000, 0, 2.0), 4) == 2.0
    assert metrics.estimate_cost(0, 0, 5.0) == 0.0


def test_is_grounded_reexported():
    assert callable(metrics.is_grounded)
