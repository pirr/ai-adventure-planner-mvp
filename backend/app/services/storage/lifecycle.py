from __future__ import annotations

from datetime import datetime

from app.services.storage.db import Database


class LifecycleRepo:
    """Cross-table account-data operations that must run as one transaction:
    merging an anonymous user into an account on sign-in, and the two
    right-to-erasure deletes. Pure data movement — no domain policy."""

    def __init__(self, db: Database):
        self.db = db

    def merge_anonymous_into_account(self, anonymous_id: str | None, account_id: int | None) -> None:
        if not anonymous_id or account_id is None:
            return
        now = datetime.utcnow().isoformat()
        with self.db.connect() as conn:
            for table in ("search_sessions", "feedback", "events"):
                conn.execute(
                    f"UPDATE {table} SET account_id = ? WHERE anonymous_id = ? AND account_id IS NULL",
                    (account_id, anonymous_id),
                )
            rows = conn.execute(
                "SELECT source_id, seen, visited, updated_at FROM place_marks WHERE anonymous_id = ?",
                (anonymous_id,),
            ).fetchall()
            for row in rows:
                conn.execute(
                    """
                    INSERT INTO account_place_marks (account_id, source_id, seen, visited, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(account_id, source_id) DO UPDATE SET
                        seen = MAX(account_place_marks.seen, excluded.seen),
                        visited = MAX(account_place_marks.visited, excluded.visited),
                        updated_at = excluded.updated_at
                    """,
                    (
                        account_id,
                        row["source_id"],
                        row["seen"],
                        row["visited"],
                        row["updated_at"] or now,
                    ),
                )

    def delete_account_history(self, account_id: int | None) -> int:
        if account_id is None:
            return 0
        with self.db.connect() as conn:
            session_ids = [row["id"] for row in conn.execute(
                "SELECT id FROM search_sessions WHERE account_id = ?", (account_id,)
            )]
            if session_ids:
                placeholders = ",".join("?" * len(session_ids))
                conn.execute(f"DELETE FROM recommendations WHERE request_id IN ({placeholders})", session_ids)
            conn.execute("DELETE FROM search_sessions WHERE account_id = ?", (account_id,))
            conn.execute("DELETE FROM feedback WHERE account_id = ?", (account_id,))
            conn.execute("DELETE FROM events WHERE account_id = ?", (account_id,))
            conn.execute(
                "UPDATE account_place_marks SET seen=0, updated_at=? WHERE account_id=? AND seen=1",
                (datetime.utcnow().isoformat(), account_id),
            )
            conn.execute(
                "DELETE FROM account_place_marks WHERE account_id=? AND seen=0 AND visited=0",
                (account_id,),
            )
        return len(session_ids)

    def delete_user_data(self, anonymous_id: str | None) -> int:
        if not anonymous_id:
            return 0
        with self.db.connect() as conn:
            session_ids = [row["id"] for row in conn.execute(
                "SELECT id FROM search_sessions WHERE anonymous_id = ?", (anonymous_id,)
            )]
            if session_ids:
                placeholders = ",".join("?" * len(session_ids))
                conn.execute(f"DELETE FROM recommendations WHERE request_id IN ({placeholders})", session_ids)
            conn.execute("DELETE FROM search_sessions WHERE anonymous_id = ?", (anonymous_id,))
            conn.execute("DELETE FROM feedback WHERE anonymous_id = ?", (anonymous_id,))
            conn.execute("DELETE FROM events WHERE anonymous_id = ?", (anonymous_id,))
            conn.execute("DELETE FROM place_marks WHERE anonymous_id = ?", (anonymous_id,))
            conn.execute("DELETE FROM users WHERE anonymous_id = ?", (anonymous_id,))
        return len(session_ids)
