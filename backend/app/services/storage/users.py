from __future__ import annotations

import sqlite3
from datetime import datetime


class UsersRepo:
    """The `users` table: one row per anonymous_id, upserted on activity.

    `touch` takes a connection so callers can fold it into the same transaction
    that records the activity (a search, feedback, an event)."""

    @staticmethod
    def touch(conn: sqlite3.Connection, anonymous_id: str | None, locale: str | None = None) -> None:
        if not anonymous_id:
            return
        conn.execute(
            "INSERT INTO users (anonymous_id, created_at, locale) VALUES (?, ?, ?) "
            "ON CONFLICT(anonymous_id) DO UPDATE SET locale=COALESCE(excluded.locale, users.locale)",
            (anonymous_id, datetime.utcnow().isoformat(), locale),
        )
