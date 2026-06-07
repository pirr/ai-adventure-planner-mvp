# MVP 0.2.2 — Result rotation & "visited" places

## Context

Repeated searches for the same user return the **same** top picks — `build_recommendations` runs
fresh every time with no memory of what was already shown, so re-searching feels like "no updates".
Separately, there is no way to tell the planner "I've already been there, stop suggesting it".

This milestone adds two per-user behaviours, both keyed off the existing **`anonymous_id`**
(localStorage → request → storage; no accounts/PII):

1. **Rotation on demand** — an explicit *"Show others"* action returns the next places that did not
   appear before, instead of repeating the top picks. A normal search still returns the best matches.
2. **Visited filtering** — a place the user marks *"I've been here"* is hard-excluded from all future
   searches, with a *"Clear visited"* reset.

The work is **backend-only** on this `v0.2.2` branch (the backend is identical across `v0.2.1` and
the `design/guided-explorer` UI experiment, so it merges into that branch cleanly). The UI is added
on `design/guided-explorer` after the merge.

---

## Workstream 1 — Per-user place state (`backend/app/services/storage.py`)

One new table, created via the existing `_init_db()` `CREATE TABLE IF NOT EXISTS` script so the
volume-persisted `data/adventures.db` self-migrates on startup:

```sql
CREATE TABLE IF NOT EXISTS place_marks (
  anonymous_id TEXT NOT NULL,
  source_id    TEXT NOT NULL,
  seen    INTEGER NOT NULL DEFAULT 0,
  visited INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (anonymous_id, source_id)
);
```

A place can be both seen and visited, so the two states are columns on one row (upserted via
`ON CONFLICT(anonymous_id, source_id) DO UPDATE`). New methods:

- `record_seen(anonymous_id, source_ids)` — upsert `seen=1`; called from `save_response` with the
  returned recommendations' `source_id`s. No-op when `anonymous_id` is None.
- `mark_visited(anonymous_id, source_id)` — upsert `visited=1`.
- `clear_visited(anonymous_id) -> int` — `UPDATE place_marks SET visited=0 WHERE anonymous_id=?`.
- `place_marks(anonymous_id) -> {"seen": set, "visited": set}` — read both sets; empty when no id.
- `delete_user_data` also deletes the user's `place_marks` rows.

## Workstream 2 — Filtering & rotation (`recommendations.py`, `schemas.py`, `scoring.py`)

- `AdventureRequest.exclude_seen: bool = False` — set by the "Show others" action.
- `Recommendation.source_id: str | None = None` — the canonical `osm:type:id`; `to_recommendation`
  fills it from `place.source_id` (the existing mangled `id` is kept for the frontend DOM).
- In `build_recommendations`, right after `get_candidate_places`: read `place_marks`, **hard-filter
  visited** out of the candidate list before routing, and compute an `order_key` used for both the
  first-pass sort and the final sort:

  ```python
  rotate = request.exclude_seen and bool(seen)
  def order_key(c):
      return c.score - (1000 if (rotate and c.place.source_id in seen) else 0)
  ```

  The penalty (1000 > max score 100) sinks already-seen candidates below all unseen ones, so the
  rescoring pool and final top-N become "unseen first", with seen places filling remaining slots by
  real score when unseen run out (graceful fallback → the action never returns empty). The displayed
  `adventure_score` is unchanged. When `exclude_seen=False` or `anonymous_id` is None, `order_key ==
  score` → the pipeline is byte-for-byte unchanged, so the offline eval (`eval/make_golden.py`, no
  `anonymous_id`) is unaffected.

## Workstream 3 — Endpoints (`backend/app/main.py`, `schemas.py`)

- `POST /api/visited` (body `VisitedRequest{anonymous_id, source_id}`) → `storage.mark_visited`.
- `DELETE /api/visited?anonymous_id=…` → `storage.clear_visited`, returns `{status, cleared}`.

## Workstream 4 — Tests (`backend/tests/`)

- `test_scoring.py`: `to_recommendation(...).source_id == place.source_id`.
- `test_place_marks.py` (pure storage, temp DB): `record_seen`/`mark_visited`/`place_marks`/
  `clear_visited` round-trips; `delete_user_data` clears marks.
- `test_rotation.py` (`use_live_data=False`, sample places, an `anonymous_id`, `TemplateProvider`):
  a marked-visited place never appears; with `exclude_seen=True` after a first search the previously
  returned places are replaced by new ones; results still return once everything is seen.

---

## Verification

- `docker compose run --rm --no-deps app python -m pytest -q` — all green, incl. the new tests.
- `docker compose run --rm --no-deps app python -m eval.run` — unchanged (no `anonymous_id`).
- After merge into `design/guided-explorer` + UI: search, mark "I've been here" (place gone for
  good), "Show others" (new places, then graceful repeats), "Clear visited" (place can return).
- All via docker compose (no local venv).
