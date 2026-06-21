from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from app.services.storage.db import Database


class AccountsRepo:
    """The `accounts` and `account_identities` tables: email/Google sign-in
    records and the email-normalization rule that keys them."""

    def __init__(self, db: Database):
        self.db = db

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
        with self.db.connect() as conn:
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
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM accounts WHERE normalized_email = ?",
                (self.normalize_email(email),),
            ).fetchone()
        return self._account_from_row(row)

    def get_account_by_id(self, account_id: int | None) -> dict[str, Any] | None:
        if account_id is None:
            return None
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
        return self._account_from_row(row)

    def account_provider(self, account_id: int) -> str:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT provider FROM account_identities WHERE account_id = ? ORDER BY id LIMIT 1",
                (account_id,),
            ).fetchone()
        return row["provider"] if row else "email"

    def get_account_by_identity(self, provider: str, provider_subject: str) -> dict[str, Any] | None:
        with self.db.connect() as conn:
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
        with self.db.connect() as conn:
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
