from __future__ import annotations

from datetime import datetime

from app.services.storage.db import Database


class ApiUsageRepo:
    """Daily budget counters (`api_usage`) for paid external APIs.

    NOTE: `reserve_api_calls` intentionally keeps its `min(...)` *inside* the
    read-modify-write. That cap is not "business policy to lift out" — the policy
    (the daily limits) is already injected by callers from settings; the min is
    the reservation mechanic itself, and it must run in the same transaction as
    the counter read+increment or concurrent requests would overdraw the budget."""

    def __init__(self, db: Database):
        self.db = db

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
        with self.db.connect() as conn:
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
