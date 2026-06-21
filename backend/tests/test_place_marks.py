from app.services.storage import Storage


def test_seen_and_visited_round_trip(tmp_path):
    db = Storage(tmp_path / "marks.db")
    with db._connect() as conn:
        db.place_marks.record_seen(conn, "u1", ["p1", "p2", None])  # None is skipped

    marks = db.place_marks.place_marks("u1")
    assert marks["seen"] == {"p1", "p2"}
    assert marks["visited"] == set()

    # Marking visited keeps the existing seen flag on the same row.
    db.place_marks.mark_visited("u1", "p1")
    marks = db.place_marks.place_marks("u1")
    assert marks["visited"] == {"p1"}
    assert "p1" in marks["seen"]

    # Marking a not-yet-seen place creates a visited-only row.
    db.place_marks.mark_visited("u1", "p3")
    assert db.place_marks.place_marks("u1")["visited"] == {"p1", "p3"}

    # clear_visited resets visited but leaves seen intact.
    assert db.place_marks.clear_visited("u1") == 2
    after = db.place_marks.place_marks("u1")
    assert after["visited"] == set()
    assert after["seen"] == {"p1", "p2"}


def test_marks_are_per_user_and_no_op_without_id(tmp_path):
    db = Storage(tmp_path / "marks.db")
    db.place_marks.mark_visited("alice", "p1")
    db.place_marks.mark_visited(None, "p1")  # no-op
    db.place_marks.mark_visited("bob", None)  # no-op
    assert db.place_marks.place_marks("alice")["visited"] == {"p1"}
    assert db.place_marks.place_marks("bob") == {"seen": set(), "visited": set(), "want_to_visit": set()}
    assert db.place_marks.place_marks(None) == {"seen": set(), "visited": set(), "want_to_visit": set()}
    assert db.place_marks.clear_visited(None) == 0


def test_delete_user_data_clears_marks(tmp_path):
    db = Storage(tmp_path / "marks.db")
    db.place_marks.mark_visited("u1", "p1")
    with db._connect() as conn:
        db.place_marks.record_seen(conn, "u1", ["p2"])
    db.lifecycle.delete_user_data("u1")
    assert db.place_marks.place_marks("u1") == {"seen": set(), "visited": set(), "want_to_visit": set()}


def test_want_to_visit_account_round_trip(tmp_path):
    db = Storage(tmp_path / "marks.db")
    account_id = db.accounts.create_email_account("a@example.com", "hash")["id"]

    db.place_marks.set_want_to_visit_account(account_id, "p1", True)
    db.place_marks.set_want_to_visit_account(account_id, "p2", True)
    assert db.place_marks.account_place_marks(account_id)["want_to_visit"] == {"p1", "p2"}

    # Toggling off removes it from the set but keeps the row.
    db.place_marks.set_want_to_visit_account(account_id, "p1", False)
    assert db.place_marks.account_place_marks(account_id)["want_to_visit"] == {"p2"}

    # No-ops without an id / source.
    db.place_marks.set_want_to_visit_account(None, "p3", True)
    db.place_marks.set_want_to_visit_account(account_id, None, True)
    assert db.place_marks.account_place_marks(account_id)["want_to_visit"] == {"p2"}


def test_wanted_places_account_lists_with_recommendation_info(tmp_path):
    import json

    db = Storage(tmp_path / "marks.db")
    account_id = db.accounts.create_email_account("a@example.com", "hash")["id"]
    with db._connect() as conn:
        conn.execute(
            "INSERT INTO recommendations (id, request_id, title, score, payload_json) VALUES (?, ?, ?, ?, ?)",
            ("osm_1", "req1", "Old Fort", 88, json.dumps({"source_id": "osm:1", "map_url": "https://maps/1"})),
        )
    db.place_marks.set_want_to_visit_account(account_id, "osm:1", True)
    db.place_marks.set_want_to_visit_account(account_id, "osm:absent", True)  # no recommendation row -> title falls back

    items = db.place_marks.wanted_places_account(account_id)
    by_id = {item["source_id"]: item for item in items}
    assert by_id["osm:1"]["title"] == "Old Fort"
    assert by_id["osm:1"]["score"] == 88
    assert by_id["osm:1"]["map_url"] == "https://maps/1"
    assert by_id["osm:absent"]["title"] == "osm:absent"  # fallback

    assert db.place_marks.clear_want_to_visit_account(account_id) == 2
    assert db.place_marks.wanted_places_account(account_id) == []
