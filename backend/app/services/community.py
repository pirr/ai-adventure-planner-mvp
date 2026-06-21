from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from app.config import settings
from app.services.storage import storage as _storage


def community_signals(source_ids: list[str | None], store=_storage) -> dict[str, dict[str, int]]:
    """Cross-user 'wisdom of our adventurers' for the given places, keyed by
    canonical ``source_id``. Returns
    ``{source_id: {ups, downs, raters, recent_visits, want_to_visit}}`` for places
    that have any signal; places with none are omitted (cold start).

    Counts are by *distinct user* (account or anonymous), so one person can't
    inflate a place. ``ups``/``downs``/``raters`` come from thumbs feedback;
    ``recent_visits`` from "mark visited" within ``community_visit_window_days``;
    ``want_to_visit`` from logged-in "want to visit" marks (all-time, intent signal).
    """
    ids = [s for s in dict.fromkeys(source_ids) if s]  # dedupe, drop None, keep order
    if not ids:
        return {}
    cutoff = (datetime.utcnow() - timedelta(days=settings.community_visit_window_days)).isoformat()
    raw = store.community.raw_signals(ids, cutoff)

    up_raters: dict[str, set] = defaultdict(set)
    down_raters: dict[str, set] = defaultdict(set)
    visitors: dict[str, set] = defaultdict(set)
    wanters: dict[str, set] = defaultdict(set)
    # Distinct-user rule ("one person can't inflate a place"): collapse each
    # signal kind into a set of raters per place before counting.
    for row in raw.ratings:
        sid, rater = row["source_id"], row["rater"]
        if not sid or not rater:
            continue
        if row["rating"] == "up":
            up_raters[sid].add(rater)
        elif row["rating"] == "down":
            down_raters[sid].add(rater)
    for row in raw.visits:
        sid, rater = row["source_id"], row["rater"]
        if sid and rater:
            visitors[sid].add(rater)
    for row in raw.wants:
        sid, rater = row["source_id"], row["rater"]
        if sid and rater:
            wanters[sid].add(rater)

    signals: dict[str, dict[str, int]] = {}
    for sid in ids:
        ups, downs = len(up_raters.get(sid, ())), len(down_raters.get(sid, ()))
        raters = len(up_raters.get(sid, set()) | down_raters.get(sid, set()))
        recent_visits = len(visitors.get(sid, ()))
        want_to_visit = len(wanters.get(sid, ()))
        if raters or recent_visits or want_to_visit:  # omit cold-start places
            signals[sid] = {
                "ups": ups,
                "downs": downs,
                "raters": raters,
                "recent_visits": recent_visits,
                "want_to_visit": want_to_visit,
            }
    return signals
