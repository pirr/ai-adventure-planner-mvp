import json
from datetime import datetime, timedelta

from app.schemas import AdventureRequest, PlaceCandidate, RouteInfo, WeatherSummary
from app.services.community import community_signals
from app.services.scoring import score_candidate, to_recommendation
from app.services.storage import Storage


# --- storage aggregation -----------------------------------------------------

def _add_rating(db: Storage, source_id: str, rating: str, rater: str, *, account: bool = False) -> None:
    """Insert one thumbs rating for a place by one user, creating the backing
    recommendation row on demand (matches how save_response/save_feedback link)."""
    rec_id = source_id.replace(":", "_")
    with db._connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO recommendations (id, request_id, title, score, payload_json) VALUES (?, ?, ?, ?, ?)",
            (rec_id, "req1", "Place", 90, json.dumps({"source_id": source_id})),
        )
        col = "account_id" if account else "anonymous_id"
        conn.execute(
            f"INSERT INTO feedback (created_at, request_id, recommendation_id, rating, reason, {col}) VALUES (?, ?, ?, ?, ?, ?)",
            (datetime.utcnow().isoformat(), "req1", rec_id, rating, None, rater),
        )


def test_community_signals_counts_distinct_raters(tmp_path):
    db = Storage(tmp_path / "c.db")
    for i in range(5):
        _add_rating(db, "osm:1", "up", f"u{i}")
    _add_rating(db, "osm:1", "up", "u0")  # same user again -> not double counted
    _add_rating(db, "osm:1", "down", "d0")

    sig = community_signals(["osm:1", "osm:absent"], store=db)
    assert sig["osm:1"] == {"ups": 5, "downs": 1, "raters": 6, "recent_visits": 0, "want_to_visit": 0}
    assert "osm:absent" not in sig  # places with no signal are omitted (cold start)


def test_community_signals_counts_distinct_wanters(tmp_path):
    db = Storage(tmp_path / "c.db")
    a1 = db.accounts.create_email_account("a1@example.com", "hash")["id"]
    a2 = db.accounts.create_email_account("a2@example.com", "hash")["id"]
    db.place_marks.set_want_to_visit_account(a1, "osm:1", True)
    db.place_marks.set_want_to_visit_account(a2, "osm:1", True)
    db.place_marks.set_want_to_visit_account(a1, "osm:1", True)  # same account again -> not double counted
    db.place_marks.set_want_to_visit_account(a2, "osm:1", False)  # toggled off -> not counted

    sig = community_signals(["osm:1"], store=db)
    assert sig["osm:1"]["want_to_visit"] == 1  # only a1 still wants it


def test_community_signals_visits_use_window_and_both_mark_tables(tmp_path):
    db = Storage(tmp_path / "c.db")
    db.place_marks.mark_visited("v1", "osm:2")
    db.place_marks.mark_visited("v2", "osm:2")
    with db._connect() as conn:
        # Beyond the default 90-day window -> excluded.
        old = (datetime.utcnow() - timedelta(days=120)).isoformat()
        conn.execute(
            "INSERT INTO place_marks (anonymous_id, source_id, visited, updated_at) VALUES (?, ?, 1, ?)",
            ("v3_old", "osm:2", old),
        )
        # a recent account-side visit exercises the UNION branch
        conn.execute(
            "INSERT INTO account_place_marks (account_id, source_id, visited, updated_at) VALUES (?, ?, 1, ?)",
            (99, "osm:2", datetime.utcnow().isoformat()),
        )

    sig = community_signals(["osm:2"], store=db)
    assert sig["osm:2"]["recent_visits"] == 3  # v1, v2, account 99; the 120-day-old visit is excluded


def test_community_signals_no_ids_is_empty(tmp_path):
    db = Storage(tmp_path / "c.db")
    assert community_signals([], store=db) == {}
    assert community_signals([None], store=db) == {}


# --- scoring integration -----------------------------------------------------

def _fixtures(source_id: str = "osm:1"):
    request = AdventureRequest(lat=42.43, lon=18.69, available_minutes=300, transport_mode="car", interests=["history"])
    weather = WeatherSummary(source="test", summary="clear", score=90, confidence="estimated")
    place = PlaceCandidate(source="osm", source_id=source_id, name="Fort", type="fortress", lat=42.45, lon=18.70, quality_score=80)
    route = RouteInfo(source="test", one_way_minutes=18, round_trip_minutes=36, distance_km=10, map_url="x", confidence="estimated")
    return place, route, weather, request


def test_community_confidence_cold_start_is_neutral_and_no_badge():
    place, route, weather, request = _fixtures()
    cold = score_candidate(place, route, weather, request)  # no community signal
    assert cold.breakdown.community_confidence == 70
    assert to_recommendation(cold).community is None


def test_community_confidence_moves_with_signal_and_shrinks_by_sample():
    place, route, weather, request = _fixtures()
    thin = {"osm:1": {"ups": 1, "downs": 0, "raters": 1, "recent_visits": 0}}
    mid = {"osm:1": {"ups": 3, "downs": 0, "raters": 3, "recent_visits": 0}}
    strong = {"osm:1": {"ups": 20, "downs": 0, "raters": 20, "recent_visits": 0}}

    thin_fit = score_candidate(place, route, weather, request, None, thin).breakdown.community_confidence
    mid_fit = score_candidate(place, route, weather, request, None, mid).breakdown.community_confidence
    strong_fit = score_candidate(place, route, weather, request, None, strong).breakdown.community_confidence

    assert thin_fit == 70  # below the min-raters gate, stays neutral
    assert 70 < mid_fit < strong_fit  # shrinkage: more votes move it further from neutral


def test_community_negative_signal_lowers_score():
    place, route, weather, request = _fixtures()
    cold = score_candidate(place, route, weather, request)
    disliked = score_candidate(place, route, weather, request, None, {"osm:1": {"ups": 0, "downs": 12, "raters": 12, "recent_visits": 0}})
    assert disliked.breakdown.community_confidence < 70
    assert disliked.score < cold.score


def test_community_badge_thresholds():
    place, route, weather, request = _fixtures()

    # neither dimension clears its bar -> no badge
    low = {"osm:1": {"ups": 2, "downs": 1, "raters": 3, "recent_visits": 1}}
    assert to_recommendation(score_candidate(place, route, weather, request, None, low), None, low).community is None

    # liked chip appears at >= 5 raters; visits chip hidden (recent_visits -> 0)
    liked = {"osm:1": {"ups": 8, "downs": 2, "raters": 10, "recent_visits": 0}}
    rec = to_recommendation(score_candidate(place, route, weather, request, None, liked), None, liked).community
    assert rec is not None and rec.sample_size == 10 and rec.recent_visits == 0
    assert abs(rec.liked_ratio - 0.8) < 1e-6

    # visits chip appears at >= 3 visits even when raters are too few for the liked chip
    visited = {"osm:1": {"ups": 1, "downs": 0, "raters": 1, "recent_visits": 5}}
    rec_v = to_recommendation(score_candidate(place, route, weather, request, None, visited), None, visited).community
    assert rec_v is not None and rec_v.recent_visits == 5 and rec_v.sample_size == 0


def test_want_to_visit_raises_score_above_neutral_when_gated():
    place, route, weather, request = _fixtures()
    below = {"osm:1": {"want_to_visit": 2}}  # below community_min_raters(3)
    some = {"osm:1": {"want_to_visit": 5}}
    many = {"osm:1": {"want_to_visit": 20}}
    below_fit = score_candidate(place, route, weather, request, None, below).breakdown.community_confidence
    some_fit = score_candidate(place, route, weather, request, None, some).breakdown.community_confidence
    many_fit = score_candidate(place, route, weather, request, None, many).breakdown.community_confidence
    assert below_fit == 70  # intent below the gate doesn't move the score
    assert 70 < some_fit < many_fit  # demand nudges up, saturating with more wanters


def test_want_to_visit_badge_threshold():
    place, route, weather, request = _fixtures()
    # below the wants badge bar (default 5) and no other dimension clears -> no badge
    low = {"osm:1": {"ups": 0, "downs": 0, "raters": 0, "recent_visits": 0, "want_to_visit": 4}}
    assert to_recommendation(score_candidate(place, route, weather, request, None, low), None, low).community is None
    # at/above the bar -> "want to go" chip appears; other dimensions stay hidden (0)
    want = {"osm:1": {"ups": 0, "downs": 0, "raters": 0, "recent_visits": 0, "want_to_visit": 6}}
    rec = to_recommendation(score_candidate(place, route, weather, request, None, want), None, want).community
    assert rec is not None and rec.want_to_visit == 6 and rec.sample_size == 0 and rec.recent_visits == 0


# --- env-driven tuning -------------------------------------------------------

def test_community_gates_are_env_tunable(monkeypatch):
    import types
    from app.config import settings as real
    from app.services import scoring

    place, route, weather, request = _fixtures()
    two_up = {"osm:1": {"ups": 2, "downs": 0, "raters": 2, "recent_visits": 0}}

    # Default gates: 2 ratings is below COMMUNITY_MIN_RATERS(3) and COMMUNITY_BADGE_MIN_RATERS(5).
    assert score_candidate(place, route, weather, request, None, two_up).breakdown.community_confidence == 70
    assert to_recommendation(score_candidate(place, route, weather, request, None, two_up), None, two_up).community is None

    # Relaxed gates (as a small audience would set via env) make the same data count.
    relaxed = types.SimpleNamespace(
        community_min_raters=2, community_shrink_prior=real.community_shrink_prior,
        community_badge_min_raters=2, community_badge_min_visits=real.community_badge_min_visits,
        community_want_span=real.community_want_span, community_badge_min_wants=real.community_badge_min_wants,
    )
    monkeypatch.setattr(scoring, "settings", relaxed)
    assert score_candidate(place, route, weather, request, None, two_up).breakdown.community_confidence > 70
    badge = to_recommendation(score_candidate(place, route, weather, request, None, two_up), None, two_up).community
    assert badge is not None and badge.sample_size == 2


def test_visit_window_is_configurable(tmp_path, monkeypatch):
    import types
    from app.services import community as community_mod

    db = Storage(tmp_path / "c.db")
    with db._connect() as conn:
        sixty = (datetime.utcnow() - timedelta(days=60)).isoformat()
        conn.execute(
            "INSERT INTO place_marks (anonymous_id, source_id, visited, updated_at) VALUES (?, ?, 1, ?)",
            ("u", "osm:9", sixty),
        )
    # Default 90-day window includes a 60-day-old visit...
    assert community_signals(["osm:9"], store=db)["osm:9"]["recent_visits"] == 1
    # ...narrowing the window to 30 days drops it (and the place, having no other signal).
    monkeypatch.setattr(community_mod, "settings", types.SimpleNamespace(community_visit_window_days=30))
    assert "osm:9" not in community_signals(["osm:9"], store=db)
