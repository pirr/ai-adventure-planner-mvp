import asyncio
from datetime import datetime, timedelta

from app.services.weather import DestinationForecast, _now_index, get_destination_forecasts


def _hours(n: int = 30) -> list[str]:
    base = datetime(2026, 6, 6, 0, 0)
    return [(base + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M") for i in range(n)]


def _forecast(precip: list[float], codes: list[int] | None = None, now_index: int = 5) -> DestinationForecast:
    times = _hours()
    n = len(times)
    return DestinationForecast(
        times=times,
        temps=[20.0] * n,
        precip=precip,
        winds=[10.0] * n,
        uvs=[3.0] * n,
        codes=codes if codes is not None else [0] * n,
        sunsets=["2026-06-06T20:30", "2026-06-07T20:31"],
        now_index=now_index,
    )


def test_timeline_marks_arrival_hour():
    # now is 05:00 (index 5); a 2 h drive arrives at 07:00 (offset 2).
    arrival_weather, timeline = _forecast([0.0] * 30).at_arrival(one_way_minutes=120, activity_minutes=60, lang="en")
    assert [h.hour_offset for h in timeline] == [1, 2, 3]
    arrival = [h for h in timeline if h.is_arrival]
    assert len(arrival) == 1
    assert arrival[0].hour_offset == 2
    assert arrival[0].time == "07:00"
    assert arrival_weather.confidence == "live"


def test_timeline_capped_at_six_hours():
    _, timeline = _forecast([0.0] * 30).at_arrival(one_way_minutes=240, activity_minutes=600, lang="en")
    assert len(timeline) == 6
    assert timeline[-1].hour_offset == 6


def test_rainy_arrival_scores_lower_than_clear():
    clear_weather, _ = _forecast([0.0] * 30).at_arrival(120, 60, "en")
    rainy_precip = [0.0] * 30
    rainy_precip[7] = 6.0  # heavy rain at the arrival hour (index 5 + 2)
    rainy_codes = [0] * 30
    rainy_codes[7] = 65
    rainy_weather, _ = _forecast(rainy_precip, rainy_codes).at_arrival(120, 60, "en")
    assert rainy_weather.score < clear_weather.score
    assert rainy_weather.rain_mm_now == 6.0


def test_rain_window_sums_last_24_hours():
    precip = [0.0] * 30
    precip[3] = 2.0
    precip[6] = 3.0
    precip[7] = 1.0  # arrival hour
    arrival_weather, _ = _forecast(precip).at_arrival(120, 60, "en")
    # window is precip[0:8] -> 2 + 3 + 1
    assert arrival_weather.rain_mm_last_24h == 6.0


def test_now_index_matches_current_hour():
    times = _hours()
    assert _now_index(times, "2026-06-06T05:30") == 5
    assert _now_index(times, None) == 0


def test_forecasts_disabled_returns_none_without_network():
    points = [(42.0, 18.0), (42.1, 18.1)]
    assert asyncio.run(get_destination_forecasts(points, use_live_data=False)) == [None, None]
    assert asyncio.run(get_destination_forecasts([], use_live_data=True)) == []
