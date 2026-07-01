"""The reference provider: the current OSM + Google discovery, unchanged.

This is what every cheap provider is measured against. It reuses the production
discovery building blocks (progressive Overpass + Google candidate backfill)
parameterized by an explicit radius, so the benchmark compares against exactly
the places production would surface. Google *enrichment* (ratings/photos) is
applied later, in `provider_orchestrator.rank_offline`, the same as production.

Public Overpass is flaky under a rapid sweep, so OSM is retried with backoff.
When it still fails, the result is a thin Google-only fallback: `last_degraded`
is set so the harness can skip that scenario (an unhealthy baseline would make
recall/RBO meaningless) and avoid caching it, letting a rerun heal.
"""
from __future__ import annotations

import asyncio
import logging

from app.schemas import PlaceCandidate
from app.services import google_places, places

logger = logging.getLogger(__name__)

_OSM_ATTEMPTS = 3
_OSM_BACKOFF = 2.0  # seconds, grows per attempt; lets a rate-limited Overpass recover


class OsmGoogleBaselineProvider:
    name = "osm_google"

    def __init__(self) -> None:
        # True after a fetch whose OSM step failed (result is Google-only, untrustworthy).
        self.last_degraded = False

    async def fetch(
        self,
        lat: float,
        lon: float,
        radius_km: float,
        interests: list[str],
        lang: str = "en",
        anonymous_id: str | None = None,
    ) -> list[PlaceCandidate]:
        self.last_degraded = False
        candidates: list[PlaceCandidate] = []
        for attempt in range(_OSM_ATTEMPTS):
            try:
                candidates = await places._fetch_osm_places_progressive(lat, lon, radius_km, interests)
                break
            except Exception as exc:  # noqa: BLE001 - benchmark should survive a flaky source
                if attempt + 1 < _OSM_ATTEMPTS:
                    await asyncio.sleep(_OSM_BACKOFF * (attempt + 1))
                    continue
                logger.warning(
                    "baseline OSM failed after %d attempts (%s); Google-only fallback",
                    _OSM_ATTEMPTS, exc.__class__.__name__,
                )
                self.last_degraded = True

        # Mirror get_candidate_places: backfill from Google only when OSM is sparse.
        # That call is budgeted and returns nothing without an anonymous_id.
        if places._needs_google_candidates(candidates, interests):
            google_candidates, _ = await google_places.search_candidate_places(
                lat, lon, radius_km, interests, anonymous_id, lang
            )
            candidates = places._merge_live_candidates(candidates, google_candidates)
        return candidates
