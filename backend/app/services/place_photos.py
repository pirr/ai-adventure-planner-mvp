from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote, unquote, urlparse

from app.config import settings
from app.schemas import PlaceCandidate, PlacePhoto
from app.services import google_places
from app.services.net import http_client
from app.services.storage import storage


COMMONS_SOURCE = "Wikimedia Commons"
_COMMONS_FILE_PREFIX_RE = re.compile(r"^(?:file|image):(.+)$", re.IGNORECASE)
_WIKIDATA_ID_RE = re.compile(r"Q\d+", re.IGNORECASE)
_IMAGE_TAG_KEYS = (
    "image",
    "image:0",
    "wikimedia_commons",
    "wikimedia_commons:image",
)


def _first_value(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text.split(";")[0].strip()


def _safe_http_url(value: str) -> str | None:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return value


def _commons_filename(value: str) -> str | None:
    text = unquote(value).strip()
    match = _COMMONS_FILE_PREFIX_RE.match(text)
    if match:
        return match.group(1).strip()

    parsed = urlparse(text)
    if not parsed.netloc.endswith("commons.wikimedia.org"):
        return None

    path = unquote(parsed.path)
    for marker in ("/wiki/File:", "/wiki/Image:", "/wiki/Special:FilePath/"):
        if marker in path:
            return path.split(marker, 1)[1].strip()
    return None


def _commons_photo(filename: str, source: str = COMMONS_SOURCE) -> PlacePhoto | None:
    normalized = filename.replace(" ", "_").strip()
    if not normalized or normalized.lower().startswith("category:"):
        return None
    encoded = quote(normalized, safe="")
    return PlacePhoto(
        url=f"https://commons.wikimedia.org/wiki/Special:FilePath/{encoded}?width=960",
        source=source,
        source_url=f"https://commons.wikimedia.org/wiki/File:{encoded}",
    )


def photo_from_tags(tags: dict[str, Any]) -> PlacePhoto | None:
    for key in _IMAGE_TAG_KEYS:
        value = _first_value(tags.get(key))
        if not value:
            continue

        commons_file = _commons_filename(value)
        if commons_file:
            return _commons_photo(commons_file)

        direct_url = _safe_http_url(value)
        if direct_url:
            return PlacePhoto(url=direct_url, source="OpenStreetMap image")

    return None


def _wikidata_id(value: Any) -> str | None:
    text = _first_value(value)
    if not text:
        return None
    match = _WIKIDATA_ID_RE.search(text)
    return match.group(0).upper() if match else None


async def _photo_from_wikidata(wikidata_id: str) -> PlacePhoto | None:
    url = f"https://www.wikidata.org/wiki/Special:EntityData/{wikidata_id}.json"
    async with http_client(min(settings.http_timeout_seconds, 4)) as client:
        response = await client.get(url)
        response.raise_for_status()
        payload = response.json()

    entity = payload.get("entities", {}).get(wikidata_id, {})
    claims = entity.get("claims", {}).get("P18", [])
    for claim in claims:
        value = (
            claim.get("mainsnak", {})
            .get("datavalue", {})
            .get("value")
        )
        if isinstance(value, str):
            return _commons_photo(value, source=COMMONS_SOURCE)
    return None


def _reserve_google_photo(anonymous_id: str | None) -> bool:
    """Reserve one Google Places Photo media call from the shared daily budget.

    Photo resolution is a billed Google request, so it draws the same daily cap
    as enrichment (``GOOGLE_PLACES_DAILY_LIMIT``). Returns False when that budget
    is exhausted — or the request isn't budgetable (no ``anonymous_id``) — so the
    caller stops hitting Google and keeps the free photo sources tried above.
    """
    granted = storage.api_usage.reserve_api_calls(
        "google",
        anonymous_id,
        1,
        daily_limit=settings.google_places_daily_limit,
        user_daily_limit=settings.google_places_user_daily_limit,
    )
    return granted >= 1


async def get_place_photo(
    place: PlaceCandidate, use_live_data: bool, anonymous_id: str | None = None
) -> PlacePhoto | None:
    tagged_photo = photo_from_tags(place.tags)
    if tagged_photo or not use_live_data:
        return tagged_photo

    wikidata_id = _wikidata_id(place.tags.get("wikidata"))
    if wikidata_id:
        try:
            photo = await _photo_from_wikidata(wikidata_id)
            if photo:
                return photo
        except Exception:
            pass

    # Free sources exhausted; fall back to Google when enrichment found one
    # (each media resolution is a billed call, so it stays last in line). The
    # budget gate means an exhausted daily quota falls back to free sources
    # rather than continuing to bill Google.
    if place.google_photo_name and _reserve_google_photo(anonymous_id):
        try:
            return await google_places.resolve_photo(place.google_photo_name, place.google_photo_attribution)
        except Exception:
            return None
    return None
