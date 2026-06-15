from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


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
    # App-side daily budgets for Google calls. Keep the global limit *below*
    # the Cloud Console quota cap so the app cuts off first. 0 disables
    # enrichment entirely.
    google_places_daily_limit: int = int(os.getenv("GOOGLE_PLACES_DAILY_LIMIT", "800"))
    # Per-anonymous_id daily cap (~4 enriched searches). Soft fairness control:
    # the id is client-supplied, so the global limit is the real backstop.
    google_places_user_daily_limit: int = int(os.getenv("GOOGLE_PLACES_USER_DAILY_LIMIT", "60"))


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
