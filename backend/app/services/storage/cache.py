from __future__ import annotations

import time
from dataclasses import dataclass

from app.services.storage.db import Database


@dataclass(frozen=True)
class CacheEntry:
    expires_at: float
    payload_json: str | None


class CacheRepo:
    """Persistent TTL caches for external place data.

    These sit behind the service-level in-memory caches so Fly scale-to-zero or
    deploys do not immediately erase warm OSM/Google data.
    """

    def __init__(self, db: Database):
        self.db = db

    def get_place_search(self, cache_key: str, now: float | None = None) -> CacheEntry | None:
        return self._get("place_search_cache", "cache_key", cache_key, now)

    def set_place_search(self, cache_key: str, expires_at: float, payload_json: str) -> None:
        self._set("place_search_cache", "cache_key", cache_key, expires_at, payload_json)

    def get_google_place(self, source_id: str, now: float | None = None) -> CacheEntry | None:
        return self._get("google_place_cache", "source_id", source_id, now)

    def set_google_place(self, source_id: str, expires_at: float, payload_json: str | None) -> None:
        self._set("google_place_cache", "source_id", source_id, expires_at, payload_json)

    def _get(self, table: str, key_column: str, key: str, now: float | None) -> CacheEntry | None:
        current = time.time() if now is None else now
        with self.db.connect() as conn:
            row = conn.execute(
                f"SELECT expires_at, payload_json FROM {table} WHERE {key_column}=?",
                (key,),
            ).fetchone()
            if row is None:
                return None
            expires_at = float(row["expires_at"])
            if expires_at <= current:
                conn.execute(f"DELETE FROM {table} WHERE {key_column}=?", (key,))
                return None
            return CacheEntry(expires_at=expires_at, payload_json=row["payload_json"])

    def _set(self, table: str, key_column: str, key: str, expires_at: float, payload_json: str | None) -> None:
        with self.db.connect() as conn:
            conn.execute(
                f"INSERT INTO {table} ({key_column}, expires_at, payload_json) VALUES (?, ?, ?) "
                f"ON CONFLICT({key_column}) DO UPDATE SET "
                "expires_at = excluded.expires_at, payload_json = excluded.payload_json",
                (key, expires_at, payload_json),
            )
