from app.schemas import AdventureRequest, PlaceCandidate, RouteInfo, WeatherSummary
from app.services.scoring import score_candidate, to_recommendation


def test_family_walk_limit_penalizes_hard_long_route():
    request = AdventureRequest(
        lat=42.43,
        lon=18.69,
        available_minutes=300,
        transport_mode="car",
        group_type="family",
        children_ages=[6, 13],
        interests=["history", "fortresses"],
        max_walking_km=3,
    )
    weather = WeatherSummary(source="test", summary="clear", score=90, confidence="estimated")
    place = PlaceCandidate(
        source="test",
        source_id="test:hard",
        name="Hard Fortress",
        type="fortress",
        lat=42.45,
        lon=18.70,
        estimated_activity_minutes=120,
        estimated_walking_km=4.5,
        difficulty="hard",
        quality_score=90,
    )
    route = RouteInfo(
        source="test",
        one_way_minutes=20,
        round_trip_minutes=40,
        distance_km=12,
        map_url="https://example.com",
        confidence="estimated",
    )
    scored = score_candidate(place, route, weather, request)
    assert scored.breakdown.group_fit < 70
    assert scored.breakdown.safety_fit < 80
    assert scored.score < 90
    assert scored.warnings


def test_good_short_match_scores_high():
    request = AdventureRequest(
        lat=42.43,
        lon=18.69,
        available_minutes=300,
        transport_mode="car",
        group_type="family",
        children_ages=[6, 13],
        interests=["history", "fortresses", "viewpoints"],
        max_walking_km=3,
    )
    weather = WeatherSummary(source="test", summary="clear", temperature_c=23, score=92, confidence="estimated")
    place = PlaceCandidate(
        source="test",
        source_id="test:easy",
        name="Easy Fortress",
        type="fortress",
        lat=42.45,
        lon=18.70,
        estimated_activity_minutes=80,
        estimated_walking_km=1.8,
        difficulty="easy",
        quality_score=85,
    )
    route = RouteInfo(
        source="test",
        one_way_minutes=18,
        round_trip_minutes=36,
        distance_km=10,
        map_url="https://example.com",
        confidence="estimated",
    )
    scored = score_candidate(place, route, weather, request)
    assert scored.score >= 80
    assert scored.breakdown.interest_fit >= 80
    assert scored.breakdown.time_fit >= 90


def test_primary_interest_breaks_food_history_tie():
    request = AdventureRequest(
        lat=42.43,
        lon=18.69,
        available_minutes=240,
        transport_mode="car",
        interests=["food", "history"],
    )
    weather = WeatherSummary(source="test", summary="clear", score=90, confidence="estimated")
    route = RouteInfo(source="test", one_way_minutes=15, round_trip_minutes=30, distance_km=8, map_url="x", confidence="estimated")
    food = PlaceCandidate(
        source="test",
        source_id="test:food",
        name="Waterfront Cafes",
        type="food",
        lat=42.43,
        lon=18.69,
        tags={"interests": ["food"]},
        quality_score=80,
    )
    fortress = PlaceCandidate(
        source="test",
        source_id="test:fort",
        name="Fortress",
        type="fortress",
        lat=42.43,
        lon=18.69,
        tags={"interests": ["history", "fortresses"]},
        quality_score=80,
    )

    food_score = score_candidate(food, route, weather, request)
    fortress_score = score_candidate(fortress, route, weather, request)

    assert food_score.breakdown.interest_fit > fortress_score.breakdown.interest_fit


def test_reduced_mobility_penalizes_hard_long_route():
    base = dict(lat=42.43, lon=18.69, available_minutes=300, transport_mode="car", interests=["history"])
    weather = WeatherSummary(source="test", summary="clear", score=90, confidence="estimated")
    place = PlaceCandidate(
        source="test",
        source_id="test:steep",
        name="Steep Hill",
        type="viewpoint",
        lat=42.45,
        lon=18.70,
        estimated_activity_minutes=60,
        estimated_walking_km=3.5,
        difficulty="hard",
        quality_score=80,
    )
    route = RouteInfo(source="test", one_way_minutes=15, round_trip_minutes=30, distance_km=10, map_url="x", confidence="estimated")
    normal = score_candidate(place, route, weather, AdventureRequest(**base))
    reduced = score_candidate(place, route, weather, AdventureRequest(**base, reduced_mobility=True))
    assert reduced.breakdown.group_fit < normal.breakdown.group_fit
    assert reduced.breakdown.safety_fit < normal.breakdown.safety_fit
    assert any("mobility" in w.lower() for w in reduced.warnings)


def test_to_recommendation_carries_source_id():
    place = PlaceCandidate(source="osm", source_id="osm:node:42", name="Lookout", type="viewpoint", lat=42.4, lon=18.7)
    route = RouteInfo(source="test", one_way_minutes=10, round_trip_minutes=20, distance_km=5, map_url="x", confidence="estimated")
    weather = WeatherSummary(source="test", summary="clear", score=90, confidence="estimated")
    scored = score_candidate(place, route, weather, AdventureRequest(lat=42.4, lon=18.7))
    rec = to_recommendation(scored)
    assert rec.source_id == "osm:node:42"  # canonical id for seen/visited tracking
    assert rec.id == "osm_node_42"  # DOM-safe mangled id unchanged


def test_personal_preference_fit_shifts_with_history():
    request = AdventureRequest(lat=42.43, lon=18.69, available_minutes=300, transport_mode="car", interests=["history", "fortresses"])
    weather = WeatherSummary(source="test", summary="clear", score=90, confidence="estimated")
    place = PlaceCandidate(
        source="test",
        source_id="test:fort",
        name="Fort",
        type="fortress",
        lat=42.45,
        lon=18.70,
        estimated_activity_minutes=80,
        estimated_walking_km=1.8,
        difficulty="easy",
        quality_score=85,
    )
    route = RouteInfo(source="test", one_way_minutes=18, round_trip_minutes=36, distance_km=10, map_url="x", confidence="estimated")
    cold = score_candidate(place, route, weather, request)
    assert cold.breakdown.personal_preference_fit == 70  # neutral on cold start
    disliked = score_candidate(place, route, weather, request, {"place_types": {"fortress": -2}})
    liked = score_candidate(place, route, weather, request, {"place_types": {"fortress": 2}})
    assert disliked.breakdown.personal_preference_fit < 70 < liked.breakdown.personal_preference_fit
    assert disliked.score < cold.score < liked.score
