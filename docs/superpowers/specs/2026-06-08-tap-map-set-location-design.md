# Tap-to-set start location on a map-first start screen

**Date:** 2026-06-08
**Branch:** `design/guided-explorer`
**Status:** Design approved — ready for implementation plan

## Summary

On the start screen, make the map the primary surface. The user opens the app to a
map-dominant view and sets their start location two ways — **tapping anywhere on the
map** to drop a pin, or **"Use my location"** (GPS). The moment a location is set, the
launcher sheet **slides up** automatically and the user continues into setup (vibe
presets). The intent is a flow that feels intuitive and comfortable: see the map →
set where you are → continue.

This replaces the current behaviour where the map is a thin, non-interactive strip
behind a bottom sheet and the only way to pick a point on the map is a separate
full-screen "Choose on map" picking mode.

## Current state (for context)

The `design/guided-explorer` branch layers a "mood launcher" (`mood.js` + `mood.css`)
over the original guided wizard:

- The original 3-step wizard (`#planner` / `.wizard`) is hidden — `.planner { display:
  none !important; }`. Its chips/inputs (`#timeChips`, `#interestChips`, `#lat`,
  `#lon`, …) remain in the DOM as hidden state that the launcher writes into.
- The visible start UI is the **launch sheet** (`#launchSheet` / `.launch-sheet`): a
  bottom sheet (`height: min(64vh, 560px)`) showing a daypart greeting, a two-button
  location row (**Use my location** / **Choose on map**), a collapsible coordinate
  entry, and vibe preset cards.
- The **map** (`#map`, Leaflet) is forced visible in planning (`body.planning #map {
  display: block !important; }`) but sits at `z-index: 0` behind the sheet, so only a
  ~36% strip at the top is visible, and it is not tappable for location.
- A separate **full-screen picking mode** exists: the "Choose on map" button calls
  `enterPick()`, which adds `body.picking`, hides the sheet, shows `#pickUi` ("Tap
  anywhere on the map to set your start"), and wires a map `click` handler
  (`onMapPick`) that only fires while `body.picking` is set.

### Key existing seams the implementation reuses

- `window.setLocation(lat, lon, label)` (app.js) → `setOrigin(lat, lon, { recenter:
  true })`; updates `#lat`/`#lon`, moves `originMarker`, recenters when asked, and sets
  `#locationStatus`.
- `ensureMap()` (app.js, exposed as `window.ensureMap`) builds the Leaflet map, the
  draggable `originMarker` (drag → `setOrigin`), and `resultsLayer`. The map instance is
  exposed as `window.appMap`.
- mood.js holds `placeLabel` (drives the topbar chip `#ctxLoc` via `locName()` /
  `updateContext()`), `useMyLocation()` (calls `requestGeolocation`, sets label "My
  location"), and the launcher build (`buildLauncher`) + grip toggle.
- The grip currently tap-toggles `#launchSheet.min` (60px) ↔ full height.

## Goals

1. Start screen opens **map-dominant**, with a slim location bar overlaid.
2. **Tapping the visible map** sets the start location directly — no intermediate
   button or mode.
3. **"Use my location"** (GPS) remains available in the bar.
4. On the **first** location-set (tap, GPS, or coords), the sheet **slides up** to the
   full launcher so the user continues setup.
5. Intuitive feedback: a hint until a location exists; a pin once it does.

## Non-goals (YAGNI)

- No reverse geocoding / place-name lookup. The label stays a simple string
  ("Map point" / "My location").
- No address search box.
- No automatic geolocation request on page load (privacy / permission-prompt UX).
- No continuous drag-to-resize gesture on the sheet — grip tap-toggle only.
- No backend, template, or data-model changes.

## Design

### 1. Sheet state machine & the slide-up

Replace the current full ↔ `.min` toggle with two named states:

- **`peek`** — the new default when entering planning. The sheet is a **slim location
  bar** (~130px tall) pinned to the bottom; the map fills the remaining ~80% of the
  screen. Peek content is location-only (see §3).
- **`open`** — the full launcher (greeting, daypart switch, vibe presets), height
  `min(64vh, 560px)`. Content unchanged from today, minus the location row (which moves
  to the peek bar) — the coordinate-entry collapsible stays here.

Transitions:

- **Auto slide-up:** the **first** time a location is set within a planning session —
  via map tap, GPS, or coordinate entry — the sheet animates `peek → open`. A
  session-scoped flag (e.g. `sheetAutoOpened`) ensures this happens once; later location
  changes (e.g. dragging the pin) do not re-trigger it.
- **Manual:** the grip tap-toggles `peek ↔ open` (collapse back to the map to re-pin).
- Entering planning (fresh or via "Edit trip") resets the sheet to `peek`.
- Entering exploring (results) hides the launch sheet, as today
  (`body.exploring .launch-sheet { display: none; }`).

CSS: the launch sheet gets a transition on the animated property (height or transform)
so `peek → open` reads as a slide. The `.min` rule and its grip handling are retired.

### 2. Map tap interaction & feedback

- **One always-on map click handler** registered in mood.js once the map exists (on
  init / when entering planning). It acts only when `body.planning` is set (never in
  exploring; the old `body.picking` path is removed). On click it sets the origin to the
  tapped lat/lng.
- **No recenter on tap.** The user tapped a point they can see, so the view must not
  jump. `setLocation` is extended:

  ```
  setLocation(lat, lon, label, { recenter = true } = {})
  ```

  The map-tap path calls it with `recenter: false`. GPS / demo / coordinate callers are
  unchanged (default `recenter: true`).
- **Pin hidden until set.** `originMarker` is hidden on load. Until the first
  location-set, a **map hint** ("Tap the map to set your start") is shown. On the first
  set (any method) the hint disappears and the draggable pin is shown at the location.
  Dragging the pin fine-tunes the origin (existing `originMarker` dragend → `setOrigin`).
- A small `locationSet` flag (mood.js) gates pin visibility and hint, and feeds the
  auto-open trigger in §1.

### 3. Slim location bar (peek) + cleanup

Peek bar contents:

```
┌──────────────────────────────────┐
│  ▁▁▁  (grip)                      │
│  [ ◎ Use my location ]            │
│  or tap the map to set your start │
└──────────────────────────────────┘
```

- Primary action: **Use my location** → existing `requestGeolocation` (via
  `useMyLocation`).
- Secondary affordance: a hint line telling the user they can tap the map.
- Once a location is set, the sheet is already sliding up; the chosen location is shown
  in the **topbar chip** (`#ctxLoc`, existing). The peek bar's job is done.
- The **coordinate-entry** collapsible moves into the `open` launcher (rare fallback),
  keeping the peek slim.

**Cleanup (single deletion):** the "Choose on map" button and the entire full-screen
picking mode are removed, since direct tap replaces them:

- `index.html`: remove the `#pickUi` block.
- `mood.js`: remove `enterPick` / `exitPick` / `confirmPick` / `onMapPick`'s
  `body.picking` guard usage / `setPickLabels` / `pickWired` and the `locMap` button
  wiring; replace `onMapPick` with the always-on planning handler from §2.
- `mood.css`: remove `.pick-ui`, `.pick-bar*`, `.pick-pin`, and `body.picking` rules.
- i18n: remove `pick_msg`, `pick_set`, `pick_cancel`, `choose_map`.

### 4. State & i18n

- **State:** reuses existing globals — `window.setLocation`, `window.ensureMap`,
  `window.appMap`, `originMarker`, and mood.js's `placeLabel` / `updateContext()`. New
  module-local flags in mood.js: `locationSet` (pin/hint gate) and `sheetAutoOpened`
  (one-shot slide-up).
- **i18n (mood.js `LANG`, EN + RU):**
  - Add: location-bar heading/label (reuse `use_loc` for the GPS button), the bar hint
    ("or tap the map to set your start"), and the on-map hint ("Tap the map to set your
    start"). (These may share one string where wording matches.)
  - Remove: `pick_msg`, `pick_set`, `pick_cancel`, `choose_map`.

### 5. Files touched

- `frontend/mood.js` — peek bar markup; always-on planning tap handler; auto-open on
  first location-set; `locationSet` / `sheetAutoOpened` flags; pin/hint gating; grip
  retargeted to `peek ↔ open`; i18n add/remove; remove picking-mode functions.
- `frontend/mood.css` — slim `peek` height; `open` height; slide transition;
  location-bar + hint styling; retire `.min` and `body.picking` rules.
- `frontend/index.html` — remove the `#pickUi` block.
- `frontend/app.js` — extend `setLocation` signature with `{ recenter }` (default
  `true`); ensure `originMarker` can start hidden / be shown on first set.

No backend, template, or data-model changes.

## Verification

No JS unit suite exists, so verification is **manual against the running app**, served
via `docker compose` (project convention), driven with Playwright + screenshots:

1. App opens **map-dominant** with the slim location bar; no pin, hint visible.
2. **Tap the map** → pin drops at the tapped point, view does **not** jump, sheet
   **slides up** to the vibe launcher, topbar chip shows the location.
3. **Use my location** (GPS, on `localhost` secure context) → pin set, sheet slides up.
4. **Coordinate entry** still sets the location and (if first) slides up.
5. **Grip** collapses `open → peek` back to the map; re-tapping the map re-pins without
   a second forced slide-up.
6. **Pick a vibe** → search runs → results (exploring) as before.
7. **EN and RU** both render correctly; no leftover picker strings/markup.

## Open risks / notes

- The launch sheet's animated property must be chosen so the slide is smooth on mobile;
  `originMarker` / Leaflet may need `map.invalidateSize()` after the sheet resizes
  (existing code already calls this on mode switches).
- Removing the full-screen picker is a deliberate simplification; if a future small-screen
  case needs a focused picker, it can be reintroduced behind the same `setLocation` seam.