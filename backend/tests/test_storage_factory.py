import json
from dataclasses import replace
from datetime import datetime

import pytest

from app.config import settings
from app.services.storage import SqliteDatabase, Storage, create_database


def test_create_database_defaults_to_sqlite(tmp_path):
    cfg = replace(settings, storage_backend="sqlite", sqlite_path=tmp_path / "x.db")
    db = create_database(cfg)
    assert isinstance(db, SqliteDatabase)
    assert db.path == tmp_path / "x.db"


def test_create_database_postgres_not_implemented():
    cfg = replace(settings, storage_backend="postgres")
    with pytest.raises(NotImplementedError):
        create_database(cfg)


def test_create_database_unknown_backend_raises_value_error():
    cfg = replace(settings, storage_backend="mongo")
    with pytest.raises(ValueError):
        create_database(cfg)


def test_feedback_with_payload_returns_plain_dicts(tmp_path):
    db = Storage(tmp_path / "fb.db")
    with db._connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO recommendations (id, request_id, title, score, payload_json) VALUES (?, ?, ?, ?, ?)",
            ("rec1", "req1", "Place", 90, json.dumps({"place_type": "park"})),
        )
        conn.execute(
            "INSERT INTO feedback (created_at, request_id, recommendation_id, rating, reason, anonymous_id) VALUES (?, ?, ?, ?, ?, ?)",
            (datetime.utcnow().isoformat(), "req1", "rec1", "up", None, "u1"),
        )

    rows = db.feedback.feedback_with_payload(anonymous_id="u1")
    assert rows == [{"rating": "up", "payload_json": json.dumps({"place_type": "park"})}]
    assert all(type(row) is dict for row in rows)  # not a driver Row type
