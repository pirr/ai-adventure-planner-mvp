from app.services.analytics import ab_summary
from app.services.recommendations import _ab_bucket
from app.services.storage import Storage


def test_ab_bucket_is_deterministic_and_splits():
    assert _ab_bucket("user-x") == _ab_bucket("user-x")
    assert {_ab_bucket(f"u{i}") for i in range(50)} == {0, 1}  # both buckets reachable


def test_ab_summary(tmp_path):
    db = Storage(tmp_path / "ab.db")
    with db._connect() as conn:
        for sid, explainer in [("s1", "llm"), ("s2", "llm"), ("s3", "template")]:
            conn.execute(
                "INSERT INTO search_sessions (id, created_at, lat, lon, request_json, response_json, explainer) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (sid, "t", 42.0, 18.0, "{}", "{}", explainer),
            )
        for request_id, rating in [("s1", "up"), ("s2", "down"), ("s3", "up")]:
            conn.execute(
                "INSERT INTO feedback (created_at, request_id, recommendation_id, rating) VALUES (?, ?, ?, ?)",
                ("t", request_id, "x", rating),
            )
        for request_id in ["s1", "s3"]:
            conn.execute(
                "INSERT INTO events (created_at, event, request_id) VALUES (?, ?, ?)", ("t", "maps_opened", request_id)
            )

    summary = {row["variant"]: row for row in ab_summary(db)}
    assert summary["llm"]["sessions"] == 2
    assert summary["llm"]["feedback"] == 2
    assert summary["llm"]["thumbs_up_rate"] == 0.5
    assert summary["llm"]["maps_opened"] == 1
    assert summary["llm"]["maps_open_rate"] == 0.5
    assert summary["template"]["sessions"] == 1
    assert summary["template"]["thumbs_up_rate"] == 1.0
    assert summary["template"]["maps_open_rate"] == 1.0
