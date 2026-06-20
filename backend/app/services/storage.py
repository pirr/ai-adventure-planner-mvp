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

                CREATE TABLE IF NOT EXISTS accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT NOT NULL,
                    normalized_email TEXT UNIQUE NOT NULL,
                    password_hash TEXT,
                    email_verified INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    locale TEXT
                );

                CREATE TABLE IF NOT EXISTS account_identities (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id INTEGER NOT NULL,
                    provider TEXT NOT NULL,
                    provider_subject TEXT NOT NULL,
                    email TEXT,
                    email_verified INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(provider, provider_subject)
                );

                CREATE TABLE IF NOT EXISTS auth_sessions (
                    token_hash TEXT PRIMARY KEY,
                    account_id INTEGER NOT NULL,
                    csrf_token TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    revoked_at TEXT,
                    user_agent TEXT
                );

                CREATE TABLE IF NOT EXISTS auth_oauth_states (
                    state_hash TEXT PRIMARY KEY,
                    anonymous_id TEXT,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    consumed_at TEXT
                );

                CREATE TABLE IF NOT EXISTS password_reset_tokens (
                    token_hash TEXT PRIMARY KEY,
                    account_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    used_at TEXT
                );

                CREATE TABLE IF NOT EXISTS search_sessions (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    anonymous_id TEXT,
                    account_id INTEGER,
                    lat REAL NOT NULL,
                    lon REAL NOT NULL,
                    request_json TEXT NOT NULL,
                    response_json TEXT NOT NULL,
                    explainer TEXT
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
                    anonymous_id TEXT,
                    account_id INTEGER
                );

                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    event TEXT NOT NULL,
                    request_id TEXT,
                    recommendation_id TEXT,
                    anonymous_id TEXT,
                    account_id INTEGER,
                    meta TEXT
                );

                -- 0.2.2: per-user place state. `seen` is set automatically for every
                -- recommended place (so "Show others" can rotate to fresh ones); `visited`
                -- is set explicitly by the user (so the place is never suggested again).
                CREATE TABLE IF NOT EXISTS place_marks (
                    anonymous_id TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    seen INTEGER NOT NULL DEFAULT 0,
                    visited INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (anonymous_id, source_id)
                );

                CREATE TABLE IF NOT EXISTS account_place_marks (
                    account_id INTEGER NOT NULL,
                    source_id TEXT NOT NULL,
                    seen INTEGER NOT NULL DEFAULT 0,
                    visited INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (account_id, source_id)
                );

                -- 0.3: daily budget counters for paid external APIs (Google Places).
                -- scope "global" (key "") caps total spend; scope "user" (key =
                -- anonymous_id) keeps one user from draining the shared budget.
                CREATE TABLE IF NOT EXISTS api_usage (
                    day TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    key TEXT NOT NULL,
                    count INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (day, scope, key)
                );
                """
            )
            # Migrate pre-0.2 databases that predate the anonymous_id columns.
            for table in ("search_sessions", "feedback", "events"):
                self._ensure_column(conn, table, "anonymous_id", "TEXT")
                self._ensure_column(conn, table, "account_id", "INTEGER")
            # 0.2.1: which explainer (llm/template) produced each session, for A/B.
            self._ensure_column(conn, "search_sessions", "explainer", "TEXT")
            conn.executescript(
                """
                CREATE INDEX IF NOT EXISTS idx_search_sessions_account ON search_sessions(account_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_feedback_account ON feedback(account_id);
                CREATE INDEX IF NOT EXISTS idx_events_account ON events(account_id);
                CREATE INDEX IF NOT EXISTS idx_auth_sessions_account ON auth_sessions(account_id);
                """
            )

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

    @staticmethod
    def normalize_email(email: str) -> str:
        return email.strip().lower()

    @staticmethod
    def _account_from_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        item = dict(row)
        item["email_verified"] = bool(item.get("email_verified"))
        return item

    def create_email_account(self, email: str, password_hash: str, locale: str | None = None) -> dict[str, Any]:
        normalized = self.normalize_email(email)
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO accounts (email, normalized_email, password_hash, email_verified, created_at, updated_at, locale) "
                "VALUES (?, ?, ?, 0, ?, ?, ?)",
                (email.strip(), normalized, password_hash, now, now, locale),
            )
            row = conn.execute("SELECT * FROM accounts WHERE id = ?", (cursor.lastrowid,)).fetchone()
        account = self._account_from_row(row)
        if account is None:
            raise RuntimeError("account_create_failed")
        return account

    def get_account_by_email(self, email: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM accounts WHERE normalized_email = ?",
                (self.normalize_email(email),),
            ).fetchone()
        return self._account_from_row(row)

    def get_account_by_id(self, account_id: int | None) -> dict[str, Any] | None:
        if account_id is None:
            return None
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
        return self._account_from_row(row)

    def account_provider(self, account_id: int) -> str:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT provider FROM account_identities WHERE account_id = ? ORDER BY id LIMIT 1",
                (account_id,),
            ).fetchone()
        return row["provider"] if row else "email"

    def get_account_by_identity(self, provider: str, provider_subject: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT a.*
                FROM accounts a
                JOIN account_identities i ON i.account_id = a.id
                WHERE i.provider = ? AND i.provider_subject = ?
                """,
                (provider, provider_subject),
            ).fetchone()
        return self._account_from_row(row)

    def create_or_link_google_account(
        self,
        *,
        provider_subject: str,
        email: str,
        email_verified: bool,
        locale: str | None = None,
    ) -> dict[str, Any]:
        now = datetime.utcnow().isoformat()
        normalized = self.normalize_email(email)
        with self._connect() as conn:
            existing = conn.execute(
                """
                SELECT a.*
                FROM accounts a
                JOIN account_identities i ON i.account_id = a.id
                WHERE i.provider = 'google' AND i.provider_subject = ?
                """,
                (provider_subject,),
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE account_identities SET email=?, email_verified=?, updated_at=? "
                    "WHERE provider='google' AND provider_subject=?",
                    (email, int(email_verified), now, provider_subject),
                )
                return self._account_from_row(existing) or {}

            account = conn.execute(
                "SELECT * FROM accounts WHERE normalized_email = ?",
                (normalized,),
            ).fetchone()
            if account is None:
                cursor = conn.execute(
                    "INSERT INTO accounts (email, normalized_email, password_hash, email_verified, created_at, updated_at, locale) "
                    "VALUES (?, ?, NULL, ?, ?, ?, ?)",
                    (email.strip(), normalized, int(email_verified), now, now, locale),
                )
                account_id = cursor.lastrowid
            else:
                account_id = account["id"]
                conn.execute(
                    "UPDATE accounts SET email_verified = CASE WHEN ? THEN 1 ELSE email_verified END, updated_at=? WHERE id=?",
                    (int(email_verified), now, account_id),
                )
            conn.execute(
                "INSERT INTO account_identities (account_id, provider, provider_subject, email, email_verified, created_at, updated_at) "
                "VALUES (?, 'google', ?, ?, ?, ?, ?)",
                (account_id, provider_subject, email, int(email_verified), now, now),
            )
            row = conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
        account = self._account_from_row(row)
        if account is None:
            raise RuntimeError("account_create_failed")
        return account

    def create_session(
        self,
        *,
        token_hash: str,
        account_id: int,
        csrf_token: str,
        expires_at: str,
        user_agent: str | None = None,
    ) -> None:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO auth_sessions (token_hash, account_id, csrf_token, created_at, expires_at, user_agent) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (token_hash, account_id, csrf_token, now, expires_at, user_agent),
            )
            conn.execute("DELETE FROM auth_sessions WHERE expires_at <= ?", (now,))

    def session_for_token_hash(self, token_hash: str) -> dict[str, Any] | None:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT s.token_hash, s.account_id, s.csrf_token, s.expires_at,
                       a.email, a.email_verified
                FROM auth_sessions s
                JOIN accounts a ON a.id = s.account_id
                WHERE s.token_hash = ? AND s.revoked_at IS NULL AND s.expires_at > ?
                """,
                (token_hash, now),
            ).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["email_verified"] = bool(item.get("email_verified"))
        item["provider"] = self.account_provider(int(item["account_id"]))
        return item

    def revoke_session(self, token_hash: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE auth_sessions SET revoked_at = ? WHERE token_hash = ?",
                (datetime.utcnow().isoformat(), token_hash),
            )

    def create_oauth_state(self, state_hash: str, anonymous_id: str | None, expires_at: str) -> None:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO auth_oauth_states (state_hash, anonymous_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                (state_hash, anonymous_id, now, expires_at),
            )
            conn.execute("DELETE FROM auth_oauth_states WHERE expires_at <= ? OR consumed_at IS NOT NULL", (now,))

    def consume_oauth_state(self, state_hash: str) -> dict[str, Any] | None:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM auth_oauth_states WHERE state_hash = ? AND consumed_at IS NULL AND expires_at > ?",
                (state_hash, now),
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                "UPDATE auth_oauth_states SET consumed_at = ? WHERE state_hash = ?",
                (now, state_hash),
            )
        return dict(row)

    def create_password_reset_token(self, token_hash: str, account_id: int, expires_at: str) -> None:
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO password_reset_tokens (token_hash, account_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                (token_hash, account_id, now, expires_at),
            )

    def save_response(
        self,
        request_id: str,
        request: AdventureRequest,
        response: AdventureResponse,
        account_id: int | None = None,
    ) -> None:
        response_json = response.json()
        # Outcome-based A/B label: did the user actually see LLM explanations?
        explainer = "llm" if any(rec.summary for rec in response.recommendations) else "template"
        with self._connect() as conn:
            self._touch_user(conn, request.anonymous_id, request.lang)
            conn.execute(
                "INSERT OR REPLACE INTO search_sessions (id, created_at, anonymous_id, account_id, lat, lon, request_json, response_json, explainer) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (request_id, datetime.utcnow().isoformat(), request.anonymous_id, account_id, request.lat, request.lon, request.json(), response_json, explainer),
            )
            for rec in response.recommendations:
                conn.execute(
                    "INSERT OR REPLACE INTO recommendations (id, request_id, title, score, payload_json) VALUES (?, ?, ?, ?, ?)",
                    (rec.id, request_id, rec.title, rec.adventure_score, rec.json()),
                )
            # Remember which places this user has now been shown, so a later
            # "Show others" search can rotate past them.
            if account_id is not None:
                self._record_account_seen(conn, account_id, [rec.source_id for rec in response.recommendations])
            else:
                self._record_seen(conn, request.anonymous_id, [rec.source_id for rec in response.recommendations])

    def save_feedback(self, feedback: FeedbackRequest, account_id: int | None = None) -> None:
        with self._connect() as conn:
            self._touch_user(conn, feedback.anonymous_id)
            conn.execute(
                "INSERT INTO feedback (created_at, request_id, recommendation_id, rating, reason, anonymous_id, account_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (datetime.utcnow().isoformat(), feedback.request_id, feedback.recommendation_id, feedback.rating, feedback.reason, feedback.anonymous_id, account_id),
            )

    def feedback_summary(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM feedback ORDER BY created_at DESC LIMIT 100").fetchall()
        return [dict(row) for row in rows]

    def save_event(self, event: AnalyticsEvent, account_id: int | None = None) -> None:
        with self._connect() as conn:
            self._touch_user(conn, event.anonymous_id)
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

    def events_summary(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT event, COUNT(*) AS count FROM events GROUP BY event ORDER BY count DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def ab_summary(self) -> list[dict[str, Any]]:
        """Per explainer variant (llm/template): sessions, thumbs-up rate and
        maps-open rate, joining feedback/events to sessions by request_id."""
        with self._connect() as conn:
            sessions = {row["explainer"]: row["n"] for row in conn.execute(
                "SELECT explainer, COUNT(*) AS n FROM search_sessions GROUP BY explainer"
            )}
            feedback = {row["v"]: row for row in conn.execute(
                "SELECT s.explainer AS v, SUM(CASE WHEN f.rating='up' THEN 1 ELSE 0 END) AS up, COUNT(*) AS total "
                "FROM feedback f JOIN search_sessions s ON s.id = f.request_id GROUP BY s.explainer"
            )}
            maps = {row["v"]: row["n"] for row in conn.execute(
                "SELECT s.explainer AS v, COUNT(*) AS n FROM events e JOIN search_sessions s ON s.id = e.request_id "
                "WHERE e.event = 'maps_opened' GROUP BY s.explainer"
            )}
        result = []
        for variant in sorted(sessions, key=lambda v: v or ""):
            n = sessions[variant]
            fb = feedback.get(variant)
            up, total = (fb["up"] or 0, fb["total"]) if fb else (0, 0)
            result.append({
                "variant": variant,
                "sessions": n,
                "feedback": total,
                "thumbs_up_rate": round(up / total, 3) if total else None,
                "maps_opened": maps.get(variant, 0),
                "maps_open_rate": round(maps.get(variant, 0) / n, 3) if n else None,
            })
        return result

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

    def history_for_account(self, account_id: int | None, limit: int = 20) -> list[dict[str, Any]]:
        if account_id is None:
            return []
        with self._connect() as conn:
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

    def preference_profile_for_account(self, account_id: int | None) -> dict[str, Any]:
        if account_id is None:
            return {}
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT f.rating AS rating, r.payload_json AS payload_json
                FROM feedback f
                JOIN recommendations r ON r.request_id = f.request_id AND r.id = f.recommendation_id
                WHERE f.account_id = ?
                """,
                (account_id,),
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

    @staticmethod
    def _record_seen(conn: sqlite3.Connection, anonymous_id: str | None, source_ids: list[str | None]) -> None:
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
    def _record_account_seen(conn: sqlite3.Connection, account_id: int, source_ids: list[str | None]) -> None:
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
        with self._connect() as conn:
            self._touch_user(conn, anonymous_id)
            conn.execute(
                "INSERT INTO place_marks (anonymous_id, source_id, visited, updated_at) VALUES (?, ?, 1, ?) "
                "ON CONFLICT(anonymous_id, source_id) DO UPDATE SET visited=1, updated_at=excluded.updated_at",
                (anonymous_id, source_id, datetime.utcnow().isoformat()),
            )

    def clear_visited(self, anonymous_id: str | None) -> int:
        """Un-mark every place this user marked visited. Returns rows affected."""
        if not anonymous_id:
            return 0
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE place_marks SET visited=0, updated_at=? WHERE anonymous_id=? AND visited=1",
                (datetime.utcnow().isoformat(), anonymous_id),
            )
            return cursor.rowcount

    def mark_visited_account(self, account_id: int | None, source_id: str | None) -> None:
        if account_id is None or not source_id:
            return
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO account_place_marks (account_id, source_id, visited, updated_at) VALUES (?, ?, 1, ?) "
                "ON CONFLICT(account_id, source_id) DO UPDATE SET visited=1, updated_at=excluded.updated_at",
                (account_id, source_id, datetime.utcnow().isoformat()),
            )

    def clear_visited_account(self, account_id: int | None) -> int:
        if account_id is None:
            return 0
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE account_place_marks SET visited=0, updated_at=? WHERE account_id=? AND visited=1",
                (datetime.utcnow().isoformat(), account_id),
            )
            return cursor.rowcount

    def place_marks(self, anonymous_id: str | None) -> dict[str, set[str]]:
        """This user's seen and visited place source_ids (empty without an id)."""
        if not anonymous_id:
            return {"seen": set(), "visited": set()}
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT source_id, seen, visited FROM place_marks WHERE anonymous_id = ?",
                (anonymous_id,),
            ).fetchall()
        seen = {row["source_id"] for row in rows if row["seen"]}
        visited = {row["source_id"] for row in rows if row["visited"]}
        return {"seen": seen, "visited": visited}

    def account_place_marks(self, account_id: int | None) -> dict[str, set[str]]:
        if account_id is None:
            return {"seen": set(), "visited": set()}
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT source_id, seen, visited FROM account_place_marks WHERE account_id = ?",
                (account_id,),
            ).fetchall()
        seen = {row["source_id"] for row in rows if row["seen"]}
        visited = {row["source_id"] for row in rows if row["visited"]}
        return {"seen": seen, "visited": visited}

    @staticmethod
    def _usage_day() -> str:
        """Today's UTC budget bucket; patchable in tests to simulate day rollover."""
        return datetime.utcnow().strftime("%Y-%m-%d")

    def reserve_api_calls(
        self,
        api: str,
        anonymous_id: str | None,
        requested: int,
        *,
        daily_limit: int,
        user_daily_limit: int,
    ) -> int:
        """Grant up to `requested` calls of `api` within today's budgets.

        Returns min(requested, global remaining, user remaining) and records
        the grant against both counters in one transaction, so concurrent
        requests can't overdraw. Budgets are independent per `api` (scope is
        prefixed). Requests without an anonymous_id get nothing: they can't be
        rate-limited individually, so they don't get to spend the budget.
        Failed calls still count (never under-counts spend).
        """
        if requested <= 0 or not anonymous_id:
            return 0
        day = self._usage_day()
        with self._connect() as conn:
            counters = ((f"{api}:global", ""), (f"{api}:user", anonymous_id))
            used: dict[str, int] = {}
            for scope, key in counters:
                row = conn.execute(
                    "SELECT count FROM api_usage WHERE day=? AND scope=? AND key=?",
                    (day, scope, key),
                ).fetchone()
                used[scope] = row["count"] if row else 0
            granted = min(
                requested,
                max(0, daily_limit - used[f"{api}:global"]),
                max(0, user_daily_limit - used[f"{api}:user"]),
            )
            if granted > 0:
                for scope, key in counters:
                    conn.execute(
                        "INSERT INTO api_usage (day, scope, key, count) VALUES (?, ?, ?, ?) "
                        "ON CONFLICT(day, scope, key) DO UPDATE SET count = count + excluded.count",
                        (day, scope, key, granted),
                    )
            # Counters are only read for "today", so old rows are dead weight.
            conn.execute("DELETE FROM api_usage WHERE day < date(?, '-7 days')", (day,))
        return granted

    def merge_anonymous_into_account(self, anonymous_id: str | None, account_id: int | None) -> None:
        if not anonymous_id or account_id is None:
            return
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
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
        with self._connect() as conn:
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
            conn.execute("DELETE FROM place_marks WHERE anonymous_id = ?", (anonymous_id,))
            conn.execute("DELETE FROM users WHERE anonymous_id = ?", (anonymous_id,))
        return len(session_ids)


storage = Storage()
