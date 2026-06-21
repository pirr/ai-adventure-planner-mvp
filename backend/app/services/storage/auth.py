from __future__ import annotations

from datetime import datetime
from typing import Any

from app.services.storage.accounts import AccountsRepo
from app.services.storage.db import Database


class AuthRepo:
    """Auth token tables: `auth_sessions`, `auth_oauth_states`,
    `password_reset_tokens`. Reads `accounts` (via AccountsRepo) only to enrich
    a looked-up session with its sign-in provider."""

    def __init__(self, db: Database, accounts: AccountsRepo):
        self.db = db
        self.accounts = accounts

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
        with self.db.connect() as conn:
            conn.execute(
                "INSERT INTO auth_sessions (token_hash, account_id, csrf_token, created_at, expires_at, user_agent) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (token_hash, account_id, csrf_token, now, expires_at, user_agent),
            )
            conn.execute("DELETE FROM auth_sessions WHERE expires_at <= ?", (now,))

    def session_for_token_hash(self, token_hash: str) -> dict[str, Any] | None:
        now = datetime.utcnow().isoformat()
        with self.db.connect() as conn:
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
        item["provider"] = self.accounts.account_provider(int(item["account_id"]))
        return item

    def revoke_session(self, token_hash: str) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE auth_sessions SET revoked_at = ? WHERE token_hash = ?",
                (datetime.utcnow().isoformat(), token_hash),
            )

    def create_oauth_state(self, state_hash: str, anonymous_id: str | None, expires_at: str) -> None:
        now = datetime.utcnow().isoformat()
        with self.db.connect() as conn:
            conn.execute(
                "INSERT INTO auth_oauth_states (state_hash, anonymous_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                (state_hash, anonymous_id, now, expires_at),
            )
            conn.execute("DELETE FROM auth_oauth_states WHERE expires_at <= ? OR consumed_at IS NOT NULL", (now,))

    def consume_oauth_state(self, state_hash: str) -> dict[str, Any] | None:
        now = datetime.utcnow().isoformat()
        with self.db.connect() as conn:
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
        with self.db.connect() as conn:
            conn.execute(
                "INSERT INTO password_reset_tokens (token_hash, account_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                (token_hash, account_id, now, expires_at),
            )
