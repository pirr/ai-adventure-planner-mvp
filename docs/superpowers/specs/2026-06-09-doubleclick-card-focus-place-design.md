# Double-click a place card → focus its place on the map

**Date:** 2026-06-09
**Branch:** `design/guided-explorer`
**Status:** Design approved — ready for implementation plan

## Summary

Double-clicking a place card in the results sheet centers the map on that place at a
closer zoom and collapses the cards sheet back to peek, so the user lands on the place
in the map. The double-clicked place also becomes the selected card/pin.

## Current state

- Result cards are built in `buildCard(item, isTop)` (`frontend/app.js`). Each card has a
  single **click** handler: `openSheet()` + `setActive(item.id, { pan: false, scroll: true })`.
- `setActive(id, { pan, scroll })` highlights the place's pin + card; `pan` uses
  `panToWithOffset` only while the sheet is **not** open.
- The results sheet (`#sheet` / `sheetEl`) toggles a `.open` class: open = expanded list,
  not-open = peek (map visible). `toggleSheet()` toggles `.open` and calls
  `map.invalidateSize()` after a 360ms transition. There is no `closeSheet` helper today.
- `panToWithOffset(latlng)` pans (at the current zoom) with a vertical offset of
  `sheetHeight()/2 - 30` so the target sits above the peek sheet.
- Pins carry `marker._latlng2 = [lat, lon]`; `markersById[id]` maps id → marker.

## Goal

Add a **double-click** interaction on a place card that:
1. Selects the place (highlight card + pin).
2. Collapses the sheet to peek (map visible).
3. Recenters the map on the place at **zoom 16**, offset above the peek sheet.

## Non-goals (YAGNI)

- No change to the single-click behavior.
- No long-press / alternative gesture (use `dblclick` as specified).
- No backend, data, or payload changes.

## Design

Add a `dblclick` listener on the card in `buildCard`, next to the existing `click`:

- Guard: ignore double-clicks whose target is inside `a, button, summary, input`
  (same guard as the click handler).
- Call a new helper `focusPlace(id, zoom = 16)`:
  1. `setActive(id, { pan: false, scroll: false })` — select without an extra pan/scroll.
  2. `sheetEl.classList.remove('open')` — collapse to peek.
  3. After the 360ms sheet transition: `map.invalidateSize()`, then center on the place's
     `latlng` at `zoom`, applying the peek-sheet offset. Reuse the `panToWithOffset` math
     but at the target zoom:
     ```
     const point = map.project(latlng, zoom).add([0, sheetHeight() / 2 - 30]);
     map.setView(map.unproject(point, zoom), zoom, { animate: true });
     ```
     (The 360ms wait ensures `sheetHeight()` reflects the collapsed peek height and the
     map has its post-collapse size.)

Works in both open and peek state — collapsing is a no-op when already peek, so a
double-click always focuses the place.

### Interaction with the click handler

A double-click also fires two `click` events; their only effect (`openSheet()` +
`setActive` scroll-into-view in the list) is harmless immediately before `focusPlace`
collapses the sheet. No click/dblclick disambiguation timer is needed.

## Files touched

- `frontend/app.js` — `focusPlace(id, zoom)` helper + the `dblclick` listener in
  `buildCard`.
- `frontend/index.html` — bump the `app.js` cache key (`?v=10` → `?v=11`).

## Verification

Manual via `docker compose up --build` + Playwright (navigate with a cache-busting query
so the browser fetches the new `app.js`):

1. Reach results, open the sheet (vertical list).
2. Double-click a non-top card → the sheet collapses to peek, the map recenters on that
   place at zoom 16 (assert `map.getZoom() === 16` and center ≈ the place latlng with the
   vertical offset), and that card/pin is the selected one.
3. Single-click still selects + scrolls without collapsing/zooming.
4. No console errors.

## Risks / notes

- `dblclick` fires on desktop double-click and mobile double-tap (browser-synthesized);
  reliability varies on some mobile browsers, acceptable per the request.
- The 360ms delay couples to the sheet's CSS transition duration (same constant
  `toggleSheet` already uses).
