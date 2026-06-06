from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import settings
from app.schemas import AdventureRequest, AdventureResponse, AnalyticsEvent, FeedbackRequest, Recommendation


class Storage:
    def __init__(self, path: Path = settings.sqlite_path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    anonymous_id TEXT UNIQUE NOT NULL,
                    created_at TEXT NOT NULL,
                    locale TEXT
                );

                CREATE TABLE IF NOT EXISTS search_sessions (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    anonymous_id TEXT,
                    lat REAL NOT NULL,
                    lon REAL NOT NULL,
                    request_json TEXT NOT NULL,
                    response_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS recommendations (
                    id TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    score INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (request_id, id)
                );

                CREATE TABLE IF NOT EXISTS feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    recommendation_id TEXT NOT NULL,
                    rating TEXT NOT NULL,
                    reason TEXT,
                    anonymous_id TEXT
                );

                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    event TEXT NOT NULL,
                    request_id TEXT,
                    recommendation_id TEXT,
                    anonymous_id TEXT,
                    meta TEXT
                );
                """
            )
            # Migrate pre-0.2 databases that predate the anonymous_id columns.
            for table in ("search_sessions", "feedback", "events"):
                self._ensure_column(conn, table, "anonymous_id", "TEXT")

    @staticmethod
    def _ensure_column(conn: sqlite3.Connection, table: str, column: str, decl: str) -> None:
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")

    @staticmethod
    def _touch_user(conn: sqlite3.Connection, anonymous_id: str | None, locale: str | None = None) -> None:
        if not anonymous_id:
            return
        conn.execute(
            "INSERT INTO users (anonymous_id, created_at, locale) VALUES (?, ?, ?) "
            "ON CONFLICT(anonymous_id) DO UPDATE SET locale=COALESCE(excluded.locale, users.locale)",
            (anonymous_id, datetime.utcnow().isoformat(), locale),
        )

    def save_response(self, request_id: str, request: AdventureRequest, response: AdventureResponse) -> None:
        response_json = response.json()
        with self._connect() as conn:
            self._touch_user(conn, request.anonymous_id, request.lang)
            conn.execute(
                "INSERT OR REPLACE INTO search_sessions (id, created_at, anonymous_id, lat, lon, request_json, response_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (request_id, datetime.utcnow().isoformat(), request.anonymous_id, request.lat, request.lon, request.json(), response_json),
            )
            for rec in response.recommendations:
                conn.execute(
                    "INSERT OR REPLACE INTO recommendations (id, request_id, title, score, payload_json) VALUES (?, ?, ?, ?, ?)",
                    (rec.id, request_id, rec.title, rec.adventure_score, rec.json()),
                )

    def save_feedback(self, feedback: FeedbackRequest) -> None:
        with self._connect() as conn:
            self._touch_user(conn, feedback.anonymous_id)
            conn.execute(
                "INSERT INTO feedback (created_at, request_id, recommendation_id, rating, reason, anonymous_id) VALUES (?, ?, ?, ?, ?, ?)",
                (datetime.utcnow().isoformat(), feedback.request_id, feedback.recommendation_id, feedback.rating, feedback.reason, feedback.anonymous_id),
            )

    def feedback_summary(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM feedback ORDER BY created_at DESC LIMIT 100").fetchall()
        return [dict(row) for row in rows]

    def save_event(self, event: AnalyticsEvent) -> None:
        with self._connect() as conn:
            self._touch_user(conn, event.anonymous_id)
            conn.execute(
                "INSERT INTO events (created_at, event, request_id, recommendation_id, anonymous_id, meta) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    datetime.utcnow().isoformat(),
                    event.event,
                    event.request_id,
                    event.recommendation_id,
                    event.anonymous_id,
                    json.dumps(event.meta) if event.meta else None,
                ),
            )

    def events_summary(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT event, COUNT(*) AS count FROM events GROUP BY event ORDER BY count DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def history_for(self, anonymous_id: str | None, limit: int = 20) -> list[dict[str, Any]]:
        if not anonymous_id:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT r.id AS id, r.request_id AS request_id, r.title AS title,
                       r.score AS score, s.created_at AS created_at
                FROM recommendations r
                JOIN search_sessions s ON s.id = r.request_id
                WHERE s.anonymous_id = ?
                ORDER BY s.created_at DESC, r.score DESC
                LIMIT ?
                """,
                (anonymous_id, limit),
            ).fetchall()
            opened = {
                row["recommendation_id"]
                for row in conn.execute(
                    "SELECT DISTINCT recommendation_id FROM events "
                    "WHERE anonymous_id = ? AND recommendation_id IS NOT NULL "
                    "AND event IN ('recommendation_opened', 'maps_opened')",
                    (anonymous_id,),
                )
            }
        items = []
        for row in rows:
            item = dict(row)
            item["opened"] = item["id"] in opened
            items.append(item)
        return items

    def preference_profile(self, anonymous_id: str | None) -> dict[str, Any]:
        """Net up/down feedback per place type for one anonymous user.

        Returns ``{"place_types": {type: net_score}}`` where net_score = (#up - #down).
        Empty when the user has no feedback yet (cold start → neutral scoring).
        """
        if not anonymous_id:
            return {}
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT f.rating AS rating, r.payload_json AS payload_json
                FROM feedback f
                JOIN recommendations r ON r.request_id = f.request_id AND r.id = f.recommendation_id
                WHERE f.anonymous_id = ?
                """,
                (anonymous_id,),
            ).fetchall()
        place_types: dict[str, int] = {}
        for row in rows:
            try:
                place_type = json.loads(row["payload_json"]).get("place_type")
            except (TypeError, ValueError):
                continue
            if not place_type:
                continue
            place_types[place_type] = place_types.get(place_type, 0) + (1 if row["rating"] == "up" else -1)
        return {"place_types": place_types} if place_types else {}

    def delete_user_data(self, anonymous_id: str | None) -> int:
        if not anonymous_id:
            return 0
        with self._connect() as conn:
            session_ids = [row["id"] for row in conn.execute(
                "SELECT id FROM search_sessions WHERE anonymous_id = ?", (anonymous_id,)
            )]
            if session_ids:
                placeholders = ",".join("?" * len(session_ids))
                conn.execute(f"DELETE FROM recommendations WHERE request_id IN ({placeholders})", session_ids)
            conn.execute("DELETE FROM search_sessions WHERE anonymous_id = ?", (anonymous_id,))
            conn.execute("DELETE FROM feedback WHERE anonymous_id = ?", (anonymous_id,))
            conn.execute("DELETE FROM events WHERE anonymous_id = ?", (anonymous_id,))
            conn.execute("DELETE FROM users WHERE anonymous_id = ?", (anonymous_id,))
        return len(session_ids)


storage = Storage()
