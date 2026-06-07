from app.services.storage import Storage


def test_seen_and_visited_round_trip(tmp_path):
    db = Storage(tmp_path / "marks.db")
    with db._connect() as conn:
        db._record_seen(conn, "u1", ["p1", "p2", None])  # None is skipped

    marks = db.place_marks("u1")
    assert marks["seen"] == {"p1", "p2"}
    assert marks["visited"] == set()

    # Marking visited keeps the existing seen flag on the same row.
    db.mark_visited("u1", "p1")
    marks = db.place_marks("u1")
    assert marks["visited"] == {"p1"}
    assert "p1" in marks["seen"]

    # Marking a not-yet-seen place creates a visited-only row.
    db.mark_visited("u1", "p3")
    assert db.place_marks("u1")["visited"] == {"p1", "p3"}

    # clear_visited resets visited but leaves seen intact.
    assert db.clear_visited("u1") == 2
    after = db.place_marks("u1")
    assert after["visited"] == set()
    assert after["seen"] == {"p1", "p2"}


def test_marks_are_per_user_and_no_op_without_id(tmp_path):
    db = Storage(tmp_path / "marks.db")
    db.mark_visited("alice", "p1")
    db.mark_visited(None, "p1")  # no-op
    db.mark_visited("bob", None)  # no-op
    assert db.place_marks("alice")["visited"] == {"p1"}
    assert db.place_marks("bob") == {"seen": set(), "visited": set()}
    assert db.place_marks(None) == {"seen": set(), "visited": set()}
    assert db.clear_visited(None) == 0


def test_delete_user_data_clears_marks(tmp_path):
    db = Storage(tmp_path / "marks.db")
    db.mark_visited("u1", "p1")
    with db._connect() as conn:
        db._record_seen(conn, "u1", ["p2"])
    db.delete_user_data("u1")
    assert db.place_marks("u1") == {"seen": set(), "visited": set()}
