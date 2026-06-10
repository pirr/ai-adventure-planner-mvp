import asyncio

from app.schemas import AdventureRequest
from app.services.llm import TemplateProvider
from app.services.recommendations import build_recommendations
from app.services.storage import Storage

# Offline path: sample places, no network (mirrors backend/eval/make_golden.py).
TIVAT = dict(
    lat=42.4304,
    lon=18.6964,
    available_minutes=300,
    transport_mode="car",
    use_live_data=False,
    interests=["history", "fortresses", "viewpoints"],
)

TIVAT_WALK = dict(
    lat=42.4304,
    lon=18.6964,
    available_minutes=120,
    transport_mode="walk",
    use_live_data=False,
    interests=["history", "fortresses", "viewpoints"],
)


def _run(req: AdventureRequest):
    return asyncio.run(build_recommendations(req, provider=TemplateProvider()))


def _store(tmp_path, monkeypatch) -> Storage:
    store = Storage(tmp_path / "rotation.db")
    monkeypatch.setattr("app.services.recommendations.storage", store)
    return store


def test_visited_place_is_never_recommended(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    store.mark_visited("u", "sample:kotor-old-town")  # otherwise a top pick here
    resp = _run(AdventureRequest(**TIVAT, anonymous_id="u", limit=5))
    assert resp.recommendations  # the rest still come through
    assert "sample:kotor-old-town" not in {r.source_id for r in resp.recommendations}


def test_walk_search_excludes_far_over_budget_places(tmp_path, monkeypatch):
    _store(tmp_path, monkeypatch)
    resp = _run(AdventureRequest(**TIVAT_WALK, group_type="solo", limit=5))
    ids = {r.source_id for r in resp.recommendations}

    assert resp.recommendations
    assert "sample:budva-old-town" not in ids
    assert "sample:kotor-old-town" not in ids
    assert all(r.travel_minutes <= TIVAT_WALK["available_minutes"] for r in resp.recommendations)
    assert all(r.total_minutes <= TIVAT_WALK["available_minutes"] + 15 for r in resp.recommendations)
    assert all(r.score_breakdown.distance_fit > 15 for r in resp.recommendations)


def test_show_others_rotates_to_unseen(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    req = AdventureRequest(**TIVAT, anonymous_id="u", limit=3)
    first = _run(req)
    ids1 = {r.source_id for r in first.recommendations}
    assert len(ids1) == 3
    store.save_response(first.request_id, req, first)  # records the 3 as seen

    second = _run(AdventureRequest(**TIVAT, anonymous_id="u", limit=3, exclude_seen=True))
    ids2 = {r.source_id for r in second.recommendations}
    assert ids2 and ids1.isdisjoint(ids2)  # fresh places surface first


def test_rotation_falls_back_when_everything_is_seen(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    seed = AdventureRequest(**TIVAT, anonymous_id="u", limit=10)  # capture all candidates
    resp = _run(seed)
    store.save_response(resp.request_id, seed, resp)  # all candidates now seen

    again = _run(AdventureRequest(**TIVAT, anonymous_id="u", limit=3, exclude_seen=True))
    assert again.recommendations  # graceful fallback, never empty


def test_no_anonymous_id_keeps_default_pipeline(tmp_path, monkeypatch):
    _store(tmp_path, monkeypatch)
    # exclude_seen with no id must not filter or reorder anything.
    a = _run(AdventureRequest(**TIVAT, limit=5))
    b = _run(AdventureRequest(**TIVAT, limit=5, exclude_seen=True))
    assert [r.source_id for r in a.recommendations] == [r.source_id for r in b.recommendations]
