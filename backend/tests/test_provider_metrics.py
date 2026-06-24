"""Known-answer checks for the objective provider metrics. Tiny hand-built sets
so each number is verifiable by hand, pinning the formulas (recall / Jaccard /
RBO / Kendall / richness / coverage / dedup / density / percentile)."""
from __future__ import annotations

import math

import pytest

from app.schemas import PlaceCandidate
from eval import provider_metrics as pm

M = 1.0 / 111_195.0
LAT, LON = 40.0, -3.0


def place(sid: str, *, dmeters: float, name: str = "", ptype: str = "attraction", **kw) -> PlaceCandidate:
    return PlaceCandidate(
        source="test",
        source_id=sid,
        name=name or sid,
        type=ptype,
        lat=LAT + dmeters * M,
        lon=LON,
        rating=kw.get("rating"),
        rating_count=kw.get("rating_count"),
        google_photo_name=kw.get("photo"),
        tags=kw.get("tags", {}),
    )


def test_recall_and_jaccard_under_matching():
    # provider A* sits ~5 m from baseline A (co-located -> same place); B/C differ.
    provider = [place("pA", dmeters=5), place("pB", dmeters=2000)]
    baseline = [place("bA", dmeters=0), place("bC", dmeters=4000)]
    assert pm.recall_at_k(provider, baseline) == pytest.approx(0.5)   # 1 of 2 baseline found
    assert pm.jaccard(provider, baseline) == pytest.approx(1 / 3)     # 1 / (2 + 2 - 1)


def test_recall_empty_baseline_is_zero():
    assert pm.recall_at_k([place("p", dmeters=0)], []) == 0.0


def test_rbo_identity_disjoint_and_monotonic():
    assert pm.rbo(["a", "b", "c"], ["a", "b", "c"], p=0.9) == pytest.approx(1 - 0.9 ** 3)
    assert pm.rbo(["a", "b"], ["x", "y"], p=0.9) == 0.0
    assert pm.rbo([], [], p=0.9) == 1.0
    # Same items, top swapped -> less overlap than identical.
    assert pm.rbo(["b", "a", "c"], ["a", "b", "c"], p=0.9) < pm.rbo(["a", "b", "c"], ["a", "b", "c"], p=0.9)


def test_rank_overlap_perfect_when_same_order():
    provider = [place("p1", dmeters=0), place("p2", dmeters=1000), place("p3", dmeters=2000)]
    baseline = [place("b1", dmeters=2), place("b2", dmeters=1002), place("b3", dmeters=2002)]
    # each provider place co-locates with the baseline place at the same rank.
    assert pm.rank_overlap(provider, baseline, p=0.9) == pytest.approx(1 - 0.9 ** 3)


def test_kendall_tau_order():
    p = [place("p1", dmeters=0), place("p2", dmeters=1000), place("p3", dmeters=2000)]
    same = [place("b1", dmeters=2), place("b2", dmeters=1002), place("b3", dmeters=2002)]
    rev = [place("b3", dmeters=2002), place("b2", dmeters=1002), place("b1", dmeters=2)]
    assert pm.kendall_tau(p, same) == pytest.approx(1.0)
    assert pm.kendall_tau(p, rev) == pytest.approx(-1.0)
    assert pm.kendall_tau(p[:2], same[:2]) is None  # < 3 matched -> undefined


def test_richness_counts_present_fields():
    cands = [
        place("a", dmeters=0, ptype="museum", rating=4.5, photo="places/x/photos/y", tags={"opening_hours": "Mo-Su"}),
        place("b", dmeters=10, ptype="place"),  # fallback type, no rating/photo/hours
    ]
    r = pm.richness(cands)
    assert r["rating"] == pytest.approx(0.5)
    assert r["photo"] == pytest.approx(0.5)
    assert r["mapped_category"] == pytest.approx(0.5)
    assert r["opening_hours"] == pytest.approx(0.5)
    assert pm.richness([]) == {"rating": 0.0, "photo": 0.0, "mapped_category": 0.0, "opening_hours": 0.0}


def test_interest_coverage_uses_place_interests():
    cands = [place("a", dmeters=0, ptype="museum"), place("b", dmeters=10, ptype="park")]
    # museum -> {history, family}; park -> {nature, family}
    assert pm.interest_coverage(cands, ["history", "nature"]) == pytest.approx(1.0)
    assert pm.interest_coverage(cands, ["history", "drinks"]) == pytest.approx(0.5)
    assert pm.interest_coverage(cands, ["surprise me"]) == pytest.approx(1.0)


def test_dedup_rate_collapses_duplicate_points():
    cands = [place("a", dmeters=0), place("a2", dmeters=5), place("c", dmeters=3000)]
    # a and a2 are the same point -> 2 clusters of 3 -> 1 - 2/3.
    assert pm.dedup_rate(cands) == pytest.approx(1 / 3)
    assert pm.dedup_rate([place("x", dmeters=0)]) == 0.0


def test_density_and_percentile():
    assert pm.density(10, 2.0) == pytest.approx(10 / (math.pi * 4))
    assert pm.density(5, 0.0) == 0.0
    assert pm.percentile([10, 20, 30, 40], 0.5) == 30
    assert pm.percentile([], 0.5) == float("inf")
