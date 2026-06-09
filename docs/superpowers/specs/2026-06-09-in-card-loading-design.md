# In-card loading state (remove full-screen loading overlay)

**Date:** 2026-06-09
**Branch:** `design/guided-explorer`
**Status:** Design approved — ready for implementation plan

## Summary

While a search runs, show a loading indicator **inside the cards area** (a centered
compass spinner + "Scouting your adventure"), instead of the full-screen `#loading`
overlay. The overlay is removed.

## Current state (`frontend/app.js` `runSearch`)

```
clearError(); loadingEl.classList.remove('hidden'); track('search_started')
fetch /api/recommendations
  ok:    sheetEl.remove('open'); enterExploring(); renderResults(data); loadHistory()
  error: enterExploring(); openSheet(); setError(...)
finally: loadingEl.classList.add('hidden')
```

- `#loading` is a fixed full-screen overlay (`.loading-overlay`) with a `.compass`
  spinner + `loading_title` / `loading_subtitle`.
- `.compass` (styles.css:344) is a standalone class (`animation: spin 2.4s linear
  infinite`, disabled under reduced-motion); it works outside the overlay.
- `renderResults(data)` → `renderCards(items)` sets `carouselEl.innerHTML = ''` then
  appends real cards, so it naturally replaces a loading placeholder in `#carousel`.

## Goal

Replace the full-screen overlay with an in-`#carousel` spinner + text shown during the
fetch, removing the `#loading` markup and its toggles.

## Non-goals (YAGNI)

- No skeleton cards (chose spinner + text).
- No change to weather / data-notes / rejected boxes during loading (cards only).
- No backend changes.

## Design

New `runSearch` flow:

```
clearError(); track('search_started')
sheetEl.classList.remove('open')      // peek so the map shows
enterExploring()                      // show the results sheet now (launcher hides)
renderLoading()                       // spinner + text in #carousel
fetch:
  ok:    renderResults(data); (no-others setError); track; loadHistory()
  error: carouselEl.innerHTML = ''; openSheet(); setError(...)
```

`renderLoading()`:

```js
function renderLoading() {
  carouselEl.innerHTML =
    '<div class="card-loading">' +
      '<div class="compass" aria-hidden="true"><i data-lucide="compass"></i></div>' +
      '<p>' + t('loading_title') + '</p>' +
    '</div>';
  if (window.lucide && window.lucide.createIcons) window.lucide.createIcons();
}
```

`.card-loading` styling: a full-width centered block (`flex: 0 0 100%`, grid place-items
center) with a smaller, clay-tinted compass and a bold title line, sized to fit the short
peek sheet.

**Removed:** the `#loading` overlay markup, the `const loadingEl` reference, and both
`loadingEl` show/hide toggles. The `loading_subtitle` i18n string becomes unused but is
left in place (`loading_title` is still used by `renderLoading`).

## Files touched

- `frontend/app.js` — rewrite `runSearch` loading flow; add `renderLoading()`; drop
  `loadingEl`.
- `frontend/styles.css` — `.card-loading` styles (reuses `.compass`).
- `frontend/index.html` — remove the `#loading` block; bump `app.js` (`?v=12` → `?v=13`)
  and `styles.css` (`?v=4` → `?v=5`) cache keys.

## Verification

`docker compose up --build` + Playwright (cache-busting query):

1. Pick a vibe → the launcher is replaced by the results sheet showing the compass
   spinner + "Scouting your adventure" in `#carousel`; the full-screen overlay does
   **not** appear (`#loading` is gone from the DOM).
2. When the search resolves, real cards replace the spinner.
3. Apply a filter / Show others → spinner shows in the cards area, then results.
4. Error path (if reproducible) clears the spinner and shows the error.
5. No console errors.

## Risks / notes

- Entering exploring before the fetch means the results sheet (and map) appear during
  loading; this is the intended "loading lives where the cards will be" behavior.
- The spinner respects the existing reduced-motion rule (no animation).
