# Intent-aware Search (MVP 0.5) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A focused single-interest search leads its results with matching places — "drink beer near" → pubs first — by adding a `drinks` category and a deterministic primary-intent re-rank.

**Architecture:** Split the coarse `food` place type into `food` (eat) and `drinks` (pub/bar/biergarten) end-to-end (OSM typing, Overpass query, Google fallback, scoring). Then, when exactly one interest is selected, stable-partition the *feasible* candidates so matching types come first — display order only, scores untouched. No LLM required (the LLM semantic re-rank is deferred per the spec).

**Tech Stack:** FastAPI, Python 3.13, pytest, httpx; static JS frontend. Spec: `docs/MVP_0.5_SCOPE.md`.

---

## Running tests (read first)

The container has **no source mount**, so a code edit is not visible until you rebuild
(see the project memory). For each test run in this plan use:

```bash
docker compose build app \
  && docker compose run --rm --no-deps app sh -c "PYTHONPATH=. pytest <path> -v"
```

Optional faster inner loop (mount local source over the image so edits apply without rebuild):

```bash
docker compose run --rm --no-deps -v "$(pwd)/backend:/app/backend" app \
  sh -c "PYTHONPATH=. pytest <path> -v"
```

Commit after each task. Branch is already `feat/intent-aware-search`.

## File structure

- `backend/app/schemas.py` — add `drinks` to the interest whitelist.
- `backend/app/services/places.py` — OSM place typing, Overpass query, estimates, Google-fallback trigger.
- `backend/app/services/scoring.py` — `drinks` interest mapping + the shared `place_matches_interest` helper + the `apply_primary_rerank` partition.
- `backend/app/services/recommendations.py` — call `apply_primary_rerank` when choosing the top-N.
- `backend/app/services/google_places.py` — type/query/interest/estimate maps for `drinks`.
- `backend/app/services/llm/openai_compat.py` — parse prompt enum + few-shot.
- `frontend/index.html`, `frontend/app.js` — Drinks chip + `c_drinks` label.
- Tests live next to existing ones in `backend/tests/` (`test_places.py`, `test_scoring.py`, `test_google_places.py`, `test_parse_request.py`).

---

## Task 1: `drinks` in the interest whitelist

**Files:**
- Modify: `backend/app/schemas.py:49`
- Test: `backend/tests/test_parse_request.py`

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_parse_request.py`:

```python
def test_parsed_situation_accepts_drinks_interest():
    from app.schemas import ParsedSituation

    assert ParsedSituation(interests=["drinks"]).interests == ["drinks"]
    # Unknown interests are still dropped by the whitelist.
    assert ParsedSituation(interests=["nightclub"]).interests is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose build app && docker compose run --rm --no-deps app sh -c "PYTHONPATH=. pytest tests/test_parse_request.py::test_parsed_situation_accepts_drinks_interest -v"`
Expected: FAIL — `assert None == ["drinks"]` (whitelist drops `drinks`).

- [ ] **Step 3: Add `drinks` to `INTEREST_IDS`**

In `backend/app/schemas.py`, change line 49:

```python
INTEREST_IDS = {"history", "fortresses", "viewpoints", "nature", "water", "food", "drinks", "surprise me"}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker compose build app && docker compose run --rm --no-deps app sh -c "PYTHONPATH=. pytest tests/test_parse_request.py -v"`
Expected: PASS (all parse tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/schemas.py backend/tests/test_parse_request.py
git commit -m "feat(schemas): add drinks to the interest whitelist"
```

---

## Task 2: OSM place typing — pub/bar/biergarten ⇒ `drinks`

**Files:**
- Modify: `backend/app/services/places.py:86-110` (`_place_type_from_tags`), `:128-152` (estimate maps), `:23-31` (`INTEREST_OSM_FILTERS`)
- Test: `backend/tests/test_places.py` (update existing + add)

- [ ] **Step 1: Update/extend the tests**

In `backend/tests/test_places.py`, replace `test_food_amenities_become_food_places` with:

```python
def test_eat_amenities_become_food_places():
    assert _place_type_from_tags({"amenity": "restaurant"}) == "food"
    assert _place_type_from_tags({"amenity": "cafe"}) == "food"
    assert _place_type_from_tags({"amenity": "fast_food"}) == "food"


def test_drink_amenities_become_drinks_places():
    assert _place_type_from_tags({"amenity": "pub"}) == "drinks"
    assert _place_type_from_tags({"amenity": "bar"}) == "drinks"
    assert _place_type_from_tags({"amenity": "biergarten"}) == "drinks"
```

- [ ] **Step 2: Run to verify failure**

Run: `docker compose build app && docker compose run --rm --no-deps app sh -c "PYTHONPATH=. pytest tests/test_places.py::test_drink_amenities_become_drinks_places -v"`
Expected: FAIL — pub currently returns `"food"`.

- [ ] **Step 3: Split the amenity branch**

In `backend/app/services/places.py`, replace the single amenity branch (line 108-109):

```python
    if amenity in {"bar", "pub", "biergarten"}:
        return "drinks"
    if amenity in {"cafe", "fast_food", "ice_cream", "restaurant"}:
        return "food"
```

Add `drinks` to the estimate maps. In `_estimate_activity` (after the `"food": 45,` line):

```python
        "food": 45,
        "drinks": 50,
```

In `_estimate_walking` (after the `"food": 0.4,` line):

```python
        "food": 0.4,
        "drinks": 0.3,
```

Update `INTEREST_OSM_FILTERS` (lines 23-31): change the `food` entry and add `drinks`:

```python
    "food": ["amenity=cafe", "amenity=restaurant", "amenity=fast_food"],
    "drinks": ["amenity=bar", "amenity=pub", "amenity=biergarten"],
```

- [ ] **Step 4: Run to verify pass**

Run: `docker compose build app && docker compose run --rm --no-deps app sh -c "PYTHONPATH=. pytest tests/test_places.py -v"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/places.py backend/tests/test_places.py
git commit -m "feat(places): type pub/bar/biergarten as drinks"
```

---

## Task 3: Overpass query fetches drink amenities

**Files:**
- Modify: `backend/app/services/places.py:56-83` (`_build_overpass_query`)
- Test: `backend/tests/test_places.py` (update existing + add)

- [ ] **Step 1: Update/extend the tests**

In `backend/tests/test_places.py`, replace the two overpass tests with:

```python
_AMENITY_REGEX = '"amenity"~"restaurant|cafe|bar|pub|fast_food|ice_cream|biergarten"'


def test_food_interest_adds_amenities_to_overpass_query():
    query = _build_overpass_query(42.43, 18.69, 25000, ["food", "history"])
    assert _AMENITY_REGEX in query


def test_drinks_interest_adds_amenities_to_overpass_query():
    query = _build_overpass_query(42.43, 18.69, 25000, ["drinks"])
    assert _AMENITY_REGEX in query


def test_non_food_interest_keeps_amenities_out_of_overpass_query():
    query = _build_overpass_query(42.43, 18.69, 25000, ["history", "fortresses"])
    assert _AMENITY_REGEX not in query
```

- [ ] **Step 2: Run to verify failure**

Run: `docker compose build app && docker compose run --rm --no-deps app sh -c "PYTHONPATH=. pytest tests/test_places.py::test_drinks_interest_adds_amenities_to_overpass_query -v"`
Expected: FAIL — `drinks` does not trigger the block, and the regex lacks `biergarten`.

- [ ] **Step 3: Trigger on food OR drinks, add biergarten**

In `backend/app/services/places.py` `_build_overpass_query`, replace lines 59-65:

```python
    food_block = ""
    if normalized & {"food", "drinks"}:
        food_block = f"""
  node(around:{radius_m},{lat},{lon})["amenity"~"restaurant|cafe|bar|pub|fast_food|ice_cream|biergarten"];
  way(around:{radius_m},{lat},{lon})["amenity"~"restaurant|cafe|bar|pub|fast_food|ice_cream|biergarten"];
  relation(around:{radius_m},{lat},{lon})["amenity"~"restaurant|cafe|bar|pub|fast_food|ice_cream|biergarten"];
"""
```

- [ ] **Step 4: Run to verify pass**

Run: `docker compose build app && docker compose run --rm --no-deps app sh -c "PYTHONPATH=. pytest tests/test_places.py -v"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/places.py backend/tests/test_places.py
git commit -m "feat(places): fetch drink amenities in the overpass query"
```

---

## Task 4: Scoring — `drinks` mapping + `place_matches_interest` helper

**Files:**
- Modify: `backend/app/services/scoring.py:11-36` (aliases + `PLACE_INTERESTS`), `:93-109` (`_interest_fit`)
- Test: `backend/tests/test_scoring.py`

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_scoring.py`:

```python
def test_place_matches_interest_for_drinks():
    from app.schemas import PlaceCandidate
    from app.services.scoring import place_matches_interest

    pub = PlaceCandidate(source="t", source_id="t:1", name="Pub", type="drinks", lat=42.4, lon=18.7)
    assert place_matches_interest(pub, "drinks") is True
    assert place_matches_interest(pub, "history") is False
```

- [ ] **Step 2: Run to verify failure**

Run: `docker compose build app && docker compose run --rm --no-deps app sh -c "PYTHONPATH=. pytest tests/test_scoring.py::test_place_matches_interest_for_drinks -v"`
Expected: FAIL — `place_matches_interest` does not exist.

- [ ] **Step 3: Add mapping + helper**

In `backend/app/services/scoring.py`:

Add to `INTEREST_ALIASES` (inside the dict):

```python
    "drink": "drinks",
    "drinks": "drinks",
```

Add to `PLACE_INTERESTS` (inside the dict):

```python
    "drinks": {"drinks"},
```

Add the helper after `normalize_interest` (around line 56), and refactor `_interest_fit` to reuse it:

```python
def place_matches_interest(place: PlaceCandidate, interest: str) -> bool:
    """True when `place` satisfies `interest`, using the same availability set
    `_interest_fit` scores on. Shared so ranking and scoring agree."""
    target = normalize_interest(interest)
    return target in _place_interests(place)


def _place_interests(place: PlaceCandidate) -> set[str]:
    tag_interests = set(map(normalize_interest, place.tags.get("interests", [])))
    return tag_interests | PLACE_INTERESTS.get(place.type, set()) | {normalize_interest(place.type)}
```

Then in `_interest_fit`, replace lines 98-100:

```python
    available = _place_interests(place)
```

- [ ] **Step 4: Run to verify pass**

Run: `docker compose build app && docker compose run --rm --no-deps app sh -c "PYTHONPATH=. pytest tests/test_scoring.py -v"`
Expected: PASS (existing scoring tests unaffected — the availability set is unchanged for non-drinks types).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/scoring.py backend/tests/test_scoring.py
git commit -m "feat(scoring): drinks interest + shared place_matches_interest helper"
```

---

## Task 5: Primary-intent re-rank

**Files:**
- Modify: `backend/app/services/scoring.py` (add `apply_primary_rerank`), `backend/app/services/recommendations.py:126`
- Test: `backend/tests/test_scoring.py`

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_scoring.py`:

```python
def _scored(place_type: str, score: int):
    from app.schemas import PlaceCandidate, RouteInfo, ScoreBreakdown
    from app.services.scoring import ScoredCandidate

    place = PlaceCandidate(source="t", source_id=f"t:{place_type}:{score}", name=place_type, type=place_type, lat=42.4, lon=18.7)
    route = RouteInfo(source="t", one_way_minutes=10, round_trip_minutes=20, distance_km=5, map_url="x")
    breakdown = ScoreBreakdown(time_fit=80, weather_fit=80, distance_fit=80, safety_fit=80, group_fit=80, interest_fit=80, place_quality=80)
    return ScoredCandidate(place=place, route=route, total_minutes=40, score=score, breakdown=breakdown, why=[], warnings=[], description="")


def test_primary_rerank_puts_single_interest_match_first():
    from app.schemas import AdventureRequest
    from app.services.scoring import apply_primary_rerank

    # Viewpoint scores higher than the pub, but a focused "drinks" search leads with the pub.
    viewpoint = _scored("viewpoint", 90)
    pub = _scored("drinks", 70)
    request = AdventureRequest(lat=42.4, lon=18.7, interests=["drinks"])

    ordered = apply_primary_rerank([viewpoint, pub], request)
    assert [c.place.type for c in ordered] == ["drinks", "viewpoint"]


def test_primary_rerank_noop_for_multi_interest():
    from app.schemas import AdventureRequest
    from app.services.scoring import apply_primary_rerank

    viewpoint = _scored("viewpoint", 90)
    pub = _scored("drinks", 70)
    request = AdventureRequest(lat=42.4, lon=18.7, interests=["drinks", "history"])

    ordered = apply_primary_rerank([viewpoint, pub], request)
    assert [c.place.type for c in ordered] == ["viewpoint", "drinks"]
```

- [ ] **Step 2: Run to verify failure**

Run: `docker compose build app && docker compose run --rm --no-deps app sh -c "PYTHONPATH=. pytest tests/test_scoring.py::test_primary_rerank_puts_single_interest_match_first -v"`
Expected: FAIL — `apply_primary_rerank` does not exist.

- [ ] **Step 3: Implement `apply_primary_rerank`**

In `backend/app/services/scoring.py`, add (near `place_matches_interest`):

```python
def apply_primary_rerank(candidates: list[ScoredCandidate], request: AdventureRequest) -> list[ScoredCandidate]:
    """Lead with places that match a *focused* request (exactly one interest,
    not 'surprise me'). Stable partition — order only, scores untouched. No-op
    for multi-interest or 'surprise me' searches."""
    interests = [normalize_interest(i) for i in request.interests]
    if len(interests) != 1 or interests[0] == "surprise me":
        return list(candidates)
    primary = interests[0]
    matched = [c for c in candidates if place_matches_interest(c.place, primary)]
    rest = [c for c in candidates if not place_matches_interest(c.place, primary)]
    return matched + rest
```

- [ ] **Step 4: Run to verify pass**

Run: `docker compose build app && docker compose run --rm --no-deps app sh -c "PYTHONPATH=. pytest tests/test_scoring.py -v"`
Expected: PASS.

- [ ] **Step 5: Wire into the pipeline**

In `backend/app/services/recommendations.py`, add `apply_primary_rerank` to the scoring import on line 15:

```python
from app.services.scoring import ScoredCandidate, apply_primary_rerank, rejected_from_scored, score_candidate, to_recommendation
```

Replace line 126:

```python
    recommendable = [item for item in final if _is_recommendable(item, request)]
    top = apply_primary_rerank(recommendable, request)[: request.limit]
```

- [ ] **Step 6: Run the full backend suite**

Run: `docker compose build app && docker compose run --rm --no-deps app sh -c "PYTHONPATH=. pytest -q"`
Expected: PASS (all tests).

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/scoring.py backend/app/services/recommendations.py backend/tests/test_scoring.py
git commit -m "feat(recommendations): lead focused searches with matching places"
```

---

## Task 6: Parser prompt knows `drinks`

**Files:**
- Modify: `backend/app/services/llm/openai_compat.py:40` (enum), `:45-52` (few-shots)
- Test: `backend/tests/test_openai_compat.py`

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_openai_compat.py`:

```python
def test_parse_prompt_includes_drinks_and_example():
    from app.services.llm.openai_compat import _PARSE_SYSTEM_PROMPT, build_parse_messages

    assert "drinks" in _PARSE_SYSTEM_PROMPT
    messages = build_parse_messages("I want to drink a beer nearby", "en")
    assert any("drinks" in m["content"] for m in messages if m["role"] == "assistant")
```

- [ ] **Step 2: Run to verify failure**

Run: `docker compose build app && docker compose run --rm --no-deps app sh -c "PYTHONPATH=. pytest tests/test_openai_compat.py::test_parse_prompt_includes_drinks_and_example -v"`
Expected: FAIL — prompt has no `drinks`.

- [ ] **Step 3: Update prompt + few-shots**

In `backend/app/services/llm/openai_compat.py`, replace the interests line (40):

```python
    '- interests: any of ["history", "fortresses", "viewpoints", "nature", "water", "food", "drinks", "surprise me"]'
    " (drinks = bar/pub for going out for a drink; food = a meal or cafe)\n"
```

Add a few-shot to `_PARSE_FEW_SHOTS` (after the existing entries):

```python
    ("I want to drink a beer nearby",
     '{"interests": ["drinks"], "max_walking_km": 2}'),
```

- [ ] **Step 4: Run to verify pass**

Run: `docker compose build app && docker compose run --rm --no-deps app sh -c "PYTHONPATH=. pytest tests/test_openai_compat.py -v"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/llm/openai_compat.py backend/tests/test_openai_compat.py
git commit -m "feat(llm): teach the parser the drinks intent"
```

---

## Task 7: Google candidate fallback for `drinks`

**Files:**
- Modify: `backend/app/services/google_places.py:31-44` (type sets/map), `:73-82` (`_candidate_text_query`), `:113-130` (`_candidate_place_type`, `_candidate_interests`), `:133-152` (estimates); `backend/app/services/places.py:236-242` (`_needs_google_candidates`)
- Test: `backend/tests/test_google_places.py`

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_google_places.py`:

```python
def test_drinks_candidate_query_and_typing():
    from app.services.google_places import _candidate_text_query, _candidate_place_type, _candidate_interests

    assert _candidate_text_query(["drinks"]) == "bars pubs"
    assert _candidate_place_type({"primaryType": "pub", "types": ["pub"]}) == "drinks"
    assert _candidate_place_type({"primaryType": "restaurant", "types": ["restaurant"]}) == "food"
    assert _candidate_interests("drinks") == ["drinks"]


def test_needs_google_candidates_triggers_for_sparse_drinks():
    from app.schemas import PlaceCandidate
    from app.services.places import _needs_google_candidates

    eats = [PlaceCandidate(source="o", source_id=f"o:{i}", name="x", type="food", lat=42.4, lon=18.7) for i in range(10)]
    assert _needs_google_candidates(eats, ["drinks"]) is True
```

- [ ] **Step 2: Run to verify failure**

Run: `docker compose build app && docker compose run --rm --no-deps app sh -c "PYTHONPATH=. pytest tests/test_google_places.py::test_drinks_candidate_query_and_typing -v"`
Expected: FAIL — drinks query/typing not implemented.

- [ ] **Step 3: Implement the drinks mapping**

In `backend/app/services/google_places.py`, replace `_FOOD_TYPES` (line 31) with two sets:

```python
_DRINK_TYPES = {"bar", "pub"}
_EAT_TYPES = {"cafe", "coffee_shop", "ice_cream_shop", "restaurant", "meal_takeaway"}
```

In `_TYPE_TO_PLACE_TYPE`, set `"bar": "drinks"` and `"pub": "drinks"` (leave cafe/coffee_shop/ice_cream_shop/meal_takeaway/restaurant as `"food"`).

Replace `_candidate_place_type` (lines 113-119):

```python
def _candidate_place_type(result: dict[str, Any]) -> str:
    types = {str(item) for item in result.get("types") or []}
    primary_type = str(result.get("primaryType") or "")
    if types & _DRINK_TYPES or primary_type in _DRINK_TYPES:
        return "drinks"
    if types & _EAT_TYPES or primary_type in _EAT_TYPES:
        return "food"
    return _TYPE_TO_PLACE_TYPE.get(primary_type, "attraction")
```

In `_candidate_text_query`, add a drinks branch before the food branch and narrow food:

```python
    if "drinks" in normalized:
        return "bars pubs"
    if "food" in normalized:
        return "restaurants cafes"
```

In `_candidate_interests`, add `"drinks": ["drinks"],`. In `_candidate_activity`, add `"drinks": 50,`. In `_candidate_walking`, add `"drinks": 0.3,`.

In `backend/app/services/places.py`, replace `_needs_google_candidates` (lines 236-242):

```python
def _needs_google_candidates(candidates: list[PlaceCandidate], interests: list[str]) -> bool:
    normalized = {str(interest).strip().lower() for interest in interests}
    if len(candidates) < 8:
        return True
    if "food" in normalized and sum(c.type == "food" for c in candidates) < 5:
        return True
    if "drinks" in normalized and sum(c.type == "drinks" for c in candidates) < 5:
        return True
    return False
```

- [ ] **Step 4: Run to verify pass**

Run: `docker compose build app && docker compose run --rm --no-deps app sh -c "PYTHONPATH=. pytest tests/test_google_places.py tests/test_places.py -v"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/google_places.py backend/app/services/places.py backend/tests/test_google_places.py
git commit -m "feat(google-places): return bars/pubs for the drinks intent"
```

---

## Task 8: Frontend — Drinks chip

**Files:**
- Modify: `frontend/index.html:123` (chips), `frontend/app.js:322` and `:490` (locale labels)
- Verification: Playwright (no unit test for static JS)

- [ ] **Step 1: Add the chip**

In `frontend/index.html`, after the Food chip (line 123), add:

```html
          <button type="button" class="tile chip-tile" data-interest="drinks"><span class="tile-emoji"><i data-lucide="beer"></i></span><span class="tile-text" data-i18n="c_drinks">Drinks</span></button>
```

- [ ] **Step 2: Add the labels**

In `frontend/app.js`, after the EN `c_food: 'Food',` (line 322):

```javascript
    c_drinks: 'Drinks',
```

After the RU `c_food: 'Еда',` (line 490):

```javascript
    c_drinks: 'Напитки',
```

- [ ] **Step 3: Verify in the browser (Playwright)**

Rebuild and serve over the HTTPS tunnel, then over the LAN IP (not localhost), bump the asset `?v=` query to bust cache:

```bash
docker compose -f docker-compose.yml -f docker-compose.tunnel.yml up --build -d
docker compose -f docker-compose.yml -f docker-compose.tunnel.yml logs cloudflared | grep trycloudflare.com
```

Checks:
- The **Drinks** chip renders in the interests row with the beer icon.
- Select **only** Drinks, run a search → pub/bar cards lead the list and map markers.
- Type *"I want to drink a beer nearby"* in Describe (LLM configured) → same result.

- [ ] **Step 4: Commit**

```bash
git add frontend/index.html frontend/app.js
git commit -m "feat(ui): add Drinks interest chip"
```

---

## Final verification

- [ ] Full suite green: `docker compose build app && docker compose run --rm --no-deps app sh -c "PYTHONPATH=. pytest -q"`
- [ ] Spec coverage: §1 (Tasks 1-3, 7), §2 (Tasks 4-5), parser (Task 6), frontend (Task 8) all implemented; LLM semantic re-rank intentionally **deferred** (spec "Deferred" section).
- [ ] Manual: Drinks chip → pubs first; default multi-chip browse order unchanged.
