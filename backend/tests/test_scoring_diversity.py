"""apply_diversity (v2): name-dedup + spatial gap + soft per-type cap, with a
graceful fallback that never returns fewer than a plain top-K. Uses lightweight
stand-ins — the function only reads .place.{name,type,lat,lon,quality_score} and
assumes the input is already score-ranked."""
from __future__ import annotations

from types import SimpleNamespace

from app.schemas import PlaceCandidate
from app.services.scoring import apply_diversity


def _sc(source_id: str, ptype: str, lat: float, lon: float, name: str | None = None, quality: int = 60):
    return SimpleNamespace(place=PlaceCandidate(
        source="osm", source_id=source_id, name=name or source_id, type=ptype,
        lat=lat, lon=lon, quality_score=quality))


# --- spatial gap (v1) --------------------------------------------------------

def test_diversity_breaks_a_same_type_cluster():
    # Five historic_sites within ~90 m (a memorial cluster) ahead of four distinct,
    # far-apart types. The cluster must not own the top-5.
    cluster = [_sc(f"h{i}", "historic_site", 51.5074 + i * 0.0002, -0.1278) for i in range(5)]
    others = [_sc("p1", "park", 51.52, -0.15), _sc("m1", "museum", 51.53, -0.10),
              _sc("w1", "water", 51.49, -0.09), _sc("f1", "food", 51.48, -0.16)]
    out = apply_diversity(cluster + others, limit=5, gap_m=400.0)

    head = out[:5]
    types = [c.place.type for c in head]
    assert types.count("historic_site") == 1                 # cluster collapsed to one slot
    assert {"park", "museum", "water", "food"} <= set(types)  # the varied types surfaced
    assert {c.place.source_id for c in out} == {c.place.source_id for c in cluster + others}


def test_diversity_keeps_spread_out_same_type():
    # Same type but ~6 km apart is not a cluster and within the cap -> order preserved.
    a = _sc("a", "historic_site", 51.50, -0.12)
    b = _sc("b", "historic_site", 51.55, -0.20)
    out = apply_diversity([a, b], limit=5, gap_m=400.0)
    assert [c.place.source_id for c in out[:2]] == ["a", "b"]


# --- name dedup (v2) ---------------------------------------------------------

def test_diversity_drops_duplicate_place_names():
    # Same place returned under two types (South Street Seaport as attr + hist) must
    # not appear twice in the head — the v1 regression this fixes. A full pool so the
    # head is really the picked set (not tail bleed-through).
    a = _sc("a1", "attraction", 40.0, -74.0, name="South Street Seaport")
    b = _sc("b1", "historic_site", 40.0001, -74.0001, name="South Street Seaport")  # dup name
    fillers = [_sc("f1", "park", 40.1, -74.1, name="Battery Park"),
               _sc("f2", "museum", 40.2, -74.2, name="MoMA"),
               _sc("f3", "food", 40.3, -74.3, name="Katz's"),
               _sc("f4", "water", 40.4, -74.4, name="Hudson")]
    head = apply_diversity([a, b] + fillers, limit=5)[:5]
    assert [x.place.name for x in head].count("South Street Seaport") == 1
    assert "b1" not in [x.place.source_id for x in head]  # the duplicate specifically excluded


# --- soft per-type cap (v2) --------------------------------------------------

def test_diversity_caps_type_when_alternatives_exist():
    # Five far-apart historic + five other types: cap historic at 2, surface variety.
    hist = [_sc(f"h{i}", "historic_site", 40.0 + i * 0.02, -74.0) for i in range(5)]
    others = [_sc("m1", "museum", 40.2, -74.2), _sc("p1", "park", 40.3, -74.3),
              _sc("w1", "water", 40.4, -74.4), _sc("f1", "food", 40.5, -74.5),
              _sc("d1", "drinks", 40.6, -74.6)]
    head = apply_diversity(hist + others, limit=5)[:5]
    assert [x.place.type for x in head].count("historic_site") == 2  # capped, not 5


def test_diversity_soft_cap_fills_single_theme_town():
    # Kotor-like: the whole offering is the historic old town. The cap must relax
    # (no random cafe forced in) and still fill `limit`.
    town = [_sc(f"h{i}", "historic_site", 42.4247 + i * 0.0003, 18.7712) for i in range(8)]
    head = apply_diversity(town, limit=5)[:5]
    assert len(head) == 5
    assert all(x.place.type == "historic_site" for x in head)


# --- notability-aware fill (v2) ----------------------------------------------

def test_diversity_fill_prefers_notability():
    # When a cluster frees a slot, the fill takes the higher-quality (notable) one.
    a = _sc("a", "historic_site", 40.0, -74.0, quality=90)
    b = _sc("b", "historic_site", 40.0005, -74.0, quality=88)
    c = _sc("c", "historic_site", 40.0006, -74.0, quality=70)
    head = apply_diversity([a, b, c], limit=2)[:2]
    assert [x.place.source_id for x in head] == ["a", "b"]  # b (notable) filled before c


def test_diversity_holds_off_interest_types_for_narrow_requests():
    # A food+drinks trip must not get a war memorial injected just for variety while
    # on-interest places remain (the v2 drinks-food regression this fixes).
    food = [_sc(f"f{i}", "food", 40.0 + i * 0.02, -74.0, name=f"Cafe {i}") for i in range(4)]
    drink = [_sc(f"d{i}", "drinks", 41.0 + i * 0.02, -74.0, name=f"Bar {i}") for i in range(2)]
    monument = _sc("m1", "historic_site", 42.0, -74.0, name="War Memorial", quality=95)
    head = apply_diversity(food + drink + [monument], limit=5, interests=["food", "drinks"])[:5]
    assert all(x.place.type in {"food", "drinks"} for x in head)  # off-interest monument held out
    # graceful: with too few on-interest places, off-interest still fills the slot
    out = apply_diversity([food[0], monument], limit=5, interests=["food", "drinks"])
    assert monument in out[:2]


def test_diversity_noop_on_trivial_input():
    assert apply_diversity([], limit=5) == []
    one = [_sc("x", "park", 51.5, -0.1)]
    assert apply_diversity(one, limit=5) == one
