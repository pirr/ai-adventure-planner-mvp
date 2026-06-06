from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    app_name: str = "AI Adventure Planner MVP"
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


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
