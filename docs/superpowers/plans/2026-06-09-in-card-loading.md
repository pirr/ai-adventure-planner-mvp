# In-card Loading State Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show a compass spinner + text inside the cards area while a search runs, and remove the full-screen loading overlay.

**Architecture:** `runSearch` enters exploring up front and renders a loading block into `#carousel` (reusing the `.compass` class); `renderResults` then replaces it with real cards. The `#loading` overlay and its references are removed.

**Tech Stack:** Vanilla JS (classic script), Leaflet, plain CSS. Served by FastAPI static hosting via `docker compose`. No JS test framework — verify via Playwright against the running app.

---

## File Structure

| File | Responsibility | Change |
|------|----------------|--------|
| `frontend/app.js` | Search flow | rewrite `runSearch` loading; add `renderLoading()`; drop `loadingEl` |
| `frontend/styles.css` | Loading style | `.card-loading` block (reuses `.compass`) |
| `frontend/index.html` | Markup | remove `#loading`; bump `app.js` `?v=12→13`, `styles.css` `?v=4→5` |

---

## How to run / verify

```bash
docker compose up --build -d
# Playwright: navigate to the LAN IP (ip route get 1.1.1.1), with a cache-busting
# query (e.g. ?fresh=N) so the browser fetches the new app.js/styles.css.
docker compose down   # when finished
```

---

## Task 1: in-card loading

**Files:**
- Modify: `frontend/app.js` — remove `loadingEl` decl (line 3); rewrite `runSearch` (lines 692-721); add `renderLoading`
- Modify: `frontend/styles.css` — add `.card-loading` after the `.loading-overlay` rules (line ~346)
- Modify: `frontend/index.html` — remove `#loading` block (lines 184-188); bump cache keys

- [ ] **Step 1: Drop the `loadingEl` reference**

Find (app.js lines 1-4):

```js
const $ = (id) => document.getElementById(id);
const carouselEl = $('carousel');
const loadingEl = $('loading');
const errorBox = $('errorBox');
```

Replace with:

```js
const $ = (id) => document.getElementById(id);
const carouselEl = $('carousel');
const errorBox = $('errorBox');
```

- [ ] **Step 2: Rewrite `runSearch` + add `renderLoading`**

Find (app.js lines 692-721):

```js
async function runSearch({ excludeSeen = false } = {}) {
  clearError();
  loadingEl.classList.remove('hidden');
  track('search_started', { meta: { exclude_seen: excludeSeen } });

  try {
    const response = await fetch('/api/recommendations', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(requestPayload(excludeSeen)),
    });
    if (!response.ok) {
      const text = await response.text();
      throw new Error(text || `Request failed: ${response.status}`);
    }
    const data = await response.json();
    sheetEl.classList.remove('open'); // start in peek so the map stays visible
    enterExploring();
    renderResults(data);
    if (excludeSeen && !(data.recommendations || []).length) setError(t('no_more_others'));
    track('search_completed', { request_id: data.request_id, meta: { count: (data.recommendations || []).length } });
    loadHistory();
  } catch (error) {
    enterExploring();
    openSheet();
    setError(t('search_failed', { error: error.message }));
  } finally {
    loadingEl.classList.add('hidden');
  }
}
```

Replace with:

```js
// In-card loading: a centered compass spinner + text inside #carousel, shown while
// the search runs and replaced by renderResults() (which rebuilds the carousel).
function renderLoading() {
  carouselEl.innerHTML =
    '<div class="card-loading">' +
      '<div class="compass" aria-hidden="true"><i data-lucide="compass"></i></div>' +
      '<p>' + t('loading_title') + '</p>' +
    '</div>';
  if (window.lucide && window.lucide.createIcons) window.lucide.createIcons();
}

async function runSearch({ excludeSeen = false } = {}) {
  clearError();
  track('search_started', { meta: { exclude_seen: excludeSeen } });
  sheetEl.classList.remove('open'); // peek so the map stays visible
  enterExploring();                 // show the results sheet now (launcher hides)
  renderLoading();                  // spinner + text where the cards will be

  try {
    const response = await fetch('/api/recommendations', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(requestPayload(excludeSeen)),
    });
    if (!response.ok) {
      const text = await response.text();
      throw new Error(text || `Request failed: ${response.status}`);
    }
    const data = await response.json();
    renderResults(data);
    if (excludeSeen && !(data.recommendations || []).length) setError(t('no_more_others'));
    track('search_completed', { request_id: data.request_id, meta: { count: (data.recommendations || []).length } });
    loadHistory();
  } catch (error) {
    carouselEl.innerHTML = ''; // clear the loading indicator
    openSheet();
    setError(t('search_failed', { error: error.message }));
  }
}
```

- [ ] **Step 3: Add `.card-loading` styles**

Find (styles.css lines 344-346):

```css
.compass { font-size: 48px; color: var(--amber-400); margin-bottom: 14px; animation: spin 2.4s linear infinite; }
.compass .lucide { width: 48px; height: 48px; stroke-width: 1.8; }
@keyframes spin { to { transform: rotate(360deg); } }
```

Replace with:

```css
.compass { font-size: 48px; color: var(--amber-400); margin-bottom: 14px; animation: spin 2.4s linear infinite; }
.compass .lucide { width: 48px; height: 48px; stroke-width: 1.8; }
@keyframes spin { to { transform: rotate(360deg); } }

/* in-card loading (replaces the full-screen overlay) */
.card-loading { flex: 0 0 100%; display: grid; place-items: center; gap: 4px; padding: 30px 16px; text-align: center; }
.card-loading .compass { font-size: 34px; color: var(--clay-500); margin-bottom: 4px; }
.card-loading .compass .lucide { width: 34px; height: 34px; }
.card-loading p { font-family: var(--display); font-weight: 700; font-size: 14px; color: var(--ink-700); }
```

- [ ] **Step 4: Remove the `#loading` overlay markup**

Find (index.html lines 184-188):

```html
  <div id="loading" class="loading-overlay hidden">
    <div class="compass" aria-hidden="true"><i data-lucide="compass"></i></div>
    <h2 data-i18n="loading_title">Scouting your adventure</h2>
    <p data-i18n="loading_subtitle">Checking places, weather, travel time and risk rules.</p>
  </div>
```

Delete this block.

- [ ] **Step 5: Bump cache keys**

Find: `<link rel="stylesheet" href="/static/styles.css?v=4" />` → replace `?v=4` with `?v=5`.
Find: `<script src="/static/app.js?v=12"></script>` → replace `?v=12` with `?v=13`.

- [ ] **Step 6: Verify (Playwright)**

```bash
docker compose up --build -d
```

Navigate with a cache-busting query (`http://<ip>:8000/?fresh=1`). Set a location, then click a vibe preset and **immediately** poll:
- Within the first ~500ms, assert `#loading` is gone from the DOM (`document.getElementById('loading') === null`), `body.classList.contains('exploring')` is true, and `#carousel .card-loading` exists with the compass `.lucide` and the loading title text.
- After the search resolves, assert `#carousel .card-loading` is gone and `#carousel .recommendation` cards exist.
- `browser_console_messages` shows no errors.

- [ ] **Step 7: Commit**

```bash
git add frontend/app.js frontend/styles.css frontend/index.html
git commit -m "feat(results): in-card loading spinner; remove full-screen loading overlay"
```

---

## Self-Review notes (author check)

- **Spec coverage:** spinner+text in `#carousel` (`renderLoading`, Step 2) ✓; enter exploring before fetch (Step 2) ✓; `renderResults` replaces loader (unchanged, rebuilds carousel) ✓; error clears loader + shows error (Step 2 catch) ✓; remove `#loading` markup + `loadingEl` + toggles (Steps 1,2,4) ✓; `.card-loading` reuses `.compass` (Step 3) ✓; cache bumps (Step 5) ✓; cards-only (weather/notes untouched) ✓.
- **Naming consistency:** `renderLoading`, `carouselEl`, `enterExploring`, `renderResults`, `t('loading_title')`, class `card-loading`; cache keys `app.js?v=13`, `styles.css?v=5`.
- **No placeholders:** every step has exact before/after.
- **Hoisting:** `renderLoading` is a function declaration above `runSearch`'s use site within the same module — fine.
