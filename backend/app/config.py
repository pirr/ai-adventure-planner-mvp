from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _env_bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).lower() == "true"


def _env_float_tuple(name: str, default: str) -> tuple[float, ...]:
    values: list[float] = []
    for raw in os.getenv(name, default).split(","):
        raw = raw.strip()
        if not raw:
            continue
        values.append(float(raw))
    return tuple(values)


@dataclass(frozen=True)
class Settings:
    app_name: str = "Adventure Planner MVP"
    version: str = "0.2"
    # Sent as the User-Agent on every outbound API call. Public providers such as
    # overpass-api.de reject default library agents ("python-httpx/*") with HTTP
    # 406, so identify the app. Add contact info (URL/email) for production use.
    user_agent: str = os.getenv("USER_AGENT", "ai-adventure-planner-mvp/0.1")
    data_dir: Path = Path(os.getenv("DATA_DIR", "./data"))
    sqlite_path: Path = Path(os.getenv("SQLITE_PATH", "./data/adventures.db"))
    overpass_url: str = os.getenv("OVERPASS_URL", "https://overpass-api.de/api/interpreter")
    # Optional fallback Overpass mirrors, tried (in order) when the primary is
    # busy (502/503/504). Comma-separated, OVERPASS_MIRRORS. Empty by default:
    # only set mirrors your network can reach quickly, since an unreachable one
    # costs a full timeout before falling back to sample places.
    overpass_mirrors: tuple[str, ...] = tuple(
        url.strip() for url in os.getenv("OVERPASS_MIRRORS", "").split(",") if url.strip()
    )
    # Per-endpoint timeout for Overpass. The query asks the server for up to 10s
    # ([timeout:10]); allow a little more so real results aren't dropped, while
    # still bounding a stuck request.
    overpass_timeout_seconds: float = float(os.getenv("OVERPASS_TIMEOUT_SECONDS", "12"))
    overpass_max_attempts: int = int(os.getenv("OVERPASS_MAX_ATTEMPTS", "2"))
    overpass_retry_backoff_seconds: float = float(os.getenv("OVERPASS_RETRY_BACKOFF_SECONDS", "0.5"))
    # Search latency controls. Progressive radius keeps dense-area Overpass
    # calls small first, then expands only when the early rings are weak.
    search_progressive_enabled: bool = _env_bool("SEARCH_PROGRESSIVE_ENABLED", True)
    search_radius_tiers_km: tuple[float, ...] = _env_float_tuple("SEARCH_RADIUS_TIERS_KM", "8,25,55")
    search_osm_target_candidates: int = int(os.getenv("SEARCH_OSM_TARGET_CANDIDATES", "32"))
    # Raw OSM candidates are safe to cache briefly; final recommendations still
    # use fresh weather, routing, personalization, seen/visited state and LLM.
    search_candidate_cache_ttl_seconds: int = int(os.getenv("SEARCH_CANDIDATE_CACHE_TTL_SECONDS", "21600"))
    search_candidate_cache_max_entries: int = int(os.getenv("SEARCH_CANDIDATE_CACHE_MAX_ENTRIES", "256"))
    # Number of cheap-pre-ranked candidates sent to live routing. Keep this
    # comfortably above the max result limit so quality/diversity survive.
    search_route_candidate_limit: int = int(os.getenv("SEARCH_ROUTE_CANDIDATE_LIMIT", "32"))
    osrm_url: str = os.getenv("OSRM_URL", "https://router.project-osrm.org")
    openweather_api_key: str | None = os.getenv("OPENWEATHER_API_KEY")
    use_open_meteo_fallback: bool = os.getenv("USE_OPEN_METEO_FALLBACK", "true").lower() == "true"
    http_timeout_seconds: float = float(os.getenv("HTTP_TIMEOUT_SECONDS", "8"))
    # App log level (INFO by default). Set DEBUG for verbose LLM/HTTP logs.
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    # Cross-origin policy. The frontend is served same-origin by this backend,
    # so the safe default is an empty allow-list (no cross-origin access). Set
    # ALLOWED_ORIGINS to a comma-separated list of full origins only if a
    # separate frontend host needs to call the API.
    allowed_origins: tuple[str, ...] = tuple(
        o.strip() for o in os.getenv("ALLOWED_ORIGINS", "").split(",") if o.strip()
    )
    # --- HTTP rate limiting (per client IP, slowapi) ---
    # Abuse backstop in front of the per-feature daily budgets. Disable in tests
    # via RATE_LIMIT_ENABLED=false. Limit strings use slowapi syntax; combine
    # windows with ';' (e.g. "10/minute;100/day").
    rate_limit_enabled: bool = os.getenv("RATE_LIMIT_ENABLED", "true").lower() == "true"
    # Global default applied to every route; loose enough for static assets and
    # the health check, tight enough to stop a flood from one IP.
    rate_limit_default: str = os.getenv("RATE_LIMIT_DEFAULT", "120/minute")
    # Heaviest endpoint: external APIs + Google Places + LLM per call.
    rate_limit_recommendations: str = os.getenv("RATE_LIMIT_RECOMMENDATIONS", "10/minute;100/day")
    # LLM free-text parse: bound paid model calls per IP.
    rate_limit_parse: str = os.getenv("RATE_LIMIT_PARSE", "5/minute;30/day")
    # Account registration/login/OAuth entrypoints.
    rate_limit_auth: str = os.getenv("RATE_LIMIT_AUTH", "5/minute;50/day")
    # Optional growth gate: anonymous users can receive the first recommendation
    # batch, but must register/sign in before requesting rotated "more" results.
    require_auth_for_more_recommendations: bool = _env_bool("REQUIRE_AUTH_FOR_MORE_RECOMMENDATIONS", False)
    # --- Authentication ---
    auth_session_cookie_name: str = os.getenv("AUTH_SESSION_COOKIE_NAME", "ap_session")
    auth_session_days: int = int(os.getenv("AUTH_SESSION_DAYS", "30"))
    auth_cookie_secure: bool = _env_bool("AUTH_COOKIE_SECURE", False)
    auth_cookie_samesite: str = os.getenv("AUTH_COOKIE_SAMESITE", "lax")
    auth_password_min_length: int = int(os.getenv("AUTH_PASSWORD_MIN_LENGTH", "8"))
    google_oauth_client_id: str | None = os.getenv("GOOGLE_OAUTH_CLIENT_ID") or None
    google_oauth_client_secret: str | None = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET") or None
    google_oauth_redirect_uri: str | None = os.getenv("GOOGLE_OAUTH_REDIRECT_URI") or None
    google_oauth_authorization_url: str = os.getenv(
        "GOOGLE_OAUTH_AUTHORIZATION_URL",
        "https://accounts.google.com/o/oauth2/v2/auth",
    )
    google_oauth_token_url: str = os.getenv("GOOGLE_OAUTH_TOKEN_URL", "https://oauth2.googleapis.com/token")
    # LLM explanation layer. Provider-agnostic over the OpenAI /v1 chat API.
    # Default "template" keeps the rule-based explanations and makes no network
    # call. Set LLM_PROVIDER to a preset (openai/llamacpp/ollama/deepseek/groq/
    # openrouter) or "openai" with an explicit LLM_BASE_URL.
    llm_provider: str = os.getenv("LLM_PROVIDER", "template")
    llm_enabled: bool = os.getenv("LLM_ENABLED", "true").lower() == "true"
    llm_base_url: str | None = os.getenv("LLM_BASE_URL")
    llm_model: str = os.getenv("LLM_MODEL", "gpt-4o-mini")
    llm_api_key: str | None = os.getenv("LLM_API_KEY")
    llm_timeout_seconds: float = float(os.getenv("LLM_TIMEOUT_SECONDS", "20"))
    llm_max_explained: int = int(os.getenv("LLM_MAX_EXPLAINED", "5"))
    llm_fallback_models: tuple[str] = tuple(os.getenv("LLM_FALLBACK_MODELS", "").split(","))
    gemini_reasoning_effort: str | None = os.getenv("GEMINI_REASONING_EFFORT", "")
    # Free-text situation parsing ("Describe your trip"). Requires a real LLM
    # provider; with the TemplateProvider the feature reports disabled
    # regardless of this flag.
    llm_parse_enabled: bool = os.getenv("LLM_PARSE_ENABLED", "true").lower() == "true"
    # App-side daily budgets for parse calls (same pattern as Google
    # enrichment). 0 disables the feature. Global is the real backstop:
    # anonymous_id is client-supplied.
    llm_parse_daily_limit: int = int(os.getenv("LLM_PARSE_DAILY_LIMIT", "500"))
    llm_parse_user_daily_limit: int = int(os.getenv("LLM_PARSE_USER_DAILY_LIMIT", "30"))
    # A/B test the LLM explanations vs templates: when on (and an LLM is
    # configured), bucket users by anonymous_id — half see templates as control.
    ab_test_enabled: bool = os.getenv("AB_TEST_ENABLED", "false").lower() == "true"
    # Optional Google Places enrichment (0.3). Off unless an API key is set:
    # without a key the recommendation pipeline is unchanged (OSM-only quality
    # and photos). The key stays backend-only — never shipped to the browser.
    google_places_api_key: str | None = os.getenv("GOOGLE_PLACES_API_KEY") or None
    google_places_url: str = os.getenv("GOOGLE_PLACES_URL", "https://places.googleapis.com/v1")
    google_places_timeout_seconds: float = float(os.getenv("GOOGLE_PLACES_TIMEOUT_SECONDS", "5"))
    # In-process cache TTL per place. Google ToS allows caching most fields up
    # to 30 days; keep the default well inside that.
    google_places_cache_ttl_seconds: int = int(os.getenv("GOOGLE_PLACES_CACHE_TTL_SECONDS", "86400"))
    # Max places enriched per request (cost cap; the re-scoring pool is <= limit+5 <= 15).
    google_places_max_enriched: int = int(os.getenv("GOOGLE_PLACES_MAX_ENRICHED", "15"))
    # Max candidates returned by one Google Text Search when OSM has no usable
    # live candidates. Text Search caps this at 20.
    google_places_candidate_limit: int = int(os.getenv("GOOGLE_PLACES_CANDIDATE_LIMIT", "12"))
    # Candidate search is the critical "find real places" path and costs at
    # most one Text Search per recommendation request. Keep it separate from
    # per-card enrichment so old enrichment usage cannot starve live results.
    google_places_candidate_daily_limit: int = int(os.getenv("GOOGLE_PLACES_CANDIDATE_DAILY_LIMIT", "120"))
    google_places_candidate_user_daily_limit: int = int(os.getenv("GOOGLE_PLACES_CANDIDATE_USER_DAILY_LIMIT", "30"))
    # App-side daily budgets for Google calls. Keep the global limit *below*
    # the Cloud Console quota cap so the app cuts off first. 0 disables
    # enrichment entirely.
    google_places_daily_limit: int = int(os.getenv("GOOGLE_PLACES_DAILY_LIMIT", "800"))
    # Per-anonymous_id daily cap (~4 enriched searches). Soft fairness control:
    # the id is client-supplied, so the global limit is the real backstop.
    google_places_user_daily_limit: int = int(os.getenv("GOOGLE_PLACES_USER_DAILY_LIMIT", "60"))
    # --- Community Confidence (first-party "wisdom of our adventurers") ---
    # All four gates are tunable per-deployment: small/concentrated audiences can
    # lower them to surface signal sooner; larger ones can raise them for tighter
    # statistical confidence. The shrinkage prior protects the (invisible) score
    # from thin-data noise, so the score floor can be loosened more aggressively
    # than the (publicly shown) badge floors.
    # Minimum distinct ratings before the liked-ratio moves the score at all.
    community_min_raters: int = int(os.getenv("COMMUNITY_MIN_RATERS", "3"))
    # Pseudo-count pulling thin samples toward neutral 70 (higher = more skeptical).
    community_shrink_prior: int = int(os.getenv("COMMUNITY_SHRINK_PRIOR", "8"))
    # Distinct raters required to show the "% liked" social-proof chip. Keep this
    # >= 4 so the chip is neither statistically flimsy nor near-deanonymizing.
    community_badge_min_raters: int = int(os.getenv("COMMUNITY_BADGE_MIN_RATERS", "5"))
    # Distinct recent visitors required to show the "visited recently" chip.
    community_badge_min_visits: int = int(os.getenv("COMMUNITY_BADGE_MIN_VISITS", "3"))
    # Rolling window (days) for counting "recent" visits. Wider than 30 so the
    # visits chip isn't starved at low traffic; drives the chip + recent_visits count.
    community_visit_window_days: int = int(os.getenv("COMMUNITY_VISIT_WINDOW_DAYS", "90"))
    # "Want to visit" is a one-sided demand/intent signal: distinct wanters nudge the
    # (invisible) score upward, saturating via community_shrink_prior and gated by
    # community_min_raters (reused). community_want_span caps the max upward nudge in
    # points; community_badge_min_wants gates the public "N want to go" chip.
    community_want_span: int = int(os.getenv("COMMUNITY_WANT_SPAN", "20"))
    community_badge_min_wants: int = int(os.getenv("COMMUNITY_BADGE_MIN_WANTS", "5"))


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
