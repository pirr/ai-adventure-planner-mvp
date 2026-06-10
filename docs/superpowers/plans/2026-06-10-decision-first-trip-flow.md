# Decision-First Trip Flow Implementation Plan

> **For agentic workers:** implement on branch `decision-first-trip-flow`. Keep the backend API schema unchanged for this pass.

**Goal:** Make the app feel like a local travel decision assistant: user sets location, taps one primary "Find best trip now" action, and receives one ready-to-go recommendation first, with other options secondary.

**Architecture:** Vanilla JS static frontend over FastAPI. Reuse existing hidden wizard inputs as the payload source, but make launcher presets fully own that hidden state. Render the first recommendation as a decision card using existing response fields and best-effort OSM tags.

**Tech stack:** `frontend/index.html`, `frontend/app.js`, `frontend/mood.js`, `frontend/styles.css`, `frontend/mood.css`; FastAPI serves static files from `/static`.

---

## Task 1: Primary "Best Trip Now" Entry

- [ ] Update the launcher so the first visible flow is location -> "Find best trip now".
- [ ] Keep mood presets below the primary CTA as secondary choices.
- [ ] Use daypart-aware smart defaults for the primary CTA:
  - Morning: `available_minutes=120`, `interests=["viewpoints","food","nature"]`
  - Afternoon: `available_minutes=120`, `interests=["history","viewpoints","nature"]`
  - Evening: `available_minutes=120`, `interests=["viewpoints","food"]`
  - Shared defaults: `transport_mode=car`, `group_type=solo`, `children_ages=[]`, `intensity=easy`, `max_walking_km=3`, `use_live_data=true`

## Task 2: Presets Own Hidden State

- [ ] Replace partial preset application with a single state writer that sets all payload-affecting hidden fields.
- [ ] Solo/couple presets must clear `children_ages`, `withDog`, `withElderly`, and `reducedMobility`.
- [ ] Family preset must set `group_type=family` without silently injecting default child ages.
- [ ] Quick preset must shorten time/walking.
- [ ] Active/surprise-style presets may increase walking limit.

## Task 3: Decision Card Result Hierarchy

- [ ] Render the first recommendation as "Best trip right now".
- [ ] Show score label, leave-now/back-by timing, travel/walk/difficulty, arrival weather, route CTA, why-now bullets, warnings, data confidence, and practical details.
- [ ] Render remaining recommendations under "Other good options".
- [ ] Keep map marker selection, card click behavior, route tracking, feedback, visited, language switching, and photos working.

## Task 4: Hide Technical Controls

- [ ] Move manual coordinates into Advanced on the launcher.
- [ ] Keep live-data toggle out of the primary flow; it remains hidden in the old wizard and defaults to checked.
- [ ] Keep score breakdown collapsed and visually secondary.
- [ ] Keep result filter facets accessible through the existing filter bar after results.

## Task 5: Verification

- [ ] Run the app with `docker compose up --build`.
- [ ] Fresh load: location CTA and primary best-trip CTA are visible; manual coordinates are secondary.
- [ ] Payload check: primary CTA sends no stale child ages.
- [ ] Preset check: solo/couple/family/quick/surprise write expected hidden state.
- [ ] Results check: first item is a decision card and other options are secondary.
- [ ] Regression check: map pins, active card selection, Show others, feedback, visited/history, EN/RU switching, and no relevant console errors.
- [ ] Responsive check: desktop and mobile have no clipped controls, overlapping text, or hidden primary actions.

## Assumptions

- No backend schema or data-enrichment changes in this pass.
- Parking, toilet, cafe, entrance, and opening-hours facts are shown only when already available in `tags`; otherwise the UI does not claim them.
- Richer practical-data enrichment is deferred.
