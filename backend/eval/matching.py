"""Decide when two providers' places are "the same place".

Providers give the same POI different ids, names and slightly different
coordinates, so overlap/recall against the OSM+Google baseline can't compare
`source_id`s directly. `same_place` pairs by a spatial gate plus normalized-name
similarity (with a co-located shortcut for renamed/abbreviated duplicates), and
`match_sets` resolves a one-to-one alignment greedily, nearest pair first.

Thresholds are module constants on purpose: they're pinned by
`tests/test_provider_matching.py` so a tweak can't silently move the benchmark.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field

from app.schemas import PlaceCandidate
from app.services.geo import haversine_km

# Spatial gate (metres). Point POIs use the tight gate; large-footprint types
# have centroids that drift between providers, so they get a looser one.
DEFAULT_GATE_M = 75.0
LOOSE_GATE_M = 150.0
LOOSE_TYPES = {"park", "water", "fortress", "historic_site"}
# Names at least this similar (0..1) count as the same place inside the gate.
NAME_SIM_THRESHOLD = 0.82
# Inside this distance, treat as the same place regardless of name (a renamed or
# abbreviated identical POI, or one provider returning no name at all).
COLOCATED_M = 25.0

# A few articles/prepositions that add no identity (EN + a couple of RU forms).
_STOPWORDS = {"the", "of", "at", "a", "an", "im", "imeni"}


def normalize_name(name: str) -> str:
    """Lowercase, strip diacritics + punctuation, drop stopwords, collapse space."""
    decomposed = unicodedata.normalize("NFKD", name or "")
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    cleaned = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in stripped.lower())
    tokens = [tok for tok in cleaned.split() if tok and tok not in _STOPWORDS]
    return " ".join(tokens)


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def name_similarity(a: str, b: str) -> float:
    """0..1 similarity: max of token-set Jaccard and normalized edit distance.

    Jaccard catches reordering ("Museum British"); edit distance catches typos /
    minor spelling drift. Both run on the normalized forms."""
    na, nb = normalize_name(a), normalize_name(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    ta, tb = set(na.split()), set(nb.split())
    jaccard = len(ta & tb) / len(ta | tb) if (ta | tb) else 0.0
    edit = 1 - _levenshtein(na, nb) / max(len(na), len(nb))
    return max(jaccard, edit)


def _gate_m(a: PlaceCandidate, b: PlaceCandidate) -> float:
    return LOOSE_GATE_M if (a.type in LOOSE_TYPES or b.type in LOOSE_TYPES) else DEFAULT_GATE_M


def same_place(a: PlaceCandidate, b: PlaceCandidate) -> bool:
    distance_m = haversine_km(a.lat, a.lon, b.lat, b.lon) * 1000
    if distance_m > _gate_m(a, b):
        return False
    if distance_m <= COLOCATED_M:
        return True
    return name_similarity(a.name, b.name) >= NAME_SIM_THRESHOLD


@dataclass
class MatchResult:
    """Alignment between a provider's places and the baseline's."""

    pairs: list[tuple[int, int]] = field(default_factory=list)  # (provider_idx, baseline_idx)
    matched_provider: set[int] = field(default_factory=set)
    matched_baseline: set[int] = field(default_factory=set)

    @property
    def n_matched(self) -> int:
        return len(self.pairs)


def match_sets(provider: list[PlaceCandidate], baseline: list[PlaceCandidate]) -> MatchResult:
    """Greedy one-to-one matching, nearest candidate pair first.

    Deterministic: all `same_place` pairs are sorted by distance (ties broken by
    index) and consumed in order, each place used at most once."""
    candidates: list[tuple[float, int, int]] = []
    for i, p in enumerate(provider):
        for j, b in enumerate(baseline):
            if same_place(p, b):
                candidates.append((haversine_km(p.lat, p.lon, b.lat, b.lon), i, j))
    candidates.sort()

    result = MatchResult()
    for _, i, j in candidates:
        if i in result.matched_provider or j in result.matched_baseline:
            continue
        result.pairs.append((i, j))
        result.matched_provider.add(i)
        result.matched_baseline.add(j)
    return result
