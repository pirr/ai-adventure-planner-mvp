from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path

from app.config import settings


class Database:
    """Owns the SQLite connection, schema and migrations. Repos share one
    instance and ask it for connections; it knows nothing about the domain."""

    def __init__(self, path: Path = settings.sqlite_path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """One connection whose `with` block is a single transaction (commit on
        success, rollback on error). Use to make multi-table writes atomic."""
        conn = self.connect()
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self.connect() as conn:
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
                    want_to_visit INTEGER NOT NULL DEFAULT 0,
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
            # 0.7: "want to visit" intent mark (login-only) on account places.
            self._ensure_column(conn, "account_place_marks", "want_to_visit", "INTEGER NOT NULL DEFAULT 0")
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
