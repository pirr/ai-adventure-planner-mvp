from __future__ import annotations

from datetime import datetime
from typing import Any

from app.services.storage.db import Connection, Database


class PlaceMarksRepo:
    """Per-user place state across `place_marks` (anonymous) and
    `account_place_marks` (logged-in). `record_seen*` take a connection so they
    can be folded into the same transaction that saves a search response."""

    def __init__(self, db: Database):
        self.db = db

    @staticmethod
    def record_seen(conn: Connection, anonymous_id: str | None, source_ids: list[str | None]) -> None:
        """Mark each given place as seen for this user (no-op without an id)."""
        if not anonymous_id:
            return
        now = datetime.utcnow().isoformat()
        for source_id in source_ids:
            if not source_id:
                continue
            conn.execute(
                "INSERT INTO place_marks (anonymous_id, source_id, seen, updated_at) VALUES (?, ?, 1, ?) "
                "ON CONFLICT(anonymous_id, source_id) DO UPDATE SET seen=1, updated_at=excluded.updated_at",
                (anonymous_id, source_id, now),
            )

    @staticmethod
    def record_account_seen(conn: Connection, account_id: int, source_ids: list[str | None]) -> None:
        now = datetime.utcnow().isoformat()
        for source_id in source_ids:
            if not source_id:
                continue
            conn.execute(
                "INSERT INTO account_place_marks (account_id, source_id, seen, updated_at) VALUES (?, ?, 1, ?) "
                "ON CONFLICT(account_id, source_id) DO UPDATE SET seen=1, updated_at=excluded.updated_at",
                (account_id, source_id, now),
            )

    def mark_visited(self, anonymous_id: str | None, source_id: str | None) -> None:
        if not anonymous_id or not source_id:
            return
        from app.services.storage.users import UsersRepo

        with self.db.connect() as conn:
            UsersRepo.touch(conn, anonymous_id)
            conn.execute(
                "INSERT INTO place_marks (anonymous_id, source_id, visited, updated_at) VALUES (?, ?, 1, ?) "
                "ON CONFLICT(anonymous_id, source_id) DO UPDATE SET visited=1, updated_at=excluded.updated_at",
                (anonymous_id, source_id, datetime.utcnow().isoformat()),
            )

    def clear_visited(self, anonymous_id: str | None) -> int:
        """Un-mark every place this user marked visited. Returns rows affected."""
        if not anonymous_id:
            return 0
        with self.db.connect() as conn:
            cursor = conn.execute(
                "UPDATE place_marks SET visited=0, updated_at=? WHERE anonymous_id=? AND visited=1",
                (datetime.utcnow().isoformat(), anonymous_id),
            )
            return cursor.rowcount

    def mark_visited_account(self, account_id: int | None, source_id: str | None) -> None:
        if account_id is None or not source_id:
            return
        with self.db.connect() as conn:
            conn.execute(
                "INSERT INTO account_place_marks (account_id, source_id, visited, updated_at) VALUES (?, ?, 1, ?) "
                "ON CONFLICT(account_id, source_id) DO UPDATE SET visited=1, updated_at=excluded.updated_at",
                (account_id, source_id, datetime.utcnow().isoformat()),
            )

    def clear_visited_account(self, account_id: int | None) -> int:
        if account_id is None:
            return 0
        with self.db.connect() as conn:
            cursor = conn.execute(
                "UPDATE account_place_marks SET visited=0, updated_at=? WHERE account_id=? AND visited=1",
                (datetime.utcnow().isoformat(), account_id),
            )
            return cursor.rowcount

    def set_want_to_visit_account(self, account_id: int | None, source_id: str | None, wanted: bool) -> None:
        """Toggle this account's "want to visit" intent for one place (login-only)."""
        if account_id is None or not source_id:
            return
        with self.db.connect() as conn:
            conn.execute(
                "INSERT INTO account_place_marks (account_id, source_id, want_to_visit, updated_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(account_id, source_id) DO UPDATE SET want_to_visit=excluded.want_to_visit, updated_at=excluded.updated_at",
                (account_id, source_id, 1 if wanted else 0, datetime.utcnow().isoformat()),
            )

    def clear_want_to_visit_account(self, account_id: int | None) -> int:
        """Un-mark every "want to visit" place for this account. Returns rows affected."""
        if account_id is None:
            return 0
        with self.db.connect() as conn:
            cursor = conn.execute(
                "UPDATE account_place_marks SET want_to_visit=0, updated_at=? WHERE account_id=? AND want_to_visit=1",
                (datetime.utcnow().isoformat(), account_id),
            )
            return cursor.rowcount

    def wanted_places_account(self, account_id: int | None, limit: int = 20) -> list[dict[str, Any]]:
        """This account's "want to visit" places, newest first, with display info
        (title, score, map_url) recovered from a recommendation row for each place.
        Falls back to source_id as the title when no recommendation row remains."""
        if account_id is None:
            return []
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT m.source_id AS source_id,
                       m.updated_at AS saved_at,
                       r.title AS title,
                       r.score AS score,
                       json_extract(r.payload_json, '$.map_url') AS map_url
                FROM account_place_marks m
                LEFT JOIN recommendations r
                  ON json_extract(r.payload_json, '$.source_id') = m.source_id
                WHERE m.account_id = ? AND m.want_to_visit = 1
                GROUP BY m.source_id
                ORDER BY m.updated_at DESC
                LIMIT ?
                """,
                (account_id, limit),
            ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["title"] = item["title"] or item["source_id"]
            items.append(item)
        return items

    def place_marks(self, anonymous_id: str | None) -> dict[str, set[str]]:
        """This user's seen and visited place source_ids (empty without an id).

        ``want_to_visit`` is always empty here: it is a login-only intent stored on
        accounts, but the key is present so the marks dict shape matches accounts."""
        if not anonymous_id:
            return {"seen": set(), "visited": set(), "want_to_visit": set()}
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT source_id, seen, visited FROM place_marks WHERE anonymous_id = ?",
                (anonymous_id,),
            ).fetchall()
        seen = {row["source_id"] for row in rows if row["seen"]}
        visited = {row["source_id"] for row in rows if row["visited"]}
        return {"seen": seen, "visited": visited, "want_to_visit": set()}

    def account_place_marks(self, account_id: int | None) -> dict[str, set[str]]:
        if account_id is None:
            return {"seen": set(), "visited": set(), "want_to_visit": set()}
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT source_id, seen, visited, want_to_visit FROM account_place_marks WHERE account_id = ?",
                (account_id,),
            ).fetchall()
        seen = {row["source_id"] for row in rows if row["seen"]}
        visited = {row["source_id"] for row in rows if row["visited"]}
        want_to_visit = {row["source_id"] for row in rows if row["want_to_visit"]}
        return {"seen": seen, "visited": visited, "want_to_visit": want_to_visit}
