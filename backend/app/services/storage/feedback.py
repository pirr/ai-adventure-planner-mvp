from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from app.schemas import FeedbackRequest
from app.services.storage.db import Database


class FeedbackRepo:
    """The `feedback` table (thumbs ratings). `insert` takes a connection so the
    user-touch and the row land in one transaction (coordinated by the facade).
    `feedback_with_payload` returns raw rows; ``services/preferences.py`` turns
    them into per-place-type scores."""

    def __init__(self, db: Database):
        self.db = db

    @staticmethod
    def insert(conn: sqlite3.Connection, feedback: FeedbackRequest, account_id: int | None) -> None:
        conn.execute(
            "INSERT INTO feedback (created_at, request_id, recommendation_id, rating, reason, anonymous_id, account_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (datetime.utcnow().isoformat(), feedback.request_id, feedback.recommendation_id, feedback.rating, feedback.reason, feedback.anonymous_id, account_id),
        )

    def summary(self) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute("SELECT * FROM feedback ORDER BY created_at DESC LIMIT 100").fetchall()
        return [dict(row) for row in rows]

    def feedback_with_payload(
        self, *, anonymous_id: str | None = None, account_id: int | None = None
    ) -> list[sqlite3.Row]:
        """Each of this user's ratings joined to the recommendation payload it
        targeted: rows of ``(rating, payload_json)``. Keyed by anonymous_id or
        account_id; returns ``[]`` when neither is given."""
        if account_id is not None:
            column, value = "account_id", account_id
        elif anonymous_id:
            column, value = "anonymous_id", anonymous_id
        else:
            return []
        with self.db.connect() as conn:
            return conn.execute(
                f"""
                SELECT f.rating AS rating, r.payload_json AS payload_json
                FROM feedback f
                JOIN recommendations r ON r.request_id = f.request_id AND r.id = f.recommendation_id
                WHERE f.{column} = ?
                """,
                (value,),
            ).fetchall()
