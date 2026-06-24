"""The reference provider: the current OSM + Google discovery, unchanged.

This is what every cheap provider is measured against. It reuses the production
discovery building blocks (progressive Overpass + Google candidate backfill)
parameterized by an explicit radius, so the benchmark compares against exactly
the places production would surface. Google *enrichment* (ratings/photos) is
applied later, in `provider_orchestrator.rank_offline`, the same as production.
"""
from __future__ import annotations

import logging

from app.schemas import PlaceCandidate
from app.services import google_places, places

logger = logging.getLogger(__name__)


class OsmGoogleBaselineProvider:
    name = "osm_google"

    async def fetch(
        self,
        lat: float,
        lon: float,
        radius_km: float,
        interests: list[str],
        lang: str = "en",
        anonymous_id: str | None = None,
    ) -> list[PlaceCandidate]:
        # Degrade like get_candidate_places: a flaky Overpass shouldn't abort a sweep.
        try:
            candidates = await places._fetch_osm_places_progressive(lat, lon, radius_km, interests)
        except Exception as exc:  # noqa: BLE001 - benchmark should survive a flaky source
            logger.warning("baseline OSM fetch failed (%s); falling back to Google-only", exc.__class__.__name__)
            candidates = []
        # Mirror get_candidate_places: backfill from Google only when OSM is sparse.
        # That call is budgeted and returns nothing without an anonymous_id.
        if places._needs_google_candidates(candidates, interests):
            google_candidates, _ = await google_places.search_candidate_places(
                lat, lon, radius_km, interests, anonymous_id, lang
            )
            candidates = places._merge_live_candidates(candidates, google_candidates)
        return candidates
