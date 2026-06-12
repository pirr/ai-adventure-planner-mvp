# MVP 0.3 — Google Places enrichment (hybrid OSM + Google)

## Context

Place quality today is a tag heuristic: `_quality_from_tags()` in
`backend/app/services/places.py` adds points for a wikipedia tag or opening hours, which can't
tell a beloved viewpoint from an abandoned one. Photos come from Wikimedia Commons
(`place_photos.py`), which is sparse outside famous landmarks — many cards ship without an image.

This milestone keeps **OSM as the only candidate source** (free, strong for nature POIs) and adds
an **optional Google Places enrichment pass** over the small re-scoring pool (≤ `limit + 5`
places): real `rating` / `userRatingCount` feed `quality_score`, and Google photos backfill cards
that Commons can't cover. The feature is **off unless `GOOGLE_PLACES_API_KEY` is set** — without a
key the pipeline is byte-for-byte unchanged, so tests and the offline eval are unaffected.

Cost & ToS constraints that shape the design:

- **Pay-per-request.** Enrichment is capped at the pool (≤ 15 Text Search calls per search worst
  case) and results are cached in-process by `source_id` with a TTL, so repeat searches around the
  same area are nearly free. Photo media is resolved **only** when Commons/Wikidata yields nothing.
  On top of that, app-side daily budgets (global + per-user, Workstream 6) and Cloud Console quota
  caps (deployment checklist) bound the worst case at four independent layers.
- **Field masks are the price lever.** We request only
  `places.id,places.location,places.rating,places.userRatingCount,places.photos`.
  Verify the current SKU mapping at https://developers.google.com/maps/billing-and-pricing
  before launch — opening hours were deliberately left out (higher SKU, low value for us).
- **Caching:** Google allows caching most place fields for up to 30 days (place IDs
  indefinitely). The TTL default (24 h) stays well inside that; no Google data is written to SQLite.
- **Attribution:** ratings and photos must be attributed to Google when shown. The frontend card
  gets a `· Google` suffix on the rating and the existing photo-credit line carries Google's
  `authorAttributions`.
- **No API key in the browser:** the photo media URL normally embeds the key, so the backend
  resolves it with `skipHttpRedirect=true` and ships the resulting key-less
  `lh3.googleusercontent.com` URI to the client.

Branch: `feature/google-places-enrichment` off `main`. Backend-first; the frontend change is one
small render addition at the end.

---

## Workstream 1 — Config (`backend/app/config.py`)

New settings, following the existing env-var pattern:

```python
# Optional Google Places enrichment. Off unless an API key is set: without a
# key the recommendation pipeline is unchanged (OSM-only quality and photos).
google_places_api_key: str | None = os.getenv("GOOGLE_PLACES_API_KEY")
google_places_url: str = os.getenv("GOOGLE_PLACES_URL", "https://places.googleapis.com/v1")
google_places_timeout_seconds: float = float(os.getenv("GOOGLE_PLACES_TIMEOUT_SECONDS", "5"))
# In-process cache TTL per place. Google ToS allows caching most fields up to
# 30 days; keep the default well inside that.
google_places_cache_ttl_seconds: int = int(os.getenv("GOOGLE_PLACES_CACHE_TTL_SECONDS", "86400"))
# Max places enriched per request (cost cap; the re-scoring pool is ≤ limit+5 ≤ 15).
google_places_max_enriched: int = int(os.getenv("GOOGLE_PLACES_MAX_ENRICHED", "15"))
```

Add `GOOGLE_PLACES_API_KEY` (empty) to `.env.example` / compose environment so docker compose
passes it through.

## Workstream 2 — Enrichment service (`backend/app/services/google_places.py`, new)

A self-contained module mirroring the style of `weather.py` / `place_photos.py`:

```python
@dataclass
class GooglePlaceInfo:
    rating: float
    rating_count: int
    photo_name: str | None       # "places/{id}/photos/{ref}", resolved lazily
    photo_attribution: str | None
```

- `enabled() -> bool` — `settings.google_places_api_key` is truthy.
- `async _search_text(client, place) -> dict | None` — POST
  `{settings.google_places_url}/places:searchText`, headers `X-Goog-Api-Key` and
  `X-Goog-FieldMask: places.id,places.location,places.rating,places.userRatingCount,places.photos`,
  body:

  ```json
  {"textQuery": "<place.name>", "maxResultCount": 1,
   "locationBias": {"circle": {"center": {"latitude": lat, "longitude": lon}, "radius": 1000.0}}}
  ```

  `locationBias` only *biases*, so guard against wrong-city matches: reject the result when
  `haversine_km(place.lat, place.lon, result_lat, result_lon) > 1.0` (reuse
  `app.services.geo.haversine_km`) or when `rating`/`userRatingCount` are missing.
- `async enrich_places(places: list[PlaceCandidate], anonymous_id: str | None) -> tuple[dict[str, GooglePlaceInfo], list[str]]`
  — returns `{source_id: info}` plus warnings. Serves from a module-level
  `{source_id: (expires_at, info | None)}` TTL cache first (negative results cached too, so
  unmatched places don't re-bill every search), then reserves daily budget for the misses
  (Workstream 6 — cache hits are free and skip the budget) and fans the granted ones out with
  `asyncio.gather` over one `http_client(settings.google_places_timeout_seconds)`, capped at
  `settings.google_places_max_enriched`. Per-place errors are swallowed; if **every** live call
  fails, return one `warn_google_unavailable` warning (new i18n key, en + ru, same shape as
  `warn_osm_unavailable`). Never raises.
- `async resolve_photo(photo_name, attribution) -> PlacePhoto | None` — GET
  `{settings.google_places_url}/{photo_name}/media?maxWidthPx=960&skipHttpRedirect=true&key=…`,
  returning `PlacePhoto(url=payload["photoUri"], source="Google Maps", attribution=attribution)`.
  The `photoUri` is a key-less googleusercontent URL safe to send to the browser.
- `blended_quality(osm_quality: int, rating: float, rating_count: int) -> int` — pure, the core
  quality upgrade:

  ```python
  def blended_quality(osm_quality: int, rating: float, rating_count: int) -> int:
      # Confidence grows with review volume: ~1k reviews → trust Google fully.
      weight = min(1.0, math.log10(rating_count + 1) / 3)
      google_quality = rating / 5 * 100
      return round((1 - weight) * osm_quality + weight * google_quality)
  ```

  A 4.8★ place with 2 000 reviews → ~96 regardless of OSM tags; a 5★ place with 3 reviews barely
  moves the heuristic. Clamped 0–100 by the existing `quality_score` field validator.

## Workstream 3 — Pipeline integration (`recommendations.py`, `schemas.py`)

- `PlaceCandidate` gains optional enrichment fields (default `None`, so nothing changes for
  OSM-only runs): `rating: float | None`, `rating_count: int | None`,
  `google_photo_name: str | None`, `google_photo_attribution: str | None`.
- `Recommendation` gains `rating: float | None` and `rating_count: int | None`;
  `to_recommendation` copies them from `place`.
- In `build_recommendations`, enrichment runs **only over the re-scoring pool** and concurrently
  with the destination forecasts (no added latency beyond the slower of the two):

  ```python
  pool, rest = scored[:pool_size], scored[pool_size:]
  # create_task starts the calls now, so they overlap the forecast await below
  # (a bare coroutine would only start when awaited, i.e. sequentially).
  enrichment_task = asyncio.create_task(
      enrich_places([c.place for c in pool], request.anonymous_id)
  ) if (request.use_live_data and google_places.enabled()) else None
  forecasts = await get_destination_forecasts(...)        # existing call
  if enrichment_task is not None:
      info_by_id, google_warnings = await enrichment_task
  ```

  In the existing rescore loop, before `score_candidate` runs: when `info_by_id` has the place,
  set `place.rating` / `place.rating_count` / photo fields and
  `place.quality_score = blended_quality(place.quality_score, info.rating, info.rating_count)`.
  The `forecast is None` branch must also re-score enriched candidates (today it appends the
  candidate untouched) — re-run `score_candidate` with the origin weather there when the place was
  enriched. `google_warnings` joins `data_warnings`.
- Rating influences ranking through the existing `place_quality` term (weight 0.09) — no formula
  change, no effect on `order_key` rotation logic.

## Workstream 4 — Photo fallback (`place_photos.py`)

`get_place_photo(place, use_live_data)` keeps its priority order and adds Google **last** (free
sources first):

1. OSM `image` / Commons tags (existing)
2. Wikidata P18 (existing)
3. New: if `place.google_photo_name` is set → `google_places.resolve_photo(...)`, wrapped in the
   same `try/except → None` as the Wikidata step.

## Workstream 5 — Frontend rating display (`frontend/app.js`, `index.html`)

- On the result card, when `item.rating` is present render `★ 4.6 (1 234) · Google` next to the
  title (new i18n strings `rating_label` en/ru; count formatted with the existing locale helpers).
  The `· Google` suffix is the required attribution and must not be omitted.
- Photo credit: the existing `photo_source` line already renders `photo.source` /
  `photo.attribution`, so Google photos get "Photo: Google Maps" + author attribution for free —
  verify, don't rebuild.
- Bump the `?v=` cache-bust query in `index.html`.

## Workstream 6 — Daily budget limiter (`storage.py`, `google_places.py`, `config.py`)

Two app-side caps so cost stays bounded even under heavy/abusive use, both checked inside
`enrich_places` for cache **misses only**. When a budget is exhausted, enrichment silently turns
off until the next UTC day and search continues OSM-only (no user-facing error).

New settings (env-var pattern as above):

```python
# App-side daily budgets for Google calls. Keep the global limit *below* the
# Cloud Console quota cap so the app cuts off first (see deployment checklist).
# 0 disables enrichment entirely.
google_places_daily_limit: int = int(os.getenv("GOOGLE_PLACES_DAILY_LIMIT", "800"))
# Per-anonymous_id daily cap (~4 enriched searches). Soft fairness control:
# the id is client-supplied, so the global limit is the real backstop.
google_places_user_daily_limit: int = int(os.getenv("GOOGLE_PLACES_USER_DAILY_LIMIT", "60"))
```

One new table via the existing `_init_db()` `CREATE TABLE IF NOT EXISTS` script (self-migrates on
startup, same as `place_marks`); counters persist across container restarts:

```sql
CREATE TABLE IF NOT EXISTS api_usage (
  day   TEXT NOT NULL,   -- UTC "YYYY-MM-DD"
  scope TEXT NOT NULL,   -- "global" | "user"
  key   TEXT NOT NULL,   -- "" for global, anonymous_id for user
  count INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (day, scope, key)
);
```

One new `Storage` method, check-and-increment in a single transaction so concurrent requests
can't both overdraw:

```python
def reserve_google_calls(self, anonymous_id: str | None, requested: int) -> int:
    """Grant up to `requested` Google calls within today's budgets.

    Returns min(requested, global remaining, user remaining) and records the
    grant against both counters ("global"/"" and "user"/anonymous_id) via
    ON CONFLICT(day, scope, key) DO UPDATE SET count = count + granted.
    Requests without an anonymous_id get no enrichment (return 0): they can't
    be rate-limited individually, so they don't get to spend the budget.
    """
```

In `enrich_places`, after the cache pass: `granted = storage.reserve_google_calls(anonymous_id,
len(misses))`; enrich only `misses[:granted]`. Failed calls still count against the budget
(conservative — never under-counts spend). Old `api_usage` rows are deleted opportunistically
(`DELETE FROM api_usage WHERE day < ?` with a 7-day cutoff inside the same transaction) so the
table stays tiny.

## Workstream 7 — Tests (`backend/tests/test_google_places.py`, new)

Follow the repo's monkeypatch style (`test_rotation.py`); no live HTTP in tests.

- `blended_quality`: high-review-count rating dominates; tiny counts barely move the OSM score;
  result stays within 0–100.
- `enrich_places` (monkeypatch `_search_text`): builds `{source_id: info}`; a match > 1 km away is
  rejected; per-place exceptions are swallowed; all-fail → single `warn_google_unavailable`; second
  call within TTL does not re-invoke `_search_text` (positive *and* negative cache hits).
- Disabled path: with `google_places_api_key=None`, `build_recommendations`
  (`use_live_data=False`, sample places, `TemplateProvider`) returns identical output with the
  module imported — proves the no-key pipeline is untouched.
- `get_place_photo`: Commons tag wins over `google_photo_name`; Google photo used only when free
  sources yield nothing (monkeypatch `resolve_photo`).
- `to_recommendation` copies `rating` / `rating_count`.
- `reserve_google_calls` (pure storage, temp DB like `test_place_marks.py`): grants are clamped by
  the global and the per-user limit, whichever is tighter; two users draw down the shared global
  budget; `anonymous_id=None` → 0; counters are per-day (monkeypatch the day function — a new day
  grants again); rows older than 7 days are pruned.
- `enrich_places` budget path (monkeypatch `_search_text` and storage): cache hits consume no
  budget; with `granted=0` no HTTP call is made and search still returns OSM-only results.

---

## Deployment checklist — Google Cloud hard caps

App-side limits bound normal operation; these Console settings are the backstop that holds even
if the app misbehaves. Do all three before pointing a real key at production:

1. **Quota caps** (*APIs & Services → Quotas*, Places API): set "requests per day" a bit **above**
   `GOOGLE_PLACES_DAILY_LIMIT` (e.g. app 800 / Cloud 1 000) so the app's limiter normally cuts off
   first and the quota only catches bugs. Cap per-minute requests too (~50). Past the quota Google
   returns 429 and bills nothing; `enrich_places` already degrades to OSM-only on errors.
2. **Key restrictions** (*APIs & Services → Credentials*): restrict the key to the Places API only
   and add an IP restriction for the server's egress address. The key is backend-only by design
   (never shipped to the browser — see the photo `skipHttpRedirect` flow), so no referrer
   restriction applies.
3. **Billing budget + alerts** (*Billing → Budgets*): monthly budget with 50/90/100% alerts.
   Budgets only notify, they do **not** stop spending — the quota cap in step 1 is the actual
   stop. Skip the budget→Pub/Sub→disable-billing automation for the MVP; it kills every API in
   the project when triggered.

---

## Verification

- `docker compose run --rm --no-deps app python -m pytest -q` — all green, incl. the new tests,
  **without** `GOOGLE_PLACES_API_KEY` set.
- `docker compose run --rm --no-deps app python -m eval.run` — unchanged (no key in eval env).
- With a real key in `.env`: `docker compose up --build`, search from the LAN IP (not localhost)
  via Playwright; confirm cards show `★ … · Google`, photo-less OSM places now have Google photos
  with attribution, and the photo URL is `lh3.googleusercontent.com` (no API key in any URL —
  check the network tab / `browser_network_requests`).
- Repeat the same search twice; confirm via DEBUG logs that the second run hits the cache (no new
  Google calls).
- Budget cutoff: restart with `GOOGLE_PLACES_DAILY_LIMIT=1`, search from a fresh browser profile
  (new `anonymous_id`, empty cache) → results come back OSM-only (no ratings) and the app logs the
  exhausted budget; no error surfaces in the UI.
- Cost sanity: one search ≤ 15 Text Search calls + ≤ `limit` photo resolutions, then cached 24 h.
- All via docker compose (no local venv).
