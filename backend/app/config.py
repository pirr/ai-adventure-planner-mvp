from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    app_name: str = "AI Adventure Planner MVP"
    version: str = "0.1.0"
    data_dir: Path = Path(os.getenv("DATA_DIR", "./data"))
    sqlite_path: Path = Path(os.getenv("SQLITE_PATH", "./data/adventures.db"))
    overpass_url: str = os.getenv("OVERPASS_URL", "https://overpass-api.de/api/interpreter")
    osrm_url: str = os.getenv("OSRM_URL", "https://router.project-osrm.org")
    openweather_api_key: str | None = os.getenv("OPENWEATHER_API_KEY")
    use_open_meteo_fallback: bool = os.getenv("USE_OPEN_METEO_FALLBACK", "true").lower() == "true"
    http_timeout_seconds: float = float(os.getenv("HTTP_TIMEOUT_SECONDS", "8"))


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
