# Tap-to-set Location on Map-First Start Screen — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the start screen map-dominant, let the user set their start location by tapping the map (or "Use my location"), and slide the launcher sheet up into setup the moment a location is set.

**Architecture:** The frontend is two layered classic scripts — `app.js` (owns the Leaflet map, the origin pin, and `setLocation`/`setOrigin`) and `mood.js` (the visible "launcher" UI; calls app.js globals via `window.*`). We add a small seam: `setOrigin` becomes the single place that shows the pin on first set and dispatches a `document` `origin-set` CustomEvent. `mood.js` listens for that event to update the location chip and auto-open the sheet, registers an always-on map-click handler (replacing the old full-screen "picking" mode), and splits the launcher into a slim peek (location bar) vs. open (vibe presets) sheet. `setLocation` gains an optional `{ recenter }` so map taps don't jump the view.

**Tech Stack:** Vanilla JS (classic scripts, no build step), Leaflet, plain CSS. Served by FastAPI static hosting via `docker compose`. No JS test framework exists, so each task is verified by loading the running app and observing behavior (Playwright + screenshots / browser console).

---

## File Structure

| File | Responsibility | Change |
|------|----------------|--------|
| `frontend/app.js` | Map, origin pin, `setLocation`/`setOrigin` | Extend `setLocation` with `{recenter}`; create pin lazily (hidden until first set); show pin + dispatch `origin-set` from `setOrigin`; show pin when entering exploring |
| `frontend/mood.js` | Launcher UI, location label, sheet state | Remove full-screen picker; add always-on planning map-click; `origin-set` listener (chip + one-shot auto-open); split location bar (`buildLocBar`) from vibe launcher; grip toggles `open`; map-hint text; i18n add/remove |
| `frontend/index.html` | Markup | Add `#mapHint` on the map; add `#launchLoc` in the sheet; remove `#pickUi` block |
| `frontend/mood.css` | Launcher styles | Peek/open sheet heights + slide transition; `.launch-loc`/`.loc-cta`/`.map-hint` styles; remove pick-mode CSS; retire `.min` |

**Conventions to match:** `mood.js` is an IIFE using `var`, `function`, and a `$ = getElementById` helper; CSS uses the existing `:root` tokens (`--pine-800`, `--cream-hi`, `--clay-500`, `--spring`, `--ease`, `--topbar-h`, `--float-sm`, `--display`). Keep the same idiom.

---

## How to run / verify (used by every task)

Frontend is baked into the image (no bind mount), so rebuild to pick up changes:

```bash
docker compose up --build -d
# open http://localhost:8000/  (Leaflet map + launcher render only when served by the backend)
```

Stop with `docker compose down` when done. Geolocation works on `localhost` (secure context). For behavioral checks, drive the page with Playwright (`browser_navigate` to `http://localhost:8000/`, `browser_click`, `browser_take_screenshot`, `browser_console_messages`).

---

## Task 1: app.js — location seam (recenter option, lazy pin, `origin-set` event)

**Files:**
- Modify: `frontend/app.js` — `ensureMap` (line ~44), `setOrigin` (lines 52-59), `setLocation` (lines 526-529), `enterExploring` (lines 146-150)

- [ ] **Step 1: Create the origin pin lazily (hidden until first set)**

In `ensureMap`, remove `.addTo(map)` so the marker exists but is not shown yet.

Find (app.js ~line 44):

```js
  originMarker = L.marker([lat, lon], { draggable: true, icon: youIcon }).addTo(map);
```

Replace with:

```js
  originMarker = L.marker([lat, lon], { draggable: true, icon: youIcon });
```

- [ ] **Step 2: Show the pin on first set + dispatch `origin-set` from `setOrigin`**

Find (app.js lines 52-59):

```js
function setOrigin(lat, lon, { recenter = false } = {}) {
  const latNum = Number(lat);
  const lonNum = Number(lon);
  $('lat').value = latNum.toFixed(6);
  $('lon').value = lonNum.toFixed(6);
  if (originMarker) originMarker.setLatLng([latNum, lonNum]);
  if (map && recenter) map.setView([latNum, lonNum], Math.max(map.getZoom(), 13));
}
```

Replace with:

```js
function setOrigin(lat, lon, { recenter = false } = {}) {
  const latNum = Number(lat);
  const lonNum = Number(lon);
  $('lat').value = latNum.toFixed(6);
  $('lon').value = lonNum.toFixed(6);
  if (originMarker) {
    originMarker.setLatLng([latNum, lonNum]);
    if (map && !map.hasLayer(originMarker)) originMarker.addTo(map); // reveal pin on first set
  }
  if (map && recenter) map.setView([latNum, lonNum], Math.max(map.getZoom(), 13));
  document.dispatchEvent(new CustomEvent('origin-set', { detail: { lat: latNum, lon: lonNum } }));
}
```

- [ ] **Step 3: Add the `{ recenter }` option to `setLocation`**

Find (app.js lines 526-529):

```js
function setLocation(lat, lon, label) {
  setOrigin(lat, lon, { recenter: true });
  $('locationStatus').textContent = label;
}
```

Replace with:

```js
function setLocation(lat, lon, label, { recenter = true } = {}) {
  setOrigin(lat, lon, { recenter });
  $('locationStatus').textContent = label;
}
```

- [ ] **Step 4: Ensure the origin pin shows in exploring even if never set on the map**

Find (app.js lines 146-150):

```js
function enterExploring() {
  setMode('exploring');
  ensureMap();
  if (map) setTimeout(() => map.invalidateSize(), 80);
}
```

Replace with:

```js
function enterExploring() {
  setMode('exploring');
  ensureMap();
  if (originMarker && map && !map.hasLayer(originMarker)) originMarker.addTo(map);
  if (map) setTimeout(() => map.invalidateSize(), 80);
}
```

- [ ] **Step 5: Verify no regression + seam works**

```bash
docker compose up --build -d
```

Navigate to `http://localhost:8000/`. In the browser console run:

```js
window.setLocation(42.50, 18.70, 'console test', { recenter: false });
```

Expected: a "you" pin appears at that point, the map does **not** recenter (view unchanged), and `#lat`/`#lon` (in the hidden wizard) update. Check `browser_console_messages` shows no errors. (The launcher still looks unchanged at this point — that's expected.)

- [ ] **Step 6: Commit**

```bash
git add frontend/app.js
git commit -m "feat(map): lazy origin pin + recenter option + origin-set event"
```

---

## Task 2: mood.js — always-on map tap, `origin-set` listener, location bar, sheet states

**Files:**
- Modify: `frontend/mood.js` — i18n `LX` (lines 19-20, 32-33), location/pick functions (lines 80-107), `buildLauncher` loc-row (lines 163-166), lang-change (line 298), `boot` (lines 301-313)

- [ ] **Step 1: Update i18n — add `loc_title`/`map_hint`, remove picker strings**

Find (mood.js lines 19-20, the EN block):

```js
      use_my_loc: "Use my location · or tap the map to set your start", picked: "Map point", mine: "My location",
      use_loc: "Use my location", choose_map: "Choose on map", pick_msg: "Tap anywhere on the map to set your start", pick_set: "Set this point", pick_cancel: "Cancel",
```

Replace with:

```js
      picked: "Map point", mine: "My location",
      use_loc: "Use my location", loc_title: "Where are you starting from?", map_hint: "Tap the map to set your start",
```

Find (mood.js lines 32-33, the RU block):

```js
      use_my_loc: "Моё место · или тап по карте", picked: "Точка на карте", mine: "Моё место",
      use_loc: "Моё место", choose_map: "Выбрать на карте", pick_msg: "Нажмите в любом месте карты", pick_set: "Выбрать точку", pick_cancel: "Отмена",
```

Replace with:

```js
      picked: "Точка на карте", mine: "Моё место",
      use_loc: "Моё место", loc_title: "Откуда начнём?", map_hint: "Коснитесь карты, чтобы выбрать старт",
```

- [ ] **Step 2: Replace the pick functions with the always-on map handler + sheet helpers**

Find (mood.js lines 80-107): the block beginning `var placeLabel = null;` through the end of `confirmPick()` (the closing `}` before the blank line and `// ---- launcher UI`). It is exactly:

```js
  var placeLabel = null;
  var pickWired = false;
  function locName() { return placeLabel || lx("loc"); }
  function useMyLocation() { if (window.requestGeolocation) window.requestGeolocation(); placeLabel = lx("mine"); updateContext(); }
  function theMap() {
    try { if (window.appMap && window.appMap.on) return window.appMap; } catch (e) {}
    try { if (typeof map !== "undefined" && map && map.on) return map; } catch (e) {}
    return null;
  }
  function onMapPick(e) { if (document.body.classList.contains("picking")) { if (window.setLocation) window.setLocation(e.latlng.lat, e.latlng.lng, lx("picked")); placeLabel = lx("picked"); updateContext(); exitPick(); } }
  function setPickLabels() { var m = $("pickMsg"); if (m) m.textContent = lx("pick_msg"); var s = $("pickSet"); if (s) s.textContent = lx("pick_set"); var c = $("pickCancel"); if (c) c.textContent = lx("pick_cancel"); }
  function enterPick() {
    document.body.classList.add("picking");
    if (typeof window.ensureMap === "function") window.ensureMap();
    setPickLabels(); refreshIcons();
    var tries = 0;
    (function attach() {
      var m = theMap();
      if (m) { try { m.invalidateSize(); } catch (e) {} m.off("click", onMapPick); m.on("click", onMapPick); return; }
      if (tries++ < 25) setTimeout(attach, 100);
    })();
  }
  function exitPick() { document.body.classList.remove("picking"); }
  function confirmPick() {
    var m = theMap();
    if (m && m.getCenter) { var c = m.getCenter(); if (window.setLocation) window.setLocation(c.lat, c.lng, lx("picked")); placeLabel = lx("picked"); updateContext(); }
    exitPick();
  }
```

Replace the entire block with:

```js
  var placeLabel = null;
  var sheetAutoOpened = false;
  function locName() { return placeLabel || lx("loc"); }
  function useMyLocation() { placeLabel = lx("mine"); updateContext(); if (window.requestGeolocation) window.requestGeolocation(); }
  function theMap() {
    try { if (window.appMap && window.appMap.on) return window.appMap; } catch (e) {}
    try { if (typeof map !== "undefined" && map && map.on) return map; } catch (e) {}
    return null;
  }

  // Tap anywhere on the start-screen map to set the start point (no recenter).
  function onPlanningMapClick(e) {
    if (!document.body.classList.contains("planning")) return;
    placeLabel = lx("picked");
    if (window.setLocation) window.setLocation(e.latlng.lat, e.latlng.lng, lx("picked"), { recenter: false });
  }
  function wireMapClick() {
    var tries = 0;
    (function attach() {
      var m = theMap();
      if (m) { m.off("click", onPlanningMapClick); m.on("click", onPlanningMapClick); return; }
      if (tries++ < 25) setTimeout(attach, 100);
    })();
  }

  // Launcher sheet: peek (location bar) <-> open (vibe presets).
  function openLauncher() { var s = $("launchSheet"); if (s) s.classList.add("open"); invalidateSoon(); }
  function toggleLauncher() { var s = $("launchSheet"); if (s) s.classList.toggle("open"); invalidateSoon(); }
  function syncPlanningSheet() {
    var s = $("launchSheet"); if (!s) return;
    if (document.body.classList.contains("loc-set")) s.classList.add("open");
    else s.classList.remove("open");
    invalidateSoon();
  }
  function invalidateSoon() { if (window.appMap) setTimeout(function () { try { window.appMap.invalidateSize(); } catch (e) {} }, 460); }

  // Any location set (tap / GPS / coords) funnels through app.js setOrigin,
  // which dispatches 'origin-set'. React once: mark set, update chip, auto-open.
  document.addEventListener("origin-set", function () {
    if (!document.body.classList.contains("planning")) return;
    document.body.classList.add("loc-set");
    updateContext();
    if (!sheetAutoOpened) { sheetAutoOpened = true; openLauncher(); }
  });
```

- [ ] **Step 3: Add `buildLocBar` and `setMapHint`**

Insert these two functions immediately after the `origin-set` listener you just added (before the `// ---- presets` / `// ---- launcher UI` region — anywhere in the IIFE works; placing them next to the other builders is fine):

```js
  function buildLocBar() {
    var host = $("launchLoc"); if (!host) return;
    host.innerHTML =
      '<p class="loc-title">' + lx("loc_title") + '</p>' +
      '<button type="button" class="loc-cta" id="locGps">' + icon("locate-fixed") + ' ' + lx("use_loc") + '</button>';
    var lg = $("locGps"); if (lg) lg.addEventListener("click", useMyLocation);
    refreshIcons();
  }
  function setMapHint() {
    var h = $("mapHint"); if (!h) return;
    h.innerHTML = icon("locate-fixed") + ' ' + lx("map_hint");
    refreshIcons();
  }
```

- [ ] **Step 4: Remove the old location row from `buildLauncher`**

Find (mood.js lines 163-166):

```js
    html += '<div class="loc-row">';
    html += '  <button type="button" class="loc-btn" id="locGps">' + icon("locate-fixed") + ' ' + lx("use_loc") + '</button>';
    html += '  <button type="button" class="loc-btn" id="locMap">' + icon("map-pin") + ' ' + lx("choose_map") + '</button>';
    html += '</div>';
```

Delete these four lines entirely (the coord-entry `<details>` immediately below stays as the fallback).

Then find the now-dangling wiring inside `buildLauncher` (lines 207-208):

```js
    var lg = $("locGps"); if (lg) lg.addEventListener("click", useMyLocation);
    var lm = $("locMap"); if (lm) lm.addEventListener("click", enterPick);
```

Delete both lines (the GPS button now lives in `buildLocBar`).

- [ ] **Step 5: Update the language-change handler**

Find (mood.js line 298):

```js
    btn.addEventListener("click", function () { setTimeout(function () { buildLauncher(); updateContext(); setPickLabels(); if (document.body.classList.contains("exploring")) buildFilterBar(); }, 0); });
```

Replace with:

```js
    btn.addEventListener("click", function () { setTimeout(function () { buildLocBar(); buildLauncher(); updateContext(); setMapHint(); if (document.body.classList.contains("exploring")) buildFilterBar(); }, 0); });
```

- [ ] **Step 6: Rewrite `boot` (remove pick wiring; add map-click, loc bar, grip→open, planning sync)**

Find (mood.js lines 302-311):

```js
  function boot() {
    if (typeof window.ensureMap === "function") window.ensureMap();
    var pc = $("pickCancel"); if (pc) pc.addEventListener("click", exitPick);
    var ps = $("pickSet"); if (ps) ps.addEventListener("click", confirmPick);
    setPickLabels();
    var grip = document.querySelector(".launch-grip"); if (grip) grip.addEventListener("click", function () { var s = $("launchSheet"); if (s) s.classList.toggle("min"); });
    buildLauncher();
    updateContext();
    refreshIcons();
  }
```

Replace with:

```js
  function boot() {
    if (typeof window.ensureMap === "function") window.ensureMap();
    wireMapClick();
    var grip = document.querySelector(".launch-grip"); if (grip) grip.addEventListener("click", toggleLauncher);
    var eb = $("editBtn"); if (eb) eb.addEventListener("click", function () { setTimeout(syncPlanningSheet, 0); });
    buildLocBar();
    buildLauncher();
    setMapHint();
    updateContext();
    refreshIcons();
  }
```

- [ ] **Step 7: Keep the vibe pill returning to planning consistent**

Find (mood.js line 253, inside `buildFilterBar`):

```js
    $("moodPill").addEventListener("click", function () { if (window.enterPlanning) window.enterPlanning(); });
```

Replace with:

```js
    $("moodPill").addEventListener("click", function () { if (window.enterPlanning) window.enterPlanning(); setTimeout(syncPlanningSheet, 0); });
```

- [ ] **Step 8: Verify the JS loads and tap works (markup/CSS land in Tasks 3-4)**

```bash
docker compose up --build -d
```

Navigate to `http://localhost:8000/`, check `browser_console_messages` for no errors (`enterPick`/`setPickLabels` are gone; nothing should reference them). Tap the visible top map area: a pin should drop and the topbar location chip should change to "Map point". (The sheet may still look full-height until Task 4 — that's expected.)

- [ ] **Step 9: Commit**

```bash
git add frontend/mood.js
git commit -m "feat(launcher): tap-map to set location + origin-set wiring; drop full-screen picker"
```

---

## Task 3: index.html — add `#launchLoc` + `#mapHint`, remove `#pickUi`

**Files:**
- Modify: `frontend/index.html` — map region (lines 16-17), launch sheet (lines 156-159), pick block (lines 161-167)

- [ ] **Step 1: Add the on-map hint pill**

Find (index.html lines 16-17):

```html
  <div id="map"></div>
  <div class="map-scrim" aria-hidden="true"></div>
```

Replace with:

```html
  <div id="map"></div>
  <div class="map-scrim" aria-hidden="true"></div>
  <div class="map-hint" id="mapHint" aria-hidden="true"></div>
```

- [ ] **Step 2: Add the location bar container to the launch sheet**

Find (index.html lines 156-159):

```html
  <section class="launch-sheet" id="launchSheet">
    <div class="launch-grip"><span></span></div>
    <div class="launch-body" id="launchBody"></div>
  </section>
```

Replace with:

```html
  <section class="launch-sheet" id="launchSheet">
    <div class="launch-grip"><span></span></div>
    <div class="launch-loc" id="launchLoc"></div>
    <div class="launch-body" id="launchBody"></div>
  </section>
```

- [ ] **Step 3: Remove the full-screen picker block**

Find (index.html lines 161-167):

```html
  <!-- map-pick mode: full-map center-pin location picker -->
  <div class="pick-ui" id="pickUi">
    <div class="pick-bar top"><i data-lucide="locate-fixed"></i> <span id="pickMsg">Tap anywhere on the map to set your start</span></div>
    <div class="pick-bar bottom">
      <button type="button" class="btn btn-back" id="pickCancel">Cancel</button>
    </div>
  </div>
```

Delete this entire block.

- [ ] **Step 4: Verify markup**

```bash
docker compose up --build -d
```

Navigate to `http://localhost:8000/`. Confirm: the on-map hint pill ("Tap the map to set your start") is visible near the top of the map; no console errors; `#pickUi` no longer in the DOM (check via `browser_snapshot` or console `document.getElementById('pickUi')` → `null`).

- [ ] **Step 5: Commit**

```bash
git add frontend/index.html
git commit -m "feat(ui): add map hint + location-bar slot; remove pick-ui markup"
```

---

## Task 4: mood.css — peek/open sheet, location-bar + map-hint styles, remove pick CSS

**Files:**
- Modify: `frontend/mood.css` — pick CSS (lines 51-66), launch sheet (lines 69-81), and the location/context area (around lines 27-37)

- [ ] **Step 1: Remove the full-screen picker CSS**

Find (mood.css lines 51-66):

```css
/* map-pick mode */
.pick-ui { display: none; }
body.picking .launch-sheet, body.picking .ctx-wrap { display: none; }
body.picking .pick-ui { display: block; }
.pick-ui { position: fixed; inset: 0; z-index: 18; pointer-events: none; }
.pick-bar { position: absolute; left: 50%; transform: translateX(-50%); pointer-events: auto; }
.pick-bar.top { top: calc(var(--topbar-h) + 6px); display: inline-flex; align-items: center; gap: 8px; max-width: 92vw; white-space: nowrap;
  background: var(--cream-hi); color: var(--pine-800); padding: 10px 16px; border-radius: 999px; box-shadow: var(--float-sm);
  font-family: var(--display); font-weight: 700; font-size: 13px; }
.pick-bar.top .lucide { width: 16px; height: 16px; color: var(--clay-500); }
.pick-bar.bottom { bottom: 28px; display: flex; gap: 12px; }
.pick-bar.bottom .btn { box-shadow: var(--float-sm); min-width: 130px; white-space: nowrap; }
.pick-pin { position: fixed; left: 50%; top: 50%; transform: translate(-50%, -100%); z-index: 19; pointer-events: none;
  filter: drop-shadow(0 7px 6px rgba(17,44,32,0.35)); }
.pick-pin .lucide { width: 46px; height: 46px; stroke-width: 2; fill: var(--clay-500); color: #fff; }
.pick-pin::after { content: ""; position: absolute; left: 50%; bottom: -3px; transform: translateX(-50%); width: 7px; height: 7px; border-radius: 50%; background: var(--clay-500); box-shadow: 0 0 0 3px rgba(255,255,255,0.6); }
```

Replace the whole block with the on-map hint pill:

```css
/* on-map hint: shown until a location is set (planning only) */
.map-hint { display: none; }
body.planning:not(.loc-set) .map-hint {
  position: fixed; left: 50%; top: calc(var(--topbar-h) + 14px); transform: translateX(-50%);
  z-index: 6; pointer-events: none; display: inline-flex; align-items: center; gap: 8px;
  max-width: 90vw; white-space: nowrap; background: var(--cream-hi); color: var(--pine-800);
  padding: 10px 16px; border-radius: 999px; box-shadow: var(--float-sm);
  font-family: var(--display); font-weight: 700; font-size: 13px;
}
.map-hint .lucide { width: 16px; height: 16px; color: var(--clay-500); }
```

- [ ] **Step 2: Make the sheet peek by default, open on demand, with a slide transition**

Find (mood.css lines 69-78):

```css
.launch-sheet {
  position: fixed; z-index: 16; bottom: 0; left: 50%; transform: translateX(-50%);
  width: min(100%, 720px); height: min(64vh, 560px);
  background: var(--cream); border-radius: 26px 26px 0 0;
  box-shadow: 0 -18px 44px -20px rgba(17,44,32,0.55);
  display: flex; flex-direction: column; overflow: hidden;
}
body.exploring .launch-sheet { display: none; }
.launch-grip { display: grid; place-items: center; padding: 11px 0 4px; flex: 0 0 auto; cursor: pointer; }
.launch-sheet.min { height: 60px; }
```

Replace with:

```css
.launch-sheet {
  position: fixed; z-index: 16; bottom: 0; left: 50%; transform: translateX(-50%);
  width: min(100%, 720px); height: 150px;
  background: var(--cream); border-radius: 26px 26px 0 0;
  box-shadow: 0 -18px 44px -20px rgba(17,44,32,0.55);
  display: flex; flex-direction: column; overflow: hidden;
  transition: height 0.42s var(--spring);
}
.launch-sheet.open { height: min(64vh, 560px); }
body.exploring .launch-sheet { display: none; }
.launch-grip { display: grid; place-items: center; padding: 11px 0 4px; flex: 0 0 auto; cursor: pointer; }

/* peek shows only the location bar; open shows only the vibe launcher */
.launch-loc { flex: 0 0 auto; width: 100%; max-width: 560px; margin: 0 auto; padding: 6px clamp(16px,4vw,22px) 16px; }
.launch-sheet.open .launch-loc { display: none; }
.launch-sheet:not(.open) .launch-body { display: none; }
.loc-title { font-family: var(--display); font-weight: 800; font-size: 16px; letter-spacing: -0.01em; color: var(--ink-900); margin: 0 0 10px; }
.loc-cta { display: flex; align-items: center; justify-content: center; gap: 8px; width: 100%; padding: 13px; border-radius: 999px;
  background: var(--pine-800); color: var(--cream); border: none; font-family: var(--display); font-weight: 700; font-size: 14px; cursor: pointer; transition: 0.18s var(--ease); }
.loc-cta:hover { background: var(--pine-900); }
.loc-cta .lucide { width: 17px; height: 17px; }
```

- [ ] **Step 3: Verify the full flow visually**

```bash
docker compose up --build -d
```

Navigate to `http://localhost:8000/` and confirm with screenshots:
1. Map is dominant; slim peek sheet shows "Where are you starting from?" + "Use my location"; on-map hint pill visible.
2. **Tap the map** → pin drops, hint pill disappears, sheet **slides up** to the vibe presets, topbar chip shows "Map point".
3. Tap the grip → sheet collapses back to peek (map dominant) without re-forcing open on the next pin move.

- [ ] **Step 4: Commit**

```bash
git add frontend/mood.css
git commit -m "feat(launcher): map-forward peek sheet + slide-up; drop pick-mode styles"
```

---

## Task 5: Full verification pass (Playwright) + cleanup check

**Files:** none (verification only)

- [ ] **Step 1: Rebuild and drive the happy paths**

```bash
docker compose up --build -d
```

Using Playwright against `http://localhost:8000/`, verify and screenshot each:

1. **Initial:** map dominant, peek sheet, on-map hint visible, no pin, no console errors.
2. **Tap to set:** click a point on the visible map → pin appears at that point, view does **not** jump, hint pill gone, sheet slides up to vibes, chip = "Map point".
3. **Use my location:** reload, click "Use my location", accept the permission → pin set, sheet slides up, chip = "My location". (On `localhost` geolocation is allowed.)
4. **Coordinates fallback:** reload, open the sheet (grip), expand "Enter coordinates", set lat/lon, Set → pin set, chip updates.
5. **Grip toggle:** collapse open→peek and back; confirm the map resizes (no grey tiles — `invalidateSize` ran).
6. **Pick a vibe:** with the sheet open, click a preset → loading → results (exploring), origin pin visible among result pins. Click the mood pill → returns to planning; because a location is set, the sheet opens straight to vibes.
7. **Language:** toggle RU then EN → loc bar title, GPS button, and map hint all re-render translated; no leftover English picker text.

- [ ] **Step 2: Confirm the old picker is fully gone**

In the browser console:

```js
[document.getElementById('pickUi'),
 document.getElementById('pickMsg'),
 document.getElementById('pickCancel')].map(Boolean)
```

Expected: `[false, false, false]`. Also `grep -rnE "pick_msg|pick_set|pick_cancel|choose_map|enterPick|onMapPick|\.min\b" frontend/mood.js frontend/mood.css frontend/index.html` returns no matches.

- [ ] **Step 3: Tear down**

```bash
docker compose down
```

- [ ] **Step 4 (optional): squash/finish branch**

This is a design-exploration branch (`design/guided-explorer`). Use the superpowers:finishing-a-development-branch skill to decide on merge/PR. Do not merge without asking the user.

---

## Self-Review notes (author check)

- **Spec coverage:** map-dominant peek (Task 4 §2) ✓; tap sets location no-recenter (Task 1 §3, Task 2 §2) ✓; GPS still works (untouched `useMyLocation`/`requestGeolocation`) ✓; auto slide-up on first set, one-shot (`sheetAutoOpened`, Task 2 §2) ✓; pin hidden until set (Task 1 §1-2) ✓; on-map hint until set (`body.loc-set`, Task 2 §3 + Task 4 §1) ✓; remove "Choose on map" + `#pickUi` + `body.picking` + picker i18n (Tasks 2-4) ✓; coord entry stays in open launcher (Task 2 §4 keeps the `<details>`) ✓; `setLocation({recenter})` seam (Task 1 §3) ✓; EN+RU strings (Task 2 §1) ✓.
- **Naming consistency:** `onPlanningMapClick`, `wireMapClick`, `buildLocBar`, `setMapHint`, `openLauncher`, `toggleLauncher`, `syncPlanningSheet`, `invalidateSoon`, `sheetAutoOpened`, event name `origin-set`, body class `loc-set`, ids `launchLoc`/`mapHint`/`locGps` — used identically across JS/HTML/CSS tasks.
- **No placeholders:** every edit shows exact before/after code.
- **Ordering safety:** Task 1 keeps the old 3-arg `setLocation` callers working (4th arg optional). Task 2's builders no-op when `#launchLoc`/`#mapHint` are absent, so the app stays loadable before Task 3 adds them.