# Results filters: Transport facet, staged Apply, and recent choices

**Date:** 2026-06-08
**Branch:** `design/guided-explorer`
**Status:** Design approved — ready for implementation plan

## Summary

Three related improvements to the **results/exploring** filter bar in `mood.js`:

1. **Transport filter** — expose the existing walk/car/bike selection as a filter facet.
2. **Staged Apply** — stop searching on every option tap; let the user change several
   filters, then run **one** search via an explicit **Apply** (with **Reset**).
3. **Recent choices** — cache the last 3 applied searches (vibe + filters) in
   localStorage and let the user re-select one (which stages it for Apply).

All three live on the existing results filter surface. The start screen (map →
location → vibe) is unchanged.

## Current state (for context)

The mood launcher (`mood.js`) drives the visible UI; the original wizard chips/inputs
stay hidden in the DOM and are the single source of truth for the search payload.

Results-view filtering today:

- `FACETS = [time, interest, crew, effort]` — each `{ key, cont, attr, multi, label,
  title }` pointing at a hidden chip container (`timeChips`, `interestChips`,
  `groupChips`, `intensityChips`). **Transport is absent**, though `#transportChips`
  (walk/car/bike, default car, `data-transport`) exists in the hidden wizard.
- `buildFilterBar()` renders `#filterbar`: a `#moodPill` (the vibe label; tapping it
  returns to planning) followed by one `.fchip` per facet (label + current value +
  chevron).
- Tapping a `.fchip` → `toggleFacet(key)` sets `openFacetKey`, opens the sheet, and
  `renderFacetPanel()` lists that facet's options as `.facet-pill`s in `#facetPanel`.
- **The immediate-search behavior:** clicking a `.facet-pill` calls `setSingle` (or
  `setInterestToggle` for multi), then unconditionally `currentMood = null;
  runSearch(); buildFilterBar();`. Single-select also clears `openFacetKey` (closes the
  panel); Interest (multi) leaves it open.
- `currentMood` is a module var holding the chosen `PRESET` object (or `null` →
  "Popular"); it only drives the mood-pill label.
- `choosePreset(p)` (start-screen vibe pick) sets `currentMood = p`, `applyPreset(p)`
  (writes all chips), `runSearch()`, `buildFilterBar()`.
- Helpers: `setSingle(cont, attr, val)`, `setInterests(list)`, `applyPreset(p)`,
  `facetValue(f)`, `tileText(tile)`, `tileIcon(tile)`, `pt(preset)` (localized label).

`PRESET` shape: `{ key, icon, grad, dayparts, time, crew, transport, intensity,
interests:[], en:{t,s}, ru:{t,s} }`.

The results sheet markup (`#sheet`) is: `sheet-handle`, `sheet-head`, `#filterbar`,
`#facetPanel`, `sheet-body` (`#carousel`).

## Goals

1. A **Transport** facet (Car/Walk/Bike) in the filter bar, single-select, default Car.
2. Filter option taps **stage** changes instead of searching; one **Apply** runs the
   search; **Reset** discards staged changes.
3. The last **3** applied searches are cached (vibe + all facets), shown as selectable
   **Recent** chips; selecting one stages it for Apply; cache persists across reloads.

## Non-goals (YAGNI)

- No recents/filters on the **start screen** — results view only.
- No reverse geocoding, address search, or new payload fields (transport already ships).
- No server-side persistence — localStorage only.
- No drag-reorder or manual delete of recents (auto dedupe + cap 3 only).
- No backend/template/data-model changes.

## Design

### 1. Transport facet

Add to `FACETS` (between Time and Crew, so order is **Vibe · Time · Transport · Crew ·
Interest · Effort**):

```js
{ key: "transport", cont: "transportChips", attr: "transport", multi: false, label: "transport", title: "f_transport" }
```

It reuses the existing `#transportChips` tiles (walk/car/bike, with their own
footprints/car-front/bike icons via `tileIcon`). Default is Car (the wizard's
pre-selected tile). The payload already reads `#transportChips`, so nothing downstream
changes.

i18n (`mood.js` `LX`, EN + RU):

- `transport`: `"Transport"` / `"Транспорт"` (chip label)
- `f_transport`: `"How are you getting there?"` / `"Как добираетесь?"` (panel title)

### 2. Staged Apply

Introduce an explicit applied/staged distinction.

**State object** — `readState()` returns the current selection from the chips +
current vibe:

```
{ vibeKey: string|null, minutes, transport, group, interests:[...], intensity }
```

- `vibeKey` = `currentMood ? currentMood.key : null`.
- Each facet value read from its container's `.tile.is-active` `data-*` (interests is an
  array of active `data-interest`).

**`writeState(state)`** applies a state object to the UI: `setSingle` for
time/transport/crew/effort, `setInterests(state.interests)`, and set `currentMood` to
the matching `PRESET` (by `vibeKey`) or `null`.

**`appliedSnapshot`** — the last *searched* state. Set on every search commit
(`choosePreset`, Apply, and selecting-then-applying a recent).

**Pending** — computed over the **facet fields only** (`minutes`, `transport`, `group`,
`interests`, `intensity`), **not** the cosmetic `vibeKey`. Otherwise tapping one filter
would read as two changes, because customizing also clears the vibe label.
`isPending()` compares the current facets to `appliedSnapshot`'s facets (interests
compared as sorted sets; see `normalizeState`). The vibe label is display-only: set
immediately when staging/selecting, restored on Reset, and never affects the search
(the payload reads facets, not the vibe).

**Behavior changes:**

- `renderFacetPanel` pill click: call `setSingle`/`setInterestToggle` and set
  `currentMood = null` (customizing clears the vibe label), then **re-render the bar +
  apply bar + pending highlight — but do NOT call `runSearch()`**. Single-select still
  closes the panel; Interest stays open.
- **Apply** (`applyStaged()`): `runSearch()`, `saveRecent(readState())`,
  `appliedSnapshot = readState()`, hide the apply bar, `buildFilterBar()`.
- **Reset** (`resetStaged()`): `writeState(appliedSnapshot)`, hide the apply bar,
  `buildFilterBar()`.
- **Leaving results** (mood pill → planning, Edit trip): call `resetStaged()` so a
  half-staged bar never persists into the next visit.

**Apply / Reset bar** — `#applyBar`, rendered directly **below `#facetPanel`** (above
the cards), shown only when `isPending()`:

```
N changes              [ Reset ]  [ Apply ]
```

`N` = count of facet fields (not the vibe label) whose value differs from
`appliedSnapshot`. Staged
`.fchip`s whose value differs from the snapshot get a `.changed` highlight so pending
edits are visible at a glance.

### 3. Recent choices

**Store** — `localStorage["ap.recentChoices"]`: a JSON array of **normalized** state
objects, newest-first, deduped, capped at 3.

- `normalizeState(s)` sorts `interests` so equality is order-independent.
- `saveRecent(state)`: drop any existing entry equal to `state`, `unshift`, `slice(0,
  3)`, persist. Called on every search commit (vibe pick **and** Apply).
- `loadRecents()` / `persistRecents()` wrap localStorage with try/catch (private-mode
  safe).

**Render** — `#recentRow`, below `#facetPanel`, **above** `#applyBar`. Shown when there
is ≥1 recent **and** no facet panel is open (`openFacetKey === null`). Layout: a
`"Recent"` label + up to 3 chips (horizontal scroll). Each chip's label:

- If `vibeKey` is set → that preset's icon + localized title (`pt(preset).t`), e.g.
  `⚡ Quick Escape`.
- Else → a compact summary from the tiles' own texts: `"{time} · {transport} · {crew}"`,
  e.g. `2 hours · Walk · Solo`.

**Select** — tapping a recent calls `writeState(recent)` (stages: fills all chips +
restores the vibe label), then re-renders. Because the staged state now differs from
`appliedSnapshot`, the apply bar appears; the user confirms with **Apply** (consistent
with the staged model). If the recent equals the current applied state, nothing is
pending and no bar appears (harmless).

### Resulting results-view layout

```
Your adventures            5 found   [Show others]   ← sheet-head
[⚡ Vibe][Time 5h][Transport Car][Crew][Interest][Effort]  ← #filterbar (scroll-x)
── #facetPanel (only when a chip is tapped) ──
Recent:  [⚡ Quick Escape] [2 hours · Walk · Solo] [1 hour · Bike]  ← #recentRow (idle only)
┌───────────────────────────────────────────┐
│ 2 changes              [ Reset ]  [ Apply ]│  ← #applyBar (pending only)
└───────────────────────────────────────────┘
── result cards (#carousel) ──
```

## Data flow

```
choosePreset(p)        → writeState(p) → runSearch → saveRecent → appliedSnapshot=read
facet-pill tap         → stage chips (no search) → re-render (apply bar if pending)
recent tap             → writeState(recent) → re-render (apply bar if pending)
Apply                  → runSearch → saveRecent → appliedSnapshot=read → hide bar
Reset / leave results  → writeState(appliedSnapshot) → hide bar
```

The search payload is unchanged: `runSearch()` reads the same hidden chips/inputs at
call time, which now only change the results when the user commits (vibe pick, Apply, or
recent+Apply).

## Files touched

- `frontend/mood.js` — add transport to `FACETS`; `readState`/`writeState`/
  `normalizeState`; `appliedSnapshot` + `isPending`; defer search in `renderFacetPanel`;
  `applyStaged`/`resetStaged`; `#applyBar` render; recents store + `#recentRow` render;
  `saveRecent` in `choosePreset` and Apply; `resetStaged` on leaving results; i18n
  (`transport`, `f_transport`, `recent`, `apply`, `reset`, `changes`).
- `frontend/mood.css` — `#applyBar` (bar + filled Apply / ghost Reset), `.fchip.changed`
  highlight, `#recentRow` + recent-chip styles.
- `frontend/index.html` — add `#recentRow` and `#applyBar` containers inside `#sheet`,
  after `#facetPanel`.

No backend, template, or data-model changes.

## Verification

Manual against the running app (`docker compose up --build`, Playwright + screenshots):

1. **Transport facet** appears in the bar, defaults to Car, panel lists Walk/Car/Bike.
2. **Staging:** change Transport then Time; assert **zero** `/recommendations` network
   calls until Apply; the apply bar shows "2 changes"; both chips show `.changed`.
3. **Apply** fires exactly **one** search; results reflect both changes; bar disappears;
   transport value reaches the payload.
4. **Reset** restores the previous applied chips and hides the bar.
5. **Recents:** after applying, a recent chip appears; tapping it stages vibe+filters
   (bar reappears); Apply re-runs. Recents **dedupe**, cap at **3**, persist across a
   page reload.
6. **Leave results** (mood pill) with staged-but-unapplied changes → returning shows no
   pending bar (silently reset).
7. **EN/RU** labels for the transport chip/panel, Recent, Apply, Reset.

## Open risks / notes

- Equality/normalization must sort `interests` or dedupe/pending checks will misfire.
- localStorage access is wrapped in try/catch for private browsing.
- The results sheet top can now stack filterbar + panel + recents + apply bar; recents
  hide while a panel is open and the apply bar shows only when pending, so at most two of
  these are visible at once, keeping the peek height usable.
