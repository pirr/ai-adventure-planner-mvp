# Staged Filters, Transport Facet & Recent Choices — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** In the results filter bar, add a Transport facet, make filter changes stage until an explicit Apply (with Reset), and cache the last 3 applied searches as re-selectable Recent chips.

**Architecture:** All changes are in `mood.js` (the launcher/filter logic), with markup slots in `index.html` and styles in `mood.css`. The hidden wizard chips remain the single source of truth for the search payload; the new logic decides *when* to commit a search (vibe pick, Apply, or recent+Apply) rather than searching on every tap. An `appliedSnapshot` of the last searched facets drives "pending" state; recents persist in `localStorage`.

**Tech Stack:** Vanilla JS (classic script IIFE), plain CSS, Leaflet (untouched here). Served by FastAPI static hosting via `docker compose`. No JS test framework, so each task is verified by loading the running app and observing behavior + network (Playwright).

---

## File Structure

| File | Responsibility | Change |
|------|----------------|--------|
| `frontend/mood.js` | Filter bar, facet panels, vibe presets | Transport facet; state read/write/snapshot; stage-not-search; Apply/Reset; recents store + render; i18n |
| `frontend/index.html` | Results sheet markup | Add `#recentRow` + `#applyBar` after `#facetPanel` |
| `frontend/mood.css` | Filter styling | `.apply-bar`, `.fchip.changed`, `.recent-row`/`.recent-chip` |

**Conventions:** `mood.js` is an IIFE using `var`/`function`, a `$` = `getElementById` helper, `icon(name)`, `lx(key)` (launcher i18n), `pt(preset)` (localized preset label), `refreshIcons()`. CSS uses `:root` tokens (`--pine-800`, `--cream`, `--clay-400/500`, `--amber-400`, `--sand-200`, `--r-lg`, `--float-sm`, `--ease`, `--muted`, `--display`, `--line`, `--pine-700`).

---

## How to run / verify (every task)

```bash
docker compose up --build -d        # frontend is baked in; rebuild to pick up changes
# Playwright: navigate to the LAN address the browser can reach (localhost may be
# unreachable from the Playwright container). Find it with:
#   ip route get 1.1.1.1   → use that src IP, e.g. http://192.168.10.99:8000/
docker compose down                 # when finished
```

To reach results in the app: set a location (tap map / Use my location), open the launcher, click a vibe preset → the search runs and the results sheet (with the filter bar) appears.

---

## Task 1: index.html — add Recent + Apply slots

**Files:**
- Modify: `frontend/index.html` — results sheet, after `#facetPanel` (line ~178)

- [ ] **Step 1: Add the two containers**

Find:

```html
    <div class="filterbar" id="filterbar"></div>
    <div class="facet-panel hidden" id="facetPanel"></div>
    <div class="sheet-body">
```

Replace with:

```html
    <div class="filterbar" id="filterbar"></div>
    <div class="facet-panel hidden" id="facetPanel"></div>
    <div class="recent-row hidden" id="recentRow"></div>
    <div class="apply-bar hidden" id="applyBar"></div>
    <div class="sheet-body">
```

- [ ] **Step 2: Commit**

```bash
git add frontend/index.html
git commit -m "feat(filters): add recent-row and apply-bar slots to results sheet"
```

---

## Task 2: mood.js — Transport facet + i18n strings

**Files:**
- Modify: `frontend/mood.js` — `LX` EN block (line ~18-19), `LX` RU block (line ~31-32)

> NOTE: the RU values in this file are stored as literal `\u` escapes (e.g.
> `"Время"`), and the EN apostrophe is `’`. To avoid
> depending on those bytes, both edits **anchor on the plain-ASCII `your_vibe` line and
> insert a new line after it** rather than rewriting the escaped lines. The new RU
> values are written as raw Cyrillic, which the UTF-8 file accepts (consistent with the
> map-feature strings already in this file).

- [ ] **Step 1: Add EN strings (insert after the EN `your_vibe` line)**

Find:

```js
      your_vibe: "Popular", time: "Time", interest: "Interest", crew: "Crew", effort: "Effort",
```

Replace with:

```js
      your_vibe: "Popular", time: "Time", interest: "Interest", crew: "Crew", effort: "Effort",
      transport: "Transport", f_transport: "How are you getting there?", recent: "Recent", apply: "Apply", reset: "Reset", changes: "changed",
```

- [ ] **Step 2: Add RU strings (insert after the RU `your_vibe` line)**

Find (the RU values are literal `\u` escapes in the source — match them exactly):

```js
      your_vibe: "Популярно", time: "Время", interest: "Интерес", crew: "Компания", effort: "Нагрузка",
```

Replace with (original escapes preserved; new values as raw Cyrillic, which the file accepts):

```js
      your_vibe: "Популярно", time: "Время", interest: "Интерес", crew: "Компания", effort: "Нагрузка",
      transport: "Транспорт", f_transport: "Как добираетесь?", recent: "Недавнее", apply: "Применить", reset: "Сбросить", changes: "изм.",
```

- [ ] **Step 3: Commit**

```bash
git add frontend/mood.js
git commit -m "feat(filters): i18n for transport, recent, apply, reset"
```

---

## Task 3: mood.js — staged-apply + recents (replace the filter section)

**Files:**
- Modify: `frontend/mood.js` — `choosePreset` (line ~135-140); the whole filter section (`// ---- results filter chips ...` through `setInterestToggle`); `boot`'s `editBtn` handler

- [ ] **Step 1: Defer the search in `choosePreset`**

Find:

```js
  function choosePreset(p) {
    currentMood = p;
    applyPreset(p);
    if (typeof window.runSearch === "function") window.runSearch();
    buildFilterBar();
  }
```

Replace with:

```js
  function choosePreset(p) {
    currentMood = p;
    applyPreset(p);
    commitSearch();
    buildFilterBar();
  }
```

- [ ] **Step 2: Replace the entire filter section**

Find the block that starts with:

```js
  // ---- results filter chips (read/write the same hidden chips) -----------
  var FACETS = [
    { key: "time", cont: "timeChips", attr: "minutes", multi: false, label: "time", title: "f_time" },
    { key: "interest", cont: "interestChips", attr: "interest", multi: true, label: "interest", title: "f_interest" },
    { key: "crew", cont: "groupChips", attr: "group", multi: false, label: "crew", title: "f_crew" },
    { key: "effort", cont: "intensityChips", attr: "intensity", multi: false, label: "effort", title: "f_effort" },
  ];
```

…and ends with the `setInterestToggle` function:

```js
  function setInterestToggle(f, val) {
    var c = $(f.cont); if (!c) return;
    c.querySelectorAll(".tile").forEach(function (tile) {
      if (String(tile.dataset[f.attr]) === String(val)) tile.classList.toggle("is-active");
    });
  }
```

Replace that whole block (everything from `// ---- results filter chips` through the closing `}` of `setInterestToggle`) with:

```js
  // ---- results filters: read/write hidden chips, stage edits, apply once ----
  var FACETS = [
    { key: "time", cont: "timeChips", attr: "minutes", multi: false, label: "time", title: "f_time", field: "minutes" },
    { key: "transport", cont: "transportChips", attr: "transport", multi: false, label: "transport", title: "f_transport", field: "transport" },
    { key: "crew", cont: "groupChips", attr: "group", multi: false, label: "crew", title: "f_crew", field: "group" },
    { key: "interest", cont: "interestChips", attr: "interest", multi: true, label: "interest", title: "f_interest", field: "interests" },
    { key: "effort", cont: "intensityChips", attr: "intensity", multi: false, label: "effort", title: "f_effort", field: "intensity" },
  ];
  function tileText(tile) { var s = tile.querySelector(".tile-text"); return s ? s.textContent.trim() : tile.textContent.trim(); }
  function tileIcon(tile) { var i = tile.querySelector("[data-lucide]"); return i ? i.getAttribute("data-lucide") : "circle"; }
  function facetValue(f) {
    var c = $(f.cont); if (!c) return "";
    if (f.multi) {
      var on = Array.prototype.slice.call(c.querySelectorAll(".tile.is-active"));
      if (!on.length) return "—";
      return on.length > 1 ? tileText(on[0]) + " +" + (on.length - 1) : tileText(on[0]);
    }
    var a = c.querySelector(".tile.is-active");
    return a ? tileText(a) : "—";
  }

  // --- state: read/write the hidden chips; vibe label is cosmetic ---
  function activeVal(cont, attr) { var c = $(cont); if (!c) return null; var a = c.querySelector(".tile.is-active"); return a ? a.dataset[attr] : null; }
  function activeVals(cont, attr) { var c = $(cont); if (!c) return []; return Array.prototype.slice.call(c.querySelectorAll(".tile.is-active")).map(function (t) { return t.dataset[attr]; }); }
  function readFacets() {
    return {
      minutes: activeVal("timeChips", "minutes"),
      transport: activeVal("transportChips", "transport"),
      group: activeVal("groupChips", "group"),
      intensity: activeVal("intensityChips", "intensity"),
      interests: activeVals("interestChips", "interest").slice().sort(),
    };
  }
  function readState() { var f = readFacets(); f.vibeKey = currentMood ? currentMood.key : null; return f; }
  function presetByKey(k) { return PRESETS.filter(function (p) { return p.key === k; })[0] || null; }
  function writeState(s) {
    setSingle("timeChips", "minutes", s.minutes);
    setSingle("transportChips", "transport", s.transport);
    setSingle("groupChips", "group", s.group);
    setSingle("intensityChips", "intensity", s.intensity);
    setInterests((s.interests || []).slice());
    currentMood = s.vibeKey ? presetByKey(s.vibeKey) : null;
  }
  function interestsEqual(a, b) { return a.length === b.length && a.every(function (x, i) { return x === b[i]; }); }
  function facetsEqual(a, b) {
    return a.minutes === b.minutes && a.transport === b.transport && a.group === b.group && a.intensity === b.intensity && interestsEqual(a.interests, b.interests);
  }
  var appliedSnapshot = null; // full state (incl. vibeKey) of the last searched filters
  function isPending() { return !!appliedSnapshot && !facetsEqual(readFacets(), appliedSnapshot); }
  function changeCount() {
    if (!appliedSnapshot) return 0;
    var f = readFacets(), n = 0;
    ["minutes", "transport", "group", "intensity"].forEach(function (k) { if (f[k] !== appliedSnapshot[k]) n++; });
    if (!interestsEqual(f.interests, appliedSnapshot.interests)) n++;
    return n;
  }
  function facetChanged(f) {
    if (!appliedSnapshot) return false;
    var cur = readFacets();
    if (f.multi) return !interestsEqual(cur.interests, appliedSnapshot.interests);
    return cur[f.field] !== appliedSnapshot[f.field];
  }

  // --- commit a search (the only place runSearch fires from the filters) ---
  function commitSearch() {
    if (typeof window.runSearch === "function") window.runSearch();
    var s = readState();
    saveRecent(s);
    appliedSnapshot = s;
  }
  function applyStaged() { commitSearch(); buildFilterBar(); }
  function resetStaged() { if (appliedSnapshot) writeState(appliedSnapshot); buildFilterBar(); }

  // --- recent choices (localStorage cache of the last 3) ---
  var RECENT_KEY = "ap.recentChoices";
  function loadRecents() { try { return JSON.parse(localStorage.getItem(RECENT_KEY)) || []; } catch (e) { return []; } }
  function persistRecents(list) { try { localStorage.setItem(RECENT_KEY, JSON.stringify(list)); } catch (e) {} }
  function sameChoice(a, b) {
    return a.vibeKey === b.vibeKey && a.minutes === b.minutes && a.transport === b.transport && a.group === b.group && a.intensity === b.intensity &&
      (a.interests || []).join(",") === (b.interests || []).join(",");
  }
  function saveRecent(state) {
    var list = loadRecents().filter(function (x) { return !sameChoice(x, state); });
    list.unshift(state);
    persistRecents(list.slice(0, 3));
  }
  function valueLabel(cont, attr, val) {
    var c = $(cont); if (!c || val == null) return val || "";
    var t = c.querySelector('.tile[data-' + attr + '="' + val + '"]');
    return t ? tileText(t) : String(val);
  }
  function recentLabel(s) {
    if (s.vibeKey) { var p = presetByKey(s.vibeKey); if (p) return icon(p.icon) + " " + pt(p).t; }
    return valueLabel("timeChips", "minutes", s.minutes) + " · " + valueLabel("transportChips", "transport", s.transport) + " · " + valueLabel("groupChips", "group", s.group);
  }
  function renderRecents() {
    var row = $("recentRow"); if (!row) return;
    var rec = loadRecents();
    if (!rec.length || openFacetKey) { row.className = "recent-row hidden"; row.innerHTML = ""; return; }
    var html = '<span class="recent-label">' + lx("recent") + "</span>";
    rec.forEach(function (s, i) { html += '<button type="button" class="recent-chip" data-i="' + i + '">' + recentLabel(s) + "</button>"; });
    row.className = "recent-row";
    row.innerHTML = html;
    row.querySelectorAll(".recent-chip").forEach(function (el) {
      el.addEventListener("click", function () { writeState(rec[Number(el.dataset.i)]); buildFilterBar(); });
    });
    refreshIcons();
  }

  // --- apply / reset bar (shown only when there are staged changes) ---
  function renderApplyBar() {
    var bar = $("applyBar"); if (!bar) return;
    if (!isPending()) { bar.className = "apply-bar hidden"; bar.innerHTML = ""; return; }
    bar.className = "apply-bar";
    bar.innerHTML = '<span class="apply-count">' + changeCount() + " " + lx("changes") + "</span>" +
      '<span class="apply-actions"><button type="button" class="apply-reset" id="filterReset">' + lx("reset") + "</button>" +
      '<button type="button" class="apply-go" id="filterApply">' + lx("apply") + "</button></span>";
    $("filterReset").addEventListener("click", resetStaged);
    $("filterApply").addEventListener("click", applyStaged);
  }

  // --- filter bar ---
  var openFacetKey = null;
  function buildFilterBar() {
    var bar = $("filterbar"); if (!bar) return;
    var html = '<button type="button" class="mood-pill" id="moodPill">' + icon(currentMood ? currentMood.icon : "dices") + " " +
      (currentMood ? pt(currentMood).t : lx("your_vibe")) + " " + icon("chevron-down") + "</button>";
    FACETS.forEach(function (f) {
      html += '<button type="button" class="fchip' + (facetChanged(f) ? " changed" : "") + '" data-facet="' + f.key + '"><span class="fk">' + lx(f.label) + "</span><b>" + facetValue(f) + "</b>" + icon("chevron-down") + "</button>";
    });
    bar.innerHTML = html;
    $("moodPill").addEventListener("click", function () { resetStaged(); if (window.enterPlanning) window.enterPlanning(); setTimeout(syncPlanningSheet, 0); });
    bar.querySelectorAll("[data-facet]").forEach(function (el) {
      el.addEventListener("click", function () { toggleFacet(el.dataset.facet); });
    });
    renderFacetPanel();
    renderRecents();
    renderApplyBar();
    refreshIcons();
  }
  function toggleFacet(key) { openFacetKey = (openFacetKey === key ? null : key); if (window.openSheet) window.openSheet(); renderFacetPanel(); renderRecents(); syncChipActive(); }
  function syncChipActive() {
    var bar = $("filterbar"); if (!bar) return;
    bar.querySelectorAll(".fchip").forEach(function (el) { el.classList.toggle("active", el.dataset.facet === openFacetKey); });
  }
  function renderFacetPanel() {
    var panel = $("facetPanel"); if (!panel) return;
    if (!openFacetKey) { panel.className = "facet-panel hidden"; panel.innerHTML = ""; return; }
    var f = FACETS.filter(function (x) { return x.key === openFacetKey; })[0];
    var c = $(f.cont); if (!c) return;
    var html = "<h4>" + lx(f.title) + '</h4><div class="facet-opts">';
    c.querySelectorAll(".tile").forEach(function (tile) {
      var on = tile.classList.contains("is-active");
      html += '<button type="button" class="facet-pill ' + (on ? "on" : "") + '" data-val="' + (tile.dataset[f.attr] || "") + '">' + icon(tileIcon(tile)) + " " + tileText(tile) + "</button>";
    });
    html += "</div>";
    panel.className = "facet-panel";
    panel.innerHTML = html;
    panel.querySelectorAll(".facet-pill").forEach(function (pill) {
      pill.addEventListener("click", function () {
        if (f.multi) { setInterestToggle(f, pill.dataset.val); }
        else { setSingle(f.cont, f.attr, pill.dataset.val); openFacetKey = null; }
        currentMood = null;       // customizing clears the vibe label
        buildFilterBar();         // stage only — NO runSearch
      });
    });
    refreshIcons();
  }
  function setInterestToggle(f, val) {
    var c = $(f.cont); if (!c) return;
    c.querySelectorAll(".tile").forEach(function (tile) {
      if (String(tile.dataset[f.attr]) === String(val)) tile.classList.toggle("is-active");
    });
  }
```

- [ ] **Step 3: Reset staged edits when leaving results via Edit trip**

Find (in `boot`):

```js
    var eb = $("editBtn"); if (eb) eb.addEventListener("click", function () { setTimeout(syncPlanningSheet, 0); });
```

Replace with:

```js
    var eb = $("editBtn"); if (eb) eb.addEventListener("click", function () { resetStaged(); setTimeout(syncPlanningSheet, 0); });
```

- [ ] **Step 4: Verify it loads and stages without searching**

```bash
docker compose up --build -d
```

Navigate to the app (LAN address), set a location, pick a vibe → results appear. Then:
- Confirm a **Transport** chip is in the bar (defaults to "Car"), and its panel lists Walk/Car/Bike.
- Open Time, pick a different value; open Transport, pick a different value. Check `browser_console_messages` (no errors) and `browser_network_requests`: **no** new `POST /api/recommendations` should have fired. The apply bar should read "2 changed", and both chips should have the `changed` class (verify via `browser_evaluate`).

- [ ] **Step 5: Commit**

```bash
git add frontend/mood.js
git commit -m "feat(filters): transport facet, staged Apply/Reset, recent choices"
```

---

## Task 4: mood.css — apply bar, changed chip, recent row

**Files:**
- Modify: `frontend/mood.css` — append after the existing facet styles (after the `.facet-pill.on .lucide` rule, line ~162)

- [ ] **Step 1: Add the styles**

Find:

```css
.facet-pill.on { background: var(--pine-800); border-color: var(--pine-800); color: var(--cream); }
.facet-pill.on .lucide { color: var(--amber-400); }
```

Replace with:

```css
.facet-pill.on { background: var(--pine-800); border-color: var(--pine-800); color: var(--cream); }
.facet-pill.on .lucide { color: var(--amber-400); }

/* staged-change highlight on a filter chip */
.fchip.changed { border-color: var(--clay-400); background: #fff6ee; }
.fchip.changed b { color: var(--clay-500); }

/* recent choices row */
.recent-row { display: flex; align-items: center; gap: 8px; overflow-x: auto; padding: 0 clamp(16px,3vw,24px) 12px; scrollbar-width: none; max-width: 560px; margin: 0 auto; }
.recent-row::-webkit-scrollbar { height: 0; }
.recent-row.hidden { display: none; }
.recent-label { flex: 0 0 auto; font-family: var(--display); font-weight: 700; font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--muted); }
.recent-chip { flex: 0 0 auto; white-space: nowrap; display: inline-flex; align-items: center; gap: 6px; background: var(--sand-200); border: 1.5px solid transparent; color: var(--pine-800);
  padding: 7px 12px; border-radius: 999px; font-family: var(--display); font-weight: 700; font-size: 12.5px; cursor: pointer; transition: 0.18s var(--ease); }
.recent-chip:hover { border-color: var(--pine-700); }
.recent-chip .lucide { width: 14px; height: 14px; color: var(--clay-500); }

/* apply / reset bar */
.apply-bar { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin: 0 clamp(16px,3vw,24px) 12px;
  background: var(--pine-800); color: var(--cream); border-radius: var(--r-lg); padding: 10px 12px 10px 16px; box-shadow: var(--float-sm); }
.apply-bar.hidden { display: none; }
.apply-count { font-family: var(--display); font-weight: 700; font-size: 13px; }
.apply-actions { display: inline-flex; align-items: center; gap: 8px; }
.apply-reset { background: transparent; border: none; color: var(--sand-200); font-family: var(--display); font-weight: 700; font-size: 13px; padding: 8px 12px; cursor: pointer; }
.apply-reset:hover { color: #fff; }
.apply-go { background: var(--amber-400); color: var(--pine-900); border: none; border-radius: 999px; font-family: var(--display); font-weight: 800; font-size: 13px; padding: 9px 18px; cursor: pointer; transition: 0.18s var(--ease); }
.apply-go:hover { filter: brightness(1.05); }
```

- [ ] **Step 2: Verify visually**

```bash
docker compose up --build -d
```

Screenshot the results view while staged: the apply bar (pine, with amber Apply + ghost Reset) sits under the chips; changed chips show the warm `changed` highlight; after Apply, a Recent chip row appears under the filters.

- [ ] **Step 3: Commit**

```bash
git add frontend/mood.css
git commit -m "feat(filters): styles for apply bar, changed chip, recent row"
```

---

## Task 5: Full verification pass (Playwright)

**Files:** none (verification only)

- [ ] **Step 1: Rebuild and drive the flows**

```bash
docker compose up --build -d
```

Against the app (LAN address), verify + screenshot:

1. **Transport facet:** present in bar, defaults to "Car"; panel lists Walk/Car/Bike; selecting Walk stages it (chip shows "Walk", `changed`), no search yet.
2. **Stage several + Apply:** change Time and Transport; `browser_network_requests` shows **zero** new `POST /api/recommendations` while staging; apply bar shows "2 changed"; clicking **Apply** fires **exactly one** `POST /api/recommendations`; results update; bar disappears; chips lose `changed`.
3. **Reset:** stage a change, click **Reset** → chips revert to the applied values, bar hides, no search fired.
4. **Recents:** after an Apply (or vibe pick), a Recent chip appears; tapping it stages vibe+filters (apply bar reappears); Apply re-runs the search. Apply two more distinct choices → at most 3 recent chips; re-applying an existing choice does not duplicate it.
5. **Persistence:** reload the page (`browser_navigate` again), reach results → the Recent chips are still there (from localStorage).
6. **Leave results:** stage a change, click the vibe pill (→ planning) without Apply; return to results → no pending bar (silently reset).
7. **i18n:** toggle RU → Transport chip/panel title, Recent, Apply, Reset all translate; toggle back EN.

- [ ] **Step 2: Source sanity check**

```bash
grep -nE "runSearch\(\)" frontend/mood.js
```

Expected: `runSearch()` is called from exactly one place in the filter logic — inside `commitSearch`. (Other matches, if any, are outside the filter section.)

- [ ] **Step 3: Tear down**

```bash
docker compose down
```

---

## Self-Review notes (author check)

- **Spec coverage:** transport facet (Task 2 i18n + Task 3 FACETS) ✓; default Car (uses existing pre-selected tile) ✓; stage-not-search (Task 3 `renderFacetPanel` → `buildFilterBar`, no `runSearch`) ✓; Apply/Reset bar + facet-only pending/count (`isPending`/`changeCount`, Task 3 + Task 4 styles) ✓; `.changed` highlight (`facetChanged` + CSS) ✓; recents capture vibe+facets, dedupe, cap 3, localStorage (`saveRecent`/`loadRecents`) ✓; recent select → stage (`writeState` + `buildFilterBar`) ✓; recents hidden while a panel is open (`renderRecents` checks `openFacetKey`) ✓; leave-results reset (mood pill + editBtn → `resetStaged`) ✓; commit on vibe pick + Apply (`choosePreset`/`applyStaged` → `commitSearch` → `saveRecent`) ✓; EN/RU strings ✓.
- **Naming consistency:** `readFacets`/`readState`/`writeState`/`facetsEqual`/`interestsEqual`/`isPending`/`changeCount`/`facetChanged`/`appliedSnapshot`/`commitSearch`/`applyStaged`/`resetStaged`/`saveRecent`/`loadRecents`/`persistRecents`/`sameChoice`/`recentLabel`/`valueLabel`/`renderRecents`/`renderApplyBar`; ids `recentRow`/`applyBar`/`filterReset`/`filterApply`; classes `recent-row`/`recent-chip`/`recent-label`/`apply-bar`/`apply-count`/`apply-actions`/`apply-reset`/`apply-go`/`fchip.changed` — consistent across JS/HTML/CSS.
- **No placeholders:** every step has exact before/after code.
- **Ordering safety:** Task 1 adds inert slots; Task 2 adds unused strings; Task 3's functions are all defined within the same IIFE (function declarations hoist, so `choosePreset`→`commitSearch` and `buildFilterBar`→`renderRecents`/`renderApplyBar` resolve regardless of order). `valueLabel`/`renderRecents` no-op until Task 1's slots exist, but Task 1 precedes Task 3.
