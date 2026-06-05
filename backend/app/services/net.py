from __future__ import annotations

import httpx

from app.config import settings


def http_client(timeout: float) -> httpx.AsyncClient:
    """An httpx client that identifies the app via a descriptive User-Agent.

    Public data providers (e.g. overpass-api.de) reject default library agents
    such as "python-httpx/*" with HTTP 406, so every outbound call must send a
    real User-Agent. Centralized here so new clients can't forget it.
    """
    return httpx.AsyncClient(timeout=timeout, headers={"User-Agent": settings.user_agent})
