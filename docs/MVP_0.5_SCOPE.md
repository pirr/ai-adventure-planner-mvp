# MVP 0.5 — Intent-aware search: "drinks" category + primary-intent re-rank

## Context

Today a focused wish like *"I want to drink a beer nearby"* does not surface the right places
first. Two reasons:

1. **The vocabulary is too coarse.** The canonical interests are
   `{history, fortresses, viewpoints, nature, water, food, surprise me}` (`schemas.py` `INTEREST_IDS`),
   and `food` lumps **cafe / restaurant / bar / pub / fast_food** into one place type `"food"`
   (`places.py` `_place_type_from_tags`, `scoring.py` `PLACE_INTERESTS`). There is no way to say
   "drinking place" vs "eating place", so the parser can only emit `["food"]`.
2. **Interest barely affects ranking.** `interest_fit` is ~9% of the Adventure Score
   (`scoring.py` `score_candidate`) and the Overpass query always pulls viewpoints/forts/nature
   regardless of interest. So a nice viewpoint can outrank a pub even when the user asked for beer.

This milestone makes a **focused intent lead the results** — *"drink beer" → pubs first* — and
generalizes to other single-intent searches ("coffee", "swim", "history") because they all flow
through the same mechanism. It ships in two parts that work **without any LLM** (the deterministic
baseline); a semantic LLM re-rank is explicitly **deferred** (see end).

Decisions that shape the design:

- **`drinks` is a first-class interest**, sibling to `food`. After this change `food` means *eat*
  (restaurant / cafe / fast_food / ice_cream) and `drinks` means *drink* (pub / bar / biergarten).
- **Strong re-rank only on focused searches.** When exactly one interest is selected (and it is
  not `surprise me`), matching places lead the list; the default multi-chip browse is unchanged.
  The re-rank **reorders only** — it never changes a card's displayed `adventure_score`.
- **Re-rank runs on feasible candidates only.** It reorders *after* the existing `_is_recommendable`
  feasibility filter, so an impractically far pub never jumps to the top.
- **No LLM required for §1+§2.** The Drinks chip and the re-rank work in `TemplateProvider` mode.
  The parser emitting `"drinks"` (Workstream 5) only adds value when a real LLM is configured —
  the same condition the "Describe your trip" field already requires.
- **Deferred: LLM semantic re-rank.** Letting the model reorder real candidates by the free-text
  wish ("romantic dinner", "quiet place to read") is the natural next step and is scoped at the
  end, not built here.

Branch: a feature branch off `main` (which already contains the describe-trip flow and the
`dinner-stroll-food-results` fixes). Backend-first.

---

## Workstream 1 — Schemas (`backend/app/schemas.py`)

- Add `"drinks"` to `INTEREST_IDS`:
  `{"history", "fortresses", "viewpoints", "nature", "water", "food", "drinks", "surprise me"}`.
- No other schema change. `ParsedSituation.whitelist_interests` already gates against `INTEREST_IDS`,
  so it accepts `"drinks"` automatically once the set includes it. `AdventureRequest.interests`
  stays a free `list[str]` normalized lower-case.

## Workstream 2 — Places (`backend/app/services/places.py`)

- **`_place_type_from_tags`**: split the single `food` branch:
  - `amenity in {"bar", "pub", "biergarten"}` ⇒ `"drinks"`
  - `amenity in {"restaurant", "cafe", "fast_food", "ice_cream"}` ⇒ `"food"`
- **`_build_overpass_query`**: trigger the amenity block when `"food"` **or** `"drinks"` is in the
  interests, and add `biergarten` to the regex
  (`restaurant|cafe|bar|pub|fast_food|ice_cream|biergarten`). The query stays broad; ranking
  decides. (Other category blocks are unchanged.)
- **`INTEREST_OSM_FILTERS`**: `food` → `["amenity=cafe", "amenity=restaurant", "amenity=fast_food"]`;
  add `drinks` → `["amenity=bar", "amenity=pub", "amenity=biergarten"]`.
- **Google candidate fallback** (`_needs_google_candidates` / the live-candidate path): treat
  `drinks` like `food` — when `"drinks"` is requested and fewer than ~5 `drinks`-typed candidates
  came back from OSM, allow a Google Text Search seeded with a "bar / pub" query so dense-but-
  sparse-in-OSM areas still return drinking spots.

## Workstream 3 — Scoring (`backend/app/services/scoring.py`)

- `INTEREST_ALIASES`: add `"drinks": "drinks"` (and optionally `"drink": "drinks"`).
- `PLACE_INTERESTS`: add `"drinks": {"drinks"}`; keep `"food": {"food"}`.
- Extract the "does this place satisfy this interest" test (currently inline in `_interest_fit`)
  into a reusable helper so Workstream 4 can use the *same* definition:

  ```python
  def place_matches_interest(place: PlaceCandidate, interest: str) -> bool:
      target = normalize_interest(interest)
      tag_interests = set(map(normalize_interest, place.tags.get("interests", [])))
      available = tag_interests | PLACE_INTERESTS.get(place.type, set()) | {normalize_interest(place.type)}
      return target in available
  ```
  `_interest_fit` keeps its current numeric behavior (now reusing `available` via the helper's set).

## Workstream 4 — Primary-intent re-rank (`backend/app/services/recommendations.py`)

- Add a small helper to detect a **focused** request and its primary interest:

  ```python
  def _primary_interest(request: AdventureRequest) -> str | None:
      interests = [normalize_interest(i) for i in request.interests]
      if len(interests) == 1 and interests[0] != "surprise me":
          return interests[0]
      return None
  ```
- In the final ordering step (where `final` is sorted and `top` is taken), when a primary interest
  exists, **stable-partition** the recommendable candidates into *matches-primary* then *rest*
  (each tier keeps the existing score order from `order_key`), then take `request.limit` from the
  concatenation:

  ```python
  recommendable = [c for c in final if _is_recommendable(c, request)]
  primary = _primary_interest(request)
  if primary:
      matched = [c for c in recommendable if place_matches_interest(c.place, primary)]
      rest = [c for c in recommendable if not place_matches_interest(c.place, primary)]
      recommendable = matched + rest
  top = recommendable[: request.limit]
  ```
- `adventure_score`, `score_breakdown`, rejected-alternatives logic and the "show others" rotation
  (`order_key`) are untouched — this only changes *display order* of the chosen top-N.

## Workstream 5 — Parser prompt (`backend/app/services/llm/openai_compat.py`)

- Add `"drinks"` to the interests enum line in `_PARSE_SYSTEM_PROMPT`, with a one-line distinction:
  *drinks = bar/pub (going out for a drink); food = a meal/cafe.*
- Add a few-shot pair, e.g. `"I want to drink a beer nearby"` →
  `{"interests": ["drinks"], "max_walking_km": 2}` (and a non-English variant if convenient).
- No code change needed for whitelisting — Workstream 1 covers it.

## Workstream 6 — Frontend (`frontend/index.html`, `frontend/app.js`)

- Add a **Drinks** chip to `#interestChips` after Food: `data-interest="drinks"`, a beer/glass
  Lucide icon, `data-i18n="c_drinks"`.
- Add `c_drinks` to both locale tables in `app.js` (EN `"Drinks"`, RU `"Напитки"`), alongside the
  existing `c_food`.
- No JS logic change: `selectedInterests()` reads `data-interest`, and the describe-trip
  parse-prefill toggles chips by interest id, so a parsed `"drinks"` activates the new chip
  automatically.

## Workstream 7 — Tests (`backend/tests/`)

- `test_places`: `bar`/`pub`/`biergarten` tags ⇒ type `"drinks"`; `restaurant`/`cafe` ⇒ `"food"`;
  the Overpass query includes the amenity block (with `biergarten`) when `drinks` is requested.
- `test_scoring`: `place_matches_interest` true for a pub vs `"drinks"`, false vs `"history"`;
  `_interest_fit` rewards a `drinks` place for a `["drinks"]` request.
- `test_recommendations` (new or extended): with `interests=["drinks"]` and a candidate set
  containing one pub and a higher-base-score viewpoint, the pub is ranked **first**; with the
  default multi-interest request the order is unchanged.
- `test_parse_request`: a "beer" sentence yields `interests` containing `"drinks"` (mock provider
  output validated through `ParsedSituation`).

## Verification

- `docker compose build app` then `docker compose run --rm --no-deps app sh -c "PYTHONPATH=. pytest -q"`
  (no source mount — rebuild is required before pytest).
- Frontend smoke via Playwright over the LAN IP (HTTPS tunnel), cache-busting the asset query:
  pick the Drinks chip alone, run a search, confirm pub/bar cards lead; then type
  *"I want to drink a beer nearby"* in Describe and confirm the same.

---

## Deferred to a later milestone — LLM semantic re-rank

Once §1+§2 ship, add an optional LLM pass that reorders the *feasible* candidate pool by the raw
`request_text` (handles open-ended wishes like "romantic dinner" or "quiet place to read" with no
new category tables):

- New provider method `rank_candidates(request_text, candidates, lang) -> list[str] | None`;
  `TemplateProvider` returns `None`.
- `openai_compat`: prompt feeds the user's sentence + a numbered list of feasible candidates
  (id, name, type, ~distance); the model returns ids best-first. A grounding guard keeps only
  known ids; `None`/empty ⇒ fall back to the Workstream 4 order.
- Cost-gated by `LLM_RANK_ENABLED` + a daily/per-`anonymous_id` budget (reuse
  `storage.reserve_api_calls`) + feeding only the top ~12 candidates.
