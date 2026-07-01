"""Provider interface + the adapter that plugs one into the production seam.

A `CandidateProvider` is the small thing each adapter implements: given a centre,
radius and interests, return `PlaceCandidate`s. `as_candidate_source` wraps one in
the exact signature `build_recommendations(candidate_source=...)` expects, so a
provider can also be driven through the *real* endpoint end-to-end. The benchmark
harness itself ranks offline (see `provider_orchestrator`) for determinism, but
the seam is kept working and tested so a full-pipeline run stays possible.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.schemas import PlaceCandidate
from app.services.places import radius_for_request


@runtime_checkable
class CandidateProvider(Protocol):
    #: Short stable id used in scoreboards / cache paths (e.g. "geoapify").
    name: str

    async def fetch(
        self,
        lat: float,
        lon: float,
        radius_km: float,
        interests: list[str],
        lang: str = "en",
        anonymous_id: str | None = None,
    ) -> list[PlaceCandidate]:
        """Discover candidate places around (lat, lon) within radius_km.

        `anonymous_id` is only needed by providers that hit a budgeted upstream
        (the baseline's Google candidate-backfill); hosted-API adapters ignore it.
        """
        ...


def as_candidate_source(provider: CandidateProvider):
    """Adapt `provider.fetch(...)` to the production `CandidateSource` signature.

    The result can be passed as `build_recommendations(candidate_source=...)`,
    which exercises the live routing/weather/enrichment pipeline with the
    provider's places instead of OSM. Enrichment is left to the pipeline (the
    benchmark's two arms are toggled in `provider_orchestrator.rank_offline`,
    not here), so this stays a pure source adapter.
    """

    async def source(
        lat: float,
        lon: float,
        available_minutes: int,
        transport_mode: str,
        interests: list[str],
        use_live_data: bool,
        lang: str = "en",
        anonymous_id: str | None = None,
    ) -> tuple[list[PlaceCandidate], list[str]]:
        radius_km = radius_for_request(available_minutes, transport_mode)
        candidates = await provider.fetch(lat, lon, radius_km, interests, lang, anonymous_id)
        return candidates, []

    return source
