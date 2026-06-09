# Double-click Card → Focus Place Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Double-clicking a place card collapses the results sheet to peek and recenters the map on that place at zoom 16.

**Architecture:** Add a `focusPlace(id, zoom)` helper in `app.js` and a `dblclick` listener on each result card (in `buildCard`), alongside the existing single-click handler. Reuse the existing `setActive`, `sheetEl`, `markersById`, and the `panToWithOffset` offset math.

**Tech Stack:** Vanilla JS (classic script), Leaflet, plain CSS. Served by FastAPI static hosting via `docker compose`. No JS test framework — verification is via Playwright against the running app.

---

## File Structure

| File | Responsibility | Change |
|------|----------------|--------|
| `frontend/app.js` | Map + result cards | `focusPlace(id, zoom)` helper; `dblclick` listener in `buildCard` |
| `frontend/index.html` | Markup | bump `app.js` cache key `?v=10` → `?v=11` |

---

## How to run / verify

```bash
docker compose up --build -d
# Playwright: navigate to the LAN address (localhost may be unreachable from the
# Playwright container). Find it with: ip route get 1.1.1.1  → use that src IP.
# IMPORTANT: append a cache-busting query (e.g. ?fresh=N) so the browser fetches the
# new app.js instead of a cached copy.
docker compose down   # when finished
```

To reach results: set a location (tap map / Use my location), open the launcher, click a vibe preset, wait for the results sheet.

---

## Task 1: focusPlace helper + dblclick listener

**Files:**
- Modify: `frontend/app.js` — add `focusPlace` after `panToWithOffset` (ends line 78); add `dblclick` listener in `buildCard` after the `click` listener (lines 829-833)
- Modify: `frontend/index.html` — bump `app.js` cache key

- [ ] **Step 1: Add the `focusPlace` helper**

Find (app.js, the end of `panToWithOffset`):

```js
function panToWithOffset(latlng) {
  if (!map) return;
  const z = map.getZoom();
  const point = map.project(latlng, z).add([0, sheetHeight() / 2 - 30]);
  map.panTo(map.unproject(point, z), { animate: true, duration: 0.4 });
}
```

Insert immediately after it:

```js
// Double-click a card: select the place, collapse the sheet to peek, and recenter
// the map on the place at a closer zoom (offset above the peek sheet).
function focusPlace(id, zoom = 16) {
  const marker = markersById[id];
  if (!map || !marker) return;
  setActive(id, { pan: false, scroll: false });
  sheetEl.classList.remove('open');
  // Wait for the sheet's collapse transition so sheetHeight() is the peek height
  // and the map has its post-collapse size, then recenter with the peek offset.
  setTimeout(() => {
    map.invalidateSize();
    const latlng = L.latLng(marker._latlng2);
    const point = map.project(latlng, zoom).add([0, sheetHeight() / 2 - 30]);
    map.setView(map.unproject(point, zoom), zoom, { animate: true });
  }, 360);
}
```

- [ ] **Step 2: Add the `dblclick` listener in `buildCard`**

Find (app.js lines 828-833):

```js
  // Tap a card: highlight its pin, and (from peek) open the sheet to read details.
  article.addEventListener('click', (event) => {
    if (event.target.closest('a, button, summary, input')) return;
    openSheet();
    setActive(item.id, { pan: false, scroll: true });
  });
```

Replace with:

```js
  // Tap a card: highlight its pin, and (from peek) open the sheet to read details.
  article.addEventListener('click', (event) => {
    if (event.target.closest('a, button, summary, input')) return;
    openSheet();
    setActive(item.id, { pan: false, scroll: true });
  });
  // Double-tap a card: focus its place on the map and collapse the sheet.
  article.addEventListener('dblclick', (event) => {
    if (event.target.closest('a, button, summary, input')) return;
    focusPlace(item.id);
  });
```

- [ ] **Step 3: Bump the app.js cache key**

Find (index.html):

```html
  <script src="/static/app.js?v=10"></script>
```

Replace with:

```html
  <script src="/static/app.js?v=11"></script>
```

- [ ] **Step 4: Verify behavior (Playwright)**

```bash
docker compose up --build -d
```

Navigate to the app with a cache-busting query (e.g. `http://<ip>:8000/?fresh=1`). Reach results, open the sheet (vertical list). Then, via `browser_evaluate`:
- Record `map.getZoom()` (expect 13) and `#sheet` has class `open`.
- Get a non-top card and dispatch a `dblclick` MouseEvent on it.
- After ~500ms, assert: `#sheet` no longer has `open` (collapsed to peek), `window.appMap.getZoom() === 16`, the dblclicked card has `is-active`, and the map center is near that place's latlng (within a small delta on lon, accounting for the vertical offset). Check `browser_console_messages` — no errors.

Also confirm a **single** click still selects + scrolls without collapsing or zooming (zoom stays 13, sheet stays open).

- [ ] **Step 5: Commit**

```bash
git add frontend/app.js frontend/index.html
git commit -m "feat(results): double-click a card to focus its place and collapse the sheet"
```

---

## Self-Review notes (author check)

- **Spec coverage:** select place (`setActive`, Step 1) ✓; collapse to peek (`classList.remove('open')`, Step 1) ✓; recenter at zoom 16 with peek offset (`setView` + offset, Step 1) ✓; guard inner controls (Step 2) ✓; single-click unchanged (Step 2 keeps it) ✓; works in open and peek (collapse is a no-op when already peek) ✓; cache bump (Step 3) ✓.
- **Naming consistency:** `focusPlace(id, zoom)`, `markersById`, `setActive`, `sheetEl`, `sheetHeight`, `panToWithOffset` math reused verbatim; cache key `?v=11`.
- **No placeholders:** every step shows exact code.
- **Hoisting:** `focusPlace` is a top-level function declaration (hoisted), so the `dblclick` listener referencing it is fine regardless of order.
