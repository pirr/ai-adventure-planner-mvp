from app.schemas import AdventureRequest, PlaceCandidate, RouteInfo, WeatherSummary
from app.services.scoring import score_candidate


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
