# MVP 0.6 — Community Intelligence (Milestone 1): first-party Community Confidence

## Context

The app's job is to **decide for the user** with **grounded** explanations (never invent facts),
while staying privacy-first, cheap, and single-container. The V1 Adventure Score already reserves a
**Community Confidence** slot (`docs/Spec.md` §17 roadmap), and §18 describes a "Community
Intelligence Layer" — but its literal form (scraping Reddit / Telegram / Google Reviews) is
explicitly flagged there as a **far-horizon, legally risky** direction.

This milestone delivers Community Intelligence from the data we *already own*. Today the app collects
a rich first-party behavioral dataset that is only used **per-user** (`personal_preference_fit`):
`feedback` (👍/👎 + reason), `place_marks` / `account_place_marks` (visited), and `events`. We turn
that into a **cross-user** signal — "wisdom of our own adventurers" — keyed by place `source_id`.
This is legally clean, free, perfectly groundable (real actions, no invention), and starts a
usage→quality flywheel that makes user-contributed **Community Tips** (the next milestone) worth
building.

Decisions that shape the design:

- **First-party only.** No external scraping/APIs. The signal is our users' own feedback + visits.
- **Cold-start-neutral.** Community Confidence is a near-exact twin of `personal_preference_fit`: a
  breakdown field defaulting to **neutral 70**, loaded once per request and threaded into
  `score_candidate`. Until real data exists, scores are **identical** to before — zero regression.
- **Weight rebalance keeps the sum at 1.0.** The old 8% `personal_preference_fit` weight splits into
  **4% personal + 4% community**. Both are neutral-70 on cold start, so the rebalance is a no-op
  until data accrues. (The spec's fuller V1 weight table is the eventual target; weights are tunable.)
- **Anti-bias guardrails.** Counts are by **distinct user** (one person can't inflate a place); the
  liked-ratio only moves the score above a minimum of distinct ratings, and even then is
  **Bayesian-shrunk toward neutral** by sample size. Social-proof **badges** need a higher bar.
- **Surface = score factor + badges** (the chosen option): a "Community" row in the score breakdown
  on every card, plus chips ("♥ 82% liked · 40 adventurers", "12 visited recently") on cards.

Branch: `feature/community-confidence` off `main`. Backend-first.

---

## Workstream 1 — Storage aggregation (`backend/app/services/storage.py`)

- Add `community_signals(source_ids) -> {source_id: {ups, downs, raters, recent_visits}}`, mirroring
  `preference_profile`. One bounded query over the request's candidate set:
  - **Likes:** join `feedback` → `recommendations` (by `request_id`+`recommendation_id`, the same
    join `preference_profile` uses), read `source_id` via `json_extract(payload_json, '$.source_id')`,
    and count **distinct** `COALESCE(account_id, anonymous_id)` per place for up vs down.
  - **Visits:** distinct users with `visited=1` and `updated_at` within `community_visit_window_days`
    (default 90), UNIONed across
    `place_marks` and `account_place_marks`.
  - Places with no signal are omitted (cold start → neutral).
- Imports: add `defaultdict`, `timedelta`.

## Workstream 2 — Scoring (`backend/app/services/scoring.py`)

- Add `_community_confidence_fit(place, community)`: neutral 70 on cold start or below
  `COMMUNITY_MIN_RATERS`; otherwise `liked_ratio*100` Bayesian-shrunk toward 70 by sample size.
- Add `_community_badge(community, source_id) -> CommunitySignal | None`: returns chips only when
  `raters >= COMMUNITY_BADGE_MIN_RATERS` (liked chip) or `recent_visits >= COMMUNITY_BADGE_MIN_VISITS`
  (visits chip); each dimension that doesn't clear its bar reports 0 so the client hides it.
- Thread a new `community: dict | None = None` param through `score_candidate`; add
  `community_confidence` to the weighted sum (0.04) and to the constructed `ScoreBreakdown`.
- `to_recommendation(..., community=None)` attaches `_community_badge(...)`.
- `_why` gains a grounded bullet when `community_confidence >= 80` (`why_community`).
- `COMMUNITY_NEUTRAL=70` stays a fixed constant (must match `ScoreBreakdown`'s cold-start default).
  The four gates are **env-tunable** via `Settings` (`config.py`) so a deployment can adjust them
  without a rebuild: `COMMUNITY_MIN_RATERS=3`, `COMMUNITY_SHRINK_PRIOR=8`,
  `COMMUNITY_BADGE_MIN_RATERS=5`, `COMMUNITY_BADGE_MIN_VISITS=3`, `COMMUNITY_VISIT_WINDOW_DAYS=90`.
  Small/concentrated audiences can lower the gates to surface signal sooner.

## Workstream 3 — Schemas (`backend/app/schemas.py`)

- `ScoreBreakdown.community_confidence: int = 70` (neutral default, like `personal_preference_fit`).
- New `CommunitySignal{ liked_ratio, sample_size, recent_visits }`.
- `Recommendation.community: CommunitySignal | None = None`.

## Workstream 4 — Pipeline (`backend/app/services/recommendations.py`)

- After candidate selection, load `community = storage.community_signals([p.source_id for p in candidate_places])`.
- Thread `community` into all `score_candidate(...)` passes and the final `to_recommendation(...)`.

## Workstream 5 — i18n + frontend (`backend/app/services/i18n.py`, `frontend/app.js`, `frontend/styles.css`)

- Backend i18n: `why_community` (EN/RU).
- `app.js`: `bd_community_confidence` breakdown label (EN/RU) — the breakdown row then renders
  automatically from `Object.entries(score_breakdown)`. Add `badge_community_liked` /
  `badge_community_visits` (EN/RU). `communityBadgesHtml(item)` helper renders chips in the `.badges`
  block (secondary cards) and a `.community-strip` on the hero decision card.
- `styles.css`: `.badge.community` (green `--good` palette) + `.community-strip`.

## Workstream 6 — Tests (`backend/tests/test_community.py`)

- Aggregation: distinct-rater counting; configurable (default 90-day) visit window + UNION across both mark tables;
  empty input.
- Cold-start neutrality: no signal → `community_confidence == 70`, score unchanged, `rec.community is None`.
- Shrinkage: thin sample stays neutral; more votes move further from neutral; negative signal lowers score.
- Badge gating: below threshold → `None`; liked chip at ≥5 raters; visits chip at ≥3 visits.

## Verification

- `docker compose build app` then `docker compose run --rm --no-deps app sh -c "PYTHONPATH=. pytest -q"`
  (no source mount — rebuild before pytest).
- Frontend smoke via Playwright over the LAN IP, cache-busting the asset query: cold DB shows no
  badges and unchanged scores; after seeding 👍 + "mark visited" across two anonymous ids, the badge
  and the "Community" breakdown row appear for that place.

---

## Out of scope / next milestone

- **Community Tips** (authenticated users leave structured best-time / parking / kid-friendly notes
  after marking a place visited) — the natural follow-on this flywheel enables.
- **External signals** (Google Reviews / Komoot / Reddit) — stays far-horizon per the spec's legal caution.
- `events`-based crowd-*time* inference — weak signal; deferred.
