from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from app.schemas import AdventureRequest, Recommendation
from app.services.storage.db import Database


class SearchRepo:
    """The `search_sessions` and `recommendations` tables: persisted searches and
    the cards they produced. Writes take a connection so the session, its
    recommendations and the user/seen bookkeeping commit in one transaction
    (coordinated by ``services/search.py``). The A/B numbers it exposes are raw
    SQL-grouped counts; rate computation lives in ``services/analytics.py``."""

    def __init__(self, db: Database):
        self.db = db

    @staticmethod
    def insert_session(
        conn: sqlite3.Connection,
        request_id: str,
        request: AdventureRequest,
        account_id: int | None,
        response_json: str,
        explainer: str,
    ) -> None:
        conn.execute(
            "INSERT OR REPLACE INTO search_sessions (id, created_at, anonymous_id, account_id, lat, lon, request_json, response_json, explainer) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (request_id, datetime.utcnow().isoformat(), request.anonymous_id, account_id, request.lat, request.lon, request.json(), response_json, explainer),
        )

    @staticmethod
    def insert_recommendations(conn: sqlite3.Connection, request_id: str, recommendations: list[Recommendation]) -> None:
        for rec in recommendations:
            conn.execute(
                "INSERT OR REPLACE INTO recommendations (id, request_id, title, score, payload_json) VALUES (?, ?, ?, ?, ?)",
                (rec.id, request_id, rec.title, rec.adventure_score, rec.json()),
            )

    def history_for(self, anonymous_id: str | None, limit: int = 20) -> list[dict[str, Any]]:
        if not anonymous_id:
            return []
        with self.db.connect() as conn:
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

    def history_for_account(self, account_id: int | None, limit: int = 20) -> list[dict[str, Any]]:
        if account_id is None:
            return []
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT r.id AS id, r.request_id AS request_id, r.title AS title,
                       r.score AS score, s.created_at AS created_at
                FROM recommendations r
                JOIN search_sessions s ON s.id = r.request_id
                WHERE s.account_id = ?
                ORDER BY s.created_at DESC, r.score DESC
                LIMIT ?
                """,
                (account_id, limit),
            ).fetchall()
            opened = {
                row["recommendation_id"]
                for row in conn.execute(
                    "SELECT DISTINCT recommendation_id FROM events "
                    "WHERE account_id = ? AND recommendation_id IS NOT NULL "
                    "AND event IN ('recommendation_opened', 'maps_opened')",
                    (account_id,),
                )
            }
        items = []
        for row in rows:
            item = dict(row)
            item["opened"] = item["id"] in opened
            items.append(item)
        return items

    def ab_raw_counts(self) -> dict[str, dict]:
        """Raw per-explainer counts joined session<->feedback/events. The
        analytics service turns these into thumbs-up / maps-open *rates*."""
        with self.db.connect() as conn:
            sessions = {
                row["explainer"]: row["n"]
                for row in conn.execute("SELECT explainer, COUNT(*) AS n FROM search_sessions GROUP BY explainer")
            }
            feedback = {
                row["v"]: {"up": row["up"], "total": row["total"]}
                for row in conn.execute(
                    "SELECT s.explainer AS v, SUM(CASE WHEN f.rating='up' THEN 1 ELSE 0 END) AS up, COUNT(*) AS total "
                    "FROM feedback f JOIN search_sessions s ON s.id = f.request_id GROUP BY s.explainer"
                )
            }
            maps = {
                row["v"]: row["n"]
                for row in conn.execute(
                    "SELECT s.explainer AS v, COUNT(*) AS n FROM events e JOIN search_sessions s ON s.id = e.request_id "
                    "WHERE e.event = 'maps_opened' GROUP BY s.explainer"
                )
            }
        return {"sessions": sessions, "feedback": feedback, "maps": maps}
