# MVP 0.1.1 — Scope & Implementation Plan

## Context

MVP 0.1 proved the core loop: location → preferences → ranked nearby adventures with explanations →
Open in Maps → feedback. Two gaps motivate the next iteration:

1. **Weather is only computed at the user's origin, "now."** `get_weather(request.lat, request.lon)`
   (`backend/app/services/recommendations.py:17`) fetches a single current-conditions summary that is
   reused for every candidate and shown in one block. For a place 90 km / 2 h away this is misleading:
   the weather on arrival is what matters. Users want to see the weather *at the destination* and *at the
   hour they arrive* — e.g. "the drive is 2 h, so show the destination at +1h, +2h, +3h."
2. Three smaller items from the 0.1 plan were never built: an **Apple Maps** link (only Google exists),
   **feedback reasons**, and **analytics events** (needed to actually measure the 0.1 success KPIs).

**The headline of 0.1.1 is the destination/arrival weather feature.** The other three are small,
self-contained add-ons bundled in.

Two product decisions taken for the weather feature:
- **Re-score the top picks** with arrival weather (a place that's rainy on arrival should rank lower) —
  not display-only.
- Show a **journey + arrival hours** timeline per place (hourly from now, through travel, into the visit).

## Hypothesis refinement

> Recommendations are more trustworthy when the weather shown is the weather the user will actually meet
> on arrival, not the weather where they are standing now.

---

## Included now (0.1.1)

- Per-place **destination weather** with an hourly **arrival timeline** (`+1h / +2h / +3h …`).
- **Re-scoring** of top candidates using the weather at their arrival time.
- **Apple Maps** deep link alongside Google Maps.
- **Feedback reasons** (too far / too difficult / bad weather / not interesting / inaccurate / other).
- **Lightweight analytics events** to measure the 0.1 KPIs.

## Still deferred

LLM/AI explanation layer · accounts · Postgres/PostGIS + Supabase migration · Redis cache ·
personalization · event / live-traffic / community layers. (Unchanged from 0.1.)

---

## Feature 1 (headline) — Destination & arrival weather + re-scoring

### Backend

**`schemas.py`** — add an hourly model and two recommendation fields:
- `HourlyForecast`: `time` ("HH:MM"), `hour_offset` (1, 2, 3…), `label` (localized sky condition),
  `temperature_c`, `precipitation_mm`, `wind_kmh`, `is_arrival` (bool).
- On `Recommendation` (`schemas.py:92`): `arrival_weather: WeatherSummary | None = None` and
  `forecast: list[HourlyForecast] = []`.

**`services/weather.py`** — add destination forecasting, reusing `_weather_score` (`weather.py:13`) and
`weather_label` (`i18n.py:156`):
- `async get_destination_forecasts(points, use_live_data, lang) -> list[DestinationForecast | None]`.
  - **One bulk Open-Meteo call** for all points: comma-separated `latitude`/`longitude`, `timezone=auto`,
    `past_days=1`, `forecast_days=2`, `current=temperature_2m`,
    `hourly=temperature_2m,precipitation,weather_code,wind_speed_10m,uv_index`, `daily=sunset`.
    Multi-point responses are arrays, single-point is an object — normalize with
    `data if isinstance(data, list) else [data]`.
  - Best-effort: if `use_live_data` is false, `use_open_meteo_fallback` is off, or the request fails →
    return `[None] * len(points)` (no crash, no timeline).
- `DestinationForecast` dataclass: parsed hourly arrays + `now_index` (matched from `current.time`'s hour
  prefix) + `sunsets`, with `at_arrival(one_way_minutes, activity_minutes, lang) -> (WeatherSummary, list[HourlyForecast])`:
  - `arrival_offset = max(1, ceil(one_way_minutes / 60))`; timeline = offsets
    `1 .. min(arrival_offset + ceil(activity_minutes / 60), 6)`, each mapped to `now_index + offset`;
    `is_arrival` set on `arrival_offset`.
  - Arrival `WeatherSummary` (source `"open-meteo"`, `confidence="live"`) from the arrival-hour values,
    with `rain_mm_last_24h` = sum of the 24 hourly precip values up to arrival and `sunset` for the
    arrival date; scored via `_weather_score(temp, rain_now, rain_24h, wind, uv, code)`.

**`services/scoring.py`** — carry the new data through:
- Add `arrival_weather` and `forecast` fields to `ScoredCandidate` (`scoring.py:39`).
- In `to_recommendation` (`scoring.py:268`) pass both onto the `Recommendation`.
- **No new scoring math:** `score_candidate` (`scoring.py:227`) already consumes `weather.score` and the
  rain/temp/wind/uv/sunset fields, so re-scoring is just calling it again with the arrival summary.

**`services/recommendations.py`** — two-phase ranking in `build_recommendations` (`recommendations.py:15`):
1. Keep the current origin-weather scoring + sort (cheap first pass over up to 40 candidates).
2. Take a pool of `min(len, request.limit + 5)` top candidates; `get_destination_forecasts` for their
   coords; for each with a forecast, compute `(arrival_weather, timeline)` and re-score via
   `score_candidate(place, route, arrival_weather, request)`, attaching `arrival_weather` / `forecast`.
   Candidates without a forecast keep their origin-based score.
3. Re-sort `rescored + rest`, take top `limit`, then run the existing photo fetch and
   `rejected_from_scored` against the final list. The origin `weather` block stays in the response
   (relabel it client-side as "current location").

**`tests/test_scoring.py`** — add cases for `DestinationForecast.at_arrival` (timeline offsets, arrival
marker position derived from travel time, the 24 h rain window) and that a rainy arrival summary lowers
`weather_fit`.

### Frontend

- **`index.html`**: add `<div class="place-weather"></div>` after `<div class="badges"></div>` in the
  `recommendationTemplate` (`index.html:142`).
- **`app.js`**: `renderPlaceWeather(node, item)` called from `renderCards` (`app.js:461`). Renders a
  title + Weather-Fit badge (`item.arrival_weather.score`), an "on arrival" summary line, and a horizontal
  `.forecast-strip` of `+Nh / HH:MM / temp / sky (+rain)` chips with the arrival hour highlighted. Hide the
  block when both fields are empty (live-off / fallback). New i18n keys `place_weather_title`, `on_arrival`,
  `forecast_arrival` (EN+RU); reuse existing `weather_fit` and `unit_h`. Works with the language re-render
  path (`setLang` → `renderResults`); backend-composed strings stay in their fetched language as today.
- **`styles.css`**: `.place-weather` (tinted panel using the blue badge palette), `.forecast-strip`
  (flex row, horizontal scroll on mobile), `.forecast-hour` chip with a green-accent `.arrival` variant.

---

## Feature 2 — Apple Maps deep link

- **`services/geo.py`**: add `apple_maps_url(o_lat, o_lon, d_lat, d_lon, mode)` →
  `https://maps.apple.com/?saddr=…&daddr=…&dirflg=` with `dirflg` = `d` (car) / `w` (walk); bike → `w`.
  Mirror `google_maps_url` (`geo.py:16`).
- **`schemas.py`**: add `apple_map_url: str` to `RouteInfo` (`schemas.py:66`) and `Recommendation`.
- **`services/routing.py`**: populate `apple_map_url` in `fallback_route` (`routing.py:16`) and
  `osrm_route` (`routing.py:32`); pass through in `to_recommendation`.
- **Frontend**: add a second link to the template `.actions` (`index.html:163`); set `href` + i18n
  `open_apple_maps` in `renderCards`.

---

## Feature 3 — Feedback reasons

- **`schemas.py`**: tighten `FeedbackRequest.reason` (`schemas.py:132`) to an optional Literal:
  `too_far | too_difficult | bad_weather | not_interesting | inaccurate | other`. The DB column already
  exists (`storage.py:46`) — no migration.
- **Frontend**: on 👎 (optionally 👍), reveal an inline reason chip-picker in the card and pass the chosen
  reason into `submitFeedback(id, rating, reason)` (`app.js:546`). EN+RU labels for the six reasons.
- No backend storage change — `save_feedback` already persists `reason` (`storage.py:70`).

---

## Feature 4 — Analytics events

- **`storage.py`**: add an `events` table (`id, created_at, event, request_id, recommendation_id, meta`)
  plus `save_event(...)` and `events_summary()`, mirroring the feedback methods (`storage.py:70-80`).
- **`schemas.py`**: add `AnalyticsEvent` (`event: Literal[...]`, optional `request_id`,
  `recommendation_id`, `meta`).
- **`main.py`**: add `POST /api/events` (store) and `GET /api/events` (counts), next to the feedback
  routes (`main.py:67-76`).
- **Frontend**: a `track(event, props)` helper POSTing to `/api/events`, fired on `search_started`
  (start of `runSearch`, `app.js:387`), `search_completed` (`renderResults`), `recommendation_opened`
  (breakdown `<details>` toggle), `maps_opened` (Google/Apple link click), `feedback_submitted`
  (`submitFeedback`). Fire-and-forget; never blocks the UI.

---

## Verification

- **Run:** `docker compose up --build` (UI + API at `http://localhost:8080`) or
  `cd backend && uvicorn app.main:app --reload`.
- **Weather (manual):** open the app → "Use Tivat demo" → Find adventure. Each card shows a destination
  weather strip with `+1h / +2h / +3h …` and a highlighted **arrival** hour. With a **car** trip to a far
  place, confirm the arrival marker lands later than for a near place. Toggle EN/RU — static labels switch,
  card data stays. Uncheck "Use live data" → no strip, no errors.
- **Re-scoring:** confirm `weather_fit` in a card's breakdown reflects the *arrival* summary, and a
  rainy-on-arrival candidate ranks below a clear one it would otherwise tie with.
- **Apple Maps:** both Maps links open correct directions for car vs walk.
- **Feedback reasons:** 👎 → pick a reason → `GET /api/feedback` shows the stored `reason`.
- **Analytics:** complete a search, open Maps, vote → `GET /api/events` shows the five event types.
- **Tests:** `cd backend && pytest` (existing `test_scoring.py`, `test_place_photos.py` + new forecast cases).