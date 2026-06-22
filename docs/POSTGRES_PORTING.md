# PostgreSQL porting checklist

The storage layer is ready to swap backends: repos depend on the abstract
`Database` contract (`backend/app/services/storage/db.py`), the concrete
implementation is chosen by `create_database(settings)` from `STORAGE_BACKEND`,
and repos no longer reference `sqlite3` types directly (they use the `Connection`
Protocol and `Row` alias). The default and only working backend today is
`SqliteDatabase`. Selecting `STORAGE_BACKEND=postgres` raises `NotImplementedError`.

This file is the bounded to-do list for actually adding the Postgres backend.
**Re-audit with `rg` when you do the work** — the inventory below was taken by
reading the repos and can drift.

## 1. The adapter

- Add `psycopg` (v3) to `backend/requirements*.txt`.
- Implement `PostgresDatabase(Database)` in `db.py` (or a new `db_postgres.py`):
  `connect()` and `transaction()`, backed by a connection pool
  (`psycopg_pool.ConnectionPool`). Use `psycopg.rows.dict_row` so `row["col"]`
  keeps working (rows then satisfy the `Row` alias as real dicts).
- `transaction()` must give the same guarantee as the SQLite one — commit on
  success, roll back on error — but implemented with psycopg's own context
  manager (its `with conn:` semantics differ from sqlite3's; don't share a base
  implementation).
- Wire it into `create_database()`'s `"postgres"` branch using
  `settings.database_url`.

## 2. SQL dialect fixes in the repos

These run raw SQL written for SQLite. Each item below is a concrete site.

- **Placeholders** — every statement uses `?`; psycopg uses `%s`. This is
  pervasive (all repo files). Decide one approach: translate `?`→`%s` centrally,
  or standardise statements on `%s` with a thin SQLite shim. Pick before touching
  individual queries.
- **Auto-increment id retrieval** — `accounts.py` reads `cursor.lastrowid` after
  INSERT in `create_email_account` and `create_or_link_google_account`. Postgres
  has no `lastrowid`; use `INSERT ... RETURNING id` and read the returned row.
- **`INSERT OR REPLACE`** (SQLite-only) — `search.py` `insert_session` and
  `insert_recommendations`. Rewrite as `INSERT ... ON CONFLICT (<pk>) DO UPDATE
  SET ...` (search_sessions pk = `id`; recommendations pk = `(request_id, id)`).
- **Upserts already portable** — `place_marks.py`, `api_usage.py`, and
  `users.py` use `INSERT ... ON CONFLICT(<cols>) DO UPDATE SET x = excluded.x`,
  which Postgres also supports (`EXCLUDED`). No change expected; verify only.
- **SQLite JSON / date functions**:
  - `json_extract(payload_json, '$.x')` in `place_marks.wanted_places_account`
    and `community.raw_signals` → Postgres `payload_json::jsonb ->> 'x'`
    (store the column as `jsonb` for index-ability).
  - `date(?, '-7 days')` in `api_usage.reserve_api_calls` → `(?::date - interval
    '7 days')` (or compute the cutoff in Python and pass it in).

## 3. Schema / DDL (`_init_db`, `_ensure_column`)

`SqliteDatabase._init_db()` creates the schema with SQLite DDL via
`executescript()`. The Postgres backend needs its own schema. Per-item:

- `INTEGER PRIMARY KEY AUTOINCREMENT` → `GENERATED ALWAYS AS IDENTITY` (or
  `BIGSERIAL`).
- Integer-as-boolean columns (`email_verified`, `seen`, `visited`,
  `want_to_visit`, defaults `0`) → `boolean` (and drop the `int(...)`/`1 if ...`
  casts in the repos, or keep `smallint` to avoid touching them — decide once).
- `executescript()` is sqlite-only → run statements individually, or adopt a
  migration tool (Alembic or plain `.sql` files) at this point.
- `_ensure_column` uses `PRAGMA table_info(...)` → use
  `information_schema.columns`, or retire the hand-rolled migrations in favour of
  the migration tool.

## 4. Non-issues (verified portable, no change)

- **Timestamps** are Python-side `datetime.utcnow().isoformat()` stored as TEXT —
  portable. (Optionally migrate to `timestamptz` later; not required.)
- `CAST(account_id AS TEXT)` (community/visits queries) is standard SQL.
- `COALESCE`, `CASE WHEN`, `GROUP BY`, `LEFT JOIN`, `UNION` — all standard.

## 5. Data migration

One-time copy of the existing SQLite `data/adventures.db` into Postgres
(per-table `SELECT` → `INSERT`), preserving ids. Sequence/identity counters must
be advanced past the max migrated id.

## 6. Tests

- Parametrize (or duplicate) the storage tests to run against a Postgres instance
  (e.g. a docker-compose `postgres` service or `testcontainers`).
- Keep the SQLite suite as-is; both backends must pass the same behavioural tests.
