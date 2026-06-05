from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import settings
from app.schemas import AdventureRequest, AdventureResponse, FeedbackRequest, Recommendation


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
                CREATE TABLE IF NOT EXISTS search_sessions (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
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
                    reason TEXT
                );
                """
            )

    def save_response(self, request_id: str, request: AdventureRequest, response: AdventureResponse) -> None:
        response_json = response.json()
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO search_sessions (id, created_at, lat, lon, request_json, response_json) VALUES (?, ?, ?, ?, ?, ?)",
                (request_id, datetime.utcnow().isoformat(), request.lat, request.lon, request.json(), response_json),
            )
            for rec in response.recommendations:
                conn.execute(
                    "INSERT OR REPLACE INTO recommendations (id, request_id, title, score, payload_json) VALUES (?, ?, ?, ?, ?)",
                    (rec.id, request_id, rec.title, rec.adventure_score, rec.json()),
                )

    def save_feedback(self, feedback: FeedbackRequest) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO feedback (created_at, request_id, recommendation_id, rating, reason) VALUES (?, ?, ?, ?, ?)",
                (datetime.utcnow().isoformat(), feedback.request_id, feedback.recommendation_id, feedback.rating, feedback.reason),
            )

    def feedback_summary(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM feedback ORDER BY created_at DESC LIMIT 100").fetchall()
        return [dict(row) for row in rows]


storage = Storage()
