"""Objective metrics for one provider's output against the OSM+Google baseline.

Everything here is deterministic and side-effect free: it takes
`PlaceCandidate` lists (a provider's discovery pool and its final top-K, plus the
baseline's top-K) and returns numbers. "Same place" is resolved through
`matching.match_sets`, so a place found via a different provider id still counts
as agreement. These answer *did the provider find / rank the same places* — the
LLM-judge answers *is the difference actually worse*.
"""
from __future__ import annotations

import math
from typing import Hashable, Sequence

from app.schemas import PlaceCandidate
from app.services.scoring import normalize_interest, place_matches_interest
from eval.matching import match_sets


# --- set agreement vs baseline ------------------------------------------------

def recall_at_k(provider_topk: list[PlaceCandidate], baseline_topk: list[PlaceCandidate]) -> float:
    """Share of the baseline's top-K that the provider also surfaced in its top-K."""
    if not baseline_topk:
        return 0.0
    return match_sets(provider_topk, baseline_topk).n_matched / len(baseline_topk)


def jaccard(provider_topk: list[PlaceCandidate], baseline_topk: list[PlaceCandidate]) -> float:
    """|P ∩ B| / |P ∪ B| over the two top-K sets, under place matching."""
    matched = match_sets(provider_topk, baseline_topk).n_matched
    union = len(provider_topk) + len(baseline_topk) - matched
    return matched / union if union else 1.0


# --- rank agreement -----------------------------------------------------------

def rbo(a: Sequence[Hashable], b: Sequence[Hashable], p: float = 0.9) -> float:
    """Rank-Biased Overlap at depth k (non-extrapolated), top-weighted by p.

    Pure function over two ordered key sequences. Two identical length-k lists
    score 1 - p**k (a known property of the finite-depth form), disjoint lists
    score 0; more top-heavy overlap scores higher."""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    depth = max(len(a), len(b))
    total = 0.0
    for d in range(1, depth + 1):
        overlap = len(set(a[:d]) & set(b[:d]))
        total += (p ** (d - 1)) * (overlap / d)
    return (1 - p) * total


def _aligned_keys(
    provider: list[PlaceCandidate], baseline: list[PlaceCandidate]
) -> tuple[list[Hashable], list[Hashable]]:
    """Key sequences where a matched pair shares a key (the baseline's id), so an
    ordered-overlap metric treats "same place, different provider id" as equal."""
    result = match_sets(provider, baseline)
    provider_to_baseline = dict(result.pairs)
    provider_keys: list[Hashable] = [
        baseline[provider_to_baseline[i]].source_id if i in provider_to_baseline else f"__p{i}__"
        for i in range(len(provider))
    ]
    baseline_keys: list[Hashable] = [place.source_id for place in baseline]
    return provider_keys, baseline_keys


def rank_overlap(provider_topk: list[PlaceCandidate], baseline_topk: list[PlaceCandidate], p: float = 0.9) -> float:
    provider_keys, baseline_keys = _aligned_keys(provider_topk, baseline_topk)
    return rbo(provider_keys, baseline_keys, p)


def kendall_tau(provider_topk: list[PlaceCandidate], baseline_topk: list[PlaceCandidate]) -> float | None:
    """Kendall τ over the matched intersection; None when fewer than 3 pairs."""
    pairs = sorted(match_sets(provider_topk, baseline_topk).pairs)  # by provider rank
    if len(pairs) < 3:
        return None
    baseline_ranks = [j for _, j in pairs]
    concordant = discordant = 0
    for x in range(len(baseline_ranks)):
        for y in range(x + 1, len(baseline_ranks)):
            # provider ranks are ascending (pairs sorted), so order is set by baseline.
            if baseline_ranks[x] < baseline_ranks[y]:
                concordant += 1
            else:
                discordant += 1
    denom = concordant + discordant
    return (concordant - discordant) / denom if denom else None


# --- capability / metadata ----------------------------------------------------

def richness(candidates: list[PlaceCandidate]) -> dict[str, float]:
    """Shares of a list that carry rating / photo / a mapped (non-fallback) type /
    opening hours. The +Google arm is where ratings/photos should appear."""
    if not candidates:
        return {"rating": 0.0, "photo": 0.0, "mapped_category": 0.0, "opening_hours": 0.0}
    n = len(candidates)
    return {
        "rating": sum(c.rating is not None for c in candidates) / n,
        "photo": sum(c.google_photo_name is not None for c in candidates) / n,
        "mapped_category": sum(c.type != "place" for c in candidates) / n,
        "opening_hours": sum(bool(c.tags.get("opening_hours")) for c in candidates) / n,
    }


def interest_coverage(candidates: list[PlaceCandidate], interests: list[str]) -> float:
    """Share of the requested (non-'surprise me') interests at least one candidate
    can satisfy, using the same availability logic the scorer uses."""
    requested = [normalize_interest(str(i)) for i in interests]
    requested = [i for i in requested if i and i != "surprise me"]
    if not requested:
        return 1.0
    covered = sum(1 for i in requested if any(place_matches_interest(c, i) for c in candidates))
    return covered / len(requested)


def dedup_rate(candidates: list[PlaceCandidate]) -> float:
    """1 - clusters/n: the share of a provider's pool that is duplicated points."""
    n = len(candidates)
    if n <= 1:
        return 0.0
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    from eval.matching import same_place  # local import keeps the module's surface small
    for i in range(n):
        for j in range(i + 1, n):
            if same_place(candidates[i], candidates[j]):
                parent[find(i)] = find(j)
    clusters = len({find(i) for i in range(n)})
    return 1 - clusters / n


def density(n_candidates: int, radius_km: float) -> float:
    """Candidates per km² inside the search circle."""
    area = math.pi * radius_km * radius_km
    return n_candidates / area if area > 0 else 0.0


def percentile(samples: Sequence[float], q: float) -> float:
    """Nearest-rank percentile (q in 0..1); inf when there are no samples."""
    if not samples:
        return float("inf")
    ordered = sorted(samples)
    idx = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
    return ordered[idx]
