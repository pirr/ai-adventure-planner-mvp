from __future__ import annotations

from dataclasses import dataclass

from app.services.storage.db import Database, Row


@dataclass
class RawSignals:
    """Raw per-place signal rows for the requested source_ids. Each row carries
    a ``source_id`` and a ``rater`` (account id as text, or anonymous id);
    ``ratings`` rows also carry ``rating``. ``services/community.py`` dedupes
    raters and decides what counts."""

    ratings: list[Row]
    visits: list[Row]
    wants: list[Row]


class CommunityRepo:
    """Cross-user signal *reads* over feedback, place_marks and
    account_place_marks. Returns raw rows only — the visit-window cutoff is
    handed in (the service decides the window); no counting or thresholds here."""

    def __init__(self, db: Database):
        self.db = db

    def raw_signals(self, ids: list[str], visit_cutoff: str) -> RawSignals:
        """Raw rating / recent-visit / want-to-visit rows for ``ids``. ``ids``
        must be non-empty and pre-deduped; ``visit_cutoff`` bounds the visit
        rows ("want" is intent, counted all-time)."""
        placeholders = ",".join("?" * len(ids))
        with self.db.connect() as conn:
            ratings = conn.execute(
                f"""
                SELECT json_extract(r.payload_json, '$.source_id') AS source_id,
                       f.rating AS rating,
                       COALESCE(CAST(f.account_id AS TEXT), f.anonymous_id) AS rater
                FROM feedback f
                JOIN recommendations r
                  ON r.request_id = f.request_id AND r.id = f.recommendation_id
                WHERE json_extract(r.payload_json, '$.source_id') IN ({placeholders})
                """,
                ids,
            ).fetchall()
            visits = conn.execute(
                f"""
                SELECT source_id, anonymous_id AS rater FROM place_marks
                WHERE visited = 1 AND updated_at >= ? AND source_id IN ({placeholders})
                UNION
                SELECT source_id, CAST(account_id AS TEXT) AS rater FROM account_place_marks
                WHERE visited = 1 AND updated_at >= ? AND source_id IN ({placeholders})
                """,
                [visit_cutoff, *ids, visit_cutoff, *ids],
            ).fetchall()
            # "Want to visit" is intent, not a dated event: count it all-time (no window).
            wants = conn.execute(
                f"""
                SELECT source_id, CAST(account_id AS TEXT) AS rater FROM account_place_marks
                WHERE want_to_visit = 1 AND source_id IN ({placeholders})
                """,
                ids,
            ).fetchall()
        return RawSignals(ratings=ratings, visits=visits, wants=wants)
