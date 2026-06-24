"""Place-matching thresholds: the same POI seen via two providers must match,
two genuinely different POIs must not. These fixtures pin the gates so a later
threshold tweak can't silently inflate (or sink) the benchmark's recall."""
from __future__ import annotations

from app.schemas import PlaceCandidate
from eval.matching import match_sets, name_similarity, normalize_name, same_place

# 1 degree of latitude ~= 111_195 m, so these offsets give known distances.
M = 1.0 / 111_195.0
BASE_LAT, BASE_LON = 51.5, -0.1


def place(name: str, *, dmeters: float = 0.0, ptype: str = "attraction", **kw) -> PlaceCandidate:
    return PlaceCandidate(
        source=kw.get("source", "test"),
        source_id=kw.get("source_id", name),
        name=name,
        type=ptype,
        lat=BASE_LAT + dmeters * M,
        lon=BASE_LON,
        rating=kw.get("rating"),
        rating_count=kw.get("rating_count"),
        tags=kw.get("tags", {}),
    )


def test_normalize_name_strips_case_punctuation_diacritics_stopwords():
    assert normalize_name("The Tower of London!") == "tower london"
    assert normalize_name("Café René") == "cafe rene"
    assert normalize_name("  ") == ""


def test_name_similarity_handles_reordering_and_typos():
    assert name_similarity("Tower of London", "the tower of london") == 1.0
    assert name_similarity("British Museum", "Museum British") == 1.0  # token set
    assert name_similarity("Hyde Park", "Greenwich Park") < 0.82
    assert name_similarity("anything", "") == 0.0


def test_same_place_matches_close_names_within_gate():
    a = place("Tower of London", dmeters=0)
    b = place("the Tower of London", dmeters=30)  # 30 m apart, same name
    assert same_place(a, b)


def test_same_place_rejects_far_different_pois():
    a = place("Cafe Rouge", dmeters=0, ptype="food")
    b = place("Blue Bottle", dmeters=200, ptype="food")  # 200 m > 75 m gate
    assert not same_place(a, b)


def test_same_place_colocated_shortcut_ignores_name():
    a = place("X", dmeters=0)
    b = place("Y", dmeters=5)  # 5 m apart, different names -> still the same spot
    assert same_place(a, b)


def test_same_place_loose_gate_still_bounded_for_large_footprints():
    # Parks get the 150 m gate, but 500 m apart is still two different parks.
    a = place("Hyde Park", dmeters=0, ptype="park")
    b = place("Hyde Park", dmeters=500, ptype="park")
    assert not same_place(a, b)
    # ...whereas 120 m apart (inside the loose gate, same name) does match.
    c = place("Hyde Park", dmeters=120, ptype="park")
    assert same_place(a, c)


def test_match_sets_is_greedy_one_to_one_by_distance():
    provider = [
        place("Big Ben", dmeters=10, source_id="p1"),   # closest to baseline B
        place("Big Ben", dmeters=40, source_id="p2"),   # also matches B, but farther
        place("Nowhere", dmeters=5000, source_id="p3"),  # matches nothing
    ]
    baseline = [place("Big Ben", dmeters=0, source_id="b1")]
    result = match_sets(provider, baseline)
    assert result.pairs == [(0, 0)]          # nearest provider wins the single baseline
    assert result.matched_provider == {0}
    assert result.matched_baseline == {0}
    assert result.n_matched == 1
