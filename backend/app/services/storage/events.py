from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from app.schemas import AnalyticsEvent
from app.services.storage.db import Connection, Database


class EventsRepo:
    """The `events` table (analytics events). `insert` takes a connection so the
    user-touch and the row land in one transaction (coordinated by the facade).
    `summary` is a plain count-by-event, no rate logic."""

    def __init__(self, db: Database):
        self.db = db

    @staticmethod
    def insert(conn: Connection, event: AnalyticsEvent, account_id: int | None) -> None:
        conn.execute(
            "INSERT INTO events (created_at, event, request_id, recommendation_id, anonymous_id, account_id, meta) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                datetime.utcnow().isoformat(),
                event.event,
                event.request_id,
                event.recommendation_id,
                event.anonymous_id,
                account_id,
                json.dumps(event.meta) if event.meta else None,
            ),
        )

    def summary(self) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT event, COUNT(*) AS count FROM events GROUP BY event ORDER BY count DESC"
            ).fetchall()
        return [dict(row) for row in rows]
