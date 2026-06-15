from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from pydantic import BaseModel, Field, validator

TransportMode = Literal["walk", "car", "bike"]
GroupType = Literal["solo", "couple", "family", "kids", "dog"]
Intensity = Literal["easy", "medium", "active"]
Lang = Literal["en", "ru"]


class AdventureRequest(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)
    available_minutes: int = Field(120, ge=30, le=720)
    transport_mode: TransportMode = "car"
    group_type: GroupType = "solo"
    children_ages: list[int] = Field(default_factory=list)
    # Refined group context (0.2). Separate from group_type so they compose.
    with_dog: bool = False
    with_elderly: bool = False
    reduced_mobility: bool = False
    intensity: Intensity = "easy"
    interests: list[str] = Field(default_factory=lambda: ["nature", "viewpoints"])
    max_walking_km: float | None = Field(default=None, ge=0, le=30)
    request_text: str | None = Field(default=None, max_length=1000)
    use_live_data: bool = True
    limit: int = Field(default=5, ge=1, le=10)
    lang: Lang = "en"
    # "Show others": rotate past places this user was already shown, surfacing
    # fresh candidates first (falls back to repeats only if none are left).
    exclude_seen: bool = False
    # Anonymous, client-generated id (localStorage). No accounts/PII; ties a
    # user's sessions/feedback together for history and personalization.
    anonymous_id: str | None = Field(default=None, max_length=64)

    @validator("interests", pre=True)
    def normalize_interests(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            value = [value]
        return [str(item).strip().lower() for item in value if str(item).strip()]


# Canonical interest ids — must match the data-interest values in
# frontend/index.html. Used by the UI, the parse whitelist and the LLM prompt.
INTEREST_IDS = {"history", "fortresses", "viewpoints", "nature", "water", "food", "drinks", "surprise me"}


class ParseTextRequest(BaseModel):
    text: str = Field(..., min_length=3, max_length=500)
    lang: Lang = "en"
    anonymous_id: str | None = Field(default=None, max_length=64)


class ParsedSituation(BaseModel):
    """Subset of AdventureRequest a parser may set. Every field optional:
    None means 'not mentioned' and the frontend keeps its default."""

    available_minutes: int | None = Field(default=None, ge=30, le=720)
    transport_mode: TransportMode | None = None
    group_type: GroupType | None = None
    children_ages: list[int] | None = None
    with_dog: bool | None = None
    with_elderly: bool | None = None
    reduced_mobility: bool | None = None
    intensity: Intensity | None = None
    interests: list[str] | None = None
    max_walking_km: float | None = Field(default=None, ge=0, le=30)

    @validator("children_ages")
    def keep_plausible_ages(cls, value: Any) -> list[int] | None:
        if value is None:
            return None
        ages = [int(v) for v in value if isinstance(v, (int, float)) and 0 <= int(v) <= 18]
        return ages or None

    @validator("interests", pre=True)
    def whitelist_interests(cls, value: Any) -> list[str] | None:
        if value is None:
            return None
        if isinstance(value, str):
            value = [value]
        kept = [str(v).strip().lower() for v in value if str(v).strip().lower() in INTEREST_IDS]
        return kept or None

    def is_empty(self) -> bool:
        return all(v is None for v in self.dict().values())


class WeatherSummary(BaseModel):
    source: str
    temperature_c: float | None = None
    rain_mm_now: float | None = None
    rain_mm_last_24h: float | None = None
    wind_kmh: float | None = None
    humidity_percent: float | None = None
    uv_index: float | None = None
    sunrise: str | None = None
    sunset: str | None = None
    summary: str
    score: int = Field(70, ge=0, le=100)
    confidence: Literal["live", "fallback", "estimated"] = "estimated"


class HourlyForecast(BaseModel):
    # One hour of the destination forecast, relative to when the request ran.
    time: str  # local clock time, "HH:MM"
    hour_offset: int  # whole hours from now (1, 2, 3, ...)
    label: str  # localized sky condition
    temperature_c: float | None = None
    precipitation_mm: float | None = None
    wind_kmh: float | None = None
    is_arrival: bool = False  # the hour the user is estimated to arrive


class PlaceCandidate(BaseModel):
    source: str
    source_id: str
    name: str
    type: str
    lat: float
    lon: float
    tags: dict[str, Any] = Field(default_factory=dict)
    estimated_activity_minutes: int = 45
    estimated_walking_km: float = 1.0
    difficulty: Literal["easy", "medium", "hard"] = "easy"
    quality_score: int = Field(default=60, ge=0, le=100)
    # Google Places enrichment (0.3); None when the place wasn't enriched.
    rating: float | None = Field(default=None, ge=0, le=5)
    rating_count: int | None = Field(default=None, ge=0)
    google_photo_name: str | None = None
    google_photo_attribution: str | None = None


class RouteInfo(BaseModel):
    source: str
    one_way_minutes: int
    round_trip_minutes: int
    distance_km: float
    map_url: str
    apple_map_url: str = ""
    confidence: Literal["live", "fallback", "estimated"] = "estimated"


class PlacePhoto(BaseModel):
    url: str
    source: str
    source_url: str | None = None
    attribution: str | None = None


class ScoreBreakdown(BaseModel):
    time_fit: int
    weather_fit: int
    distance_fit: int
    safety_fit: int
    group_fit: int
    interest_fit: int
    place_quality: int
    personal_preference_fit: int = 70  # neutral until the user has feedback history


class Recommendation(BaseModel):
    id: str
    # Canonical place id (e.g. "osm:node:123"); `id` is the DOM-safe mangled form.
    # Used to record "seen" and to let the client mark a place visited.
    source_id: str | None = None
    title: str
    place_type: str
    lat: float
    lon: float
    adventure_score: int
    score_breakdown: ScoreBreakdown
    total_minutes: int
    travel_minutes: int
    activity_minutes: int
    distance_km: float
    walking_km: float
    difficulty: str
    # Google rating; shown with mandatory "Google" attribution when present.
    rating: float | None = None
    rating_count: int | None = None
    description: str
    # summary + data_confidence_note are filled by the LLM layer when enabled and
    # grounded; otherwise they stay None and the rule-based `why` is used as-is.
    summary: str | None = None
    data_confidence_note: str | None = None
    why: list[str]
    warnings: list[str]
    map_url: str
    apple_map_url: str = ""
    source: str
    data_confidence: Literal["live", "mixed", "fallback", "estimated"] = "mixed"
    photo: PlacePhoto | None = None
    # Weather at the destination at the estimated arrival time, plus an hourly
    # timeline from now through travel and into the visit. Empty when live data
    # is off or the forecast call failed.
    arrival_weather: WeatherSummary | None = None
    forecast: list[HourlyForecast] = Field(default_factory=list)
    tags: dict[str, Any] = Field(default_factory=dict)


class RejectedAlternative(BaseModel):
    title: str
    reason: str
    score: int


class AdventureResponse(BaseModel):
    request_id: str
    generated_at: datetime
    version: str = "0.1.0"
    weather: WeatherSummary
    recommendations: list[Recommendation]
    rejected_alternatives: list[RejectedAlternative]
    data_warnings: list[str] = Field(default_factory=list)


FeedbackReason = Literal["too_far", "too_difficult", "bad_weather", "not_interesting", "inaccurate", "other"]


class FeedbackRequest(BaseModel):
    request_id: str
    recommendation_id: str
    rating: Literal["up", "down"]
    reason: FeedbackReason | None = None
    anonymous_id: str | None = Field(default=None, max_length=64)


AnalyticsEventName = Literal[
    "search_started",
    "search_completed",
    "recommendation_opened",
    "maps_opened",
    "feedback_submitted",
]


class AnalyticsEvent(BaseModel):
    event: AnalyticsEventName
    request_id: str | None = None
    recommendation_id: str | None = None
    anonymous_id: str | None = Field(default=None, max_length=64)
    meta: dict[str, Any] | None = None


class VisitedRequest(BaseModel):
    anonymous_id: str = Field(..., max_length=64)
    source_id: str = Field(..., max_length=200)
