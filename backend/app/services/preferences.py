from __future__ import annotations

import json
from typing import Any

from app.services.storage import storage as _storage


def _profile_from_rows(rows) -> dict[str, Any]:
    """Net up/down feedback per place type: net_score = (#up - #down).

    Empty when there is no usable feedback yet (cold start -> neutral scoring)."""
    place_types: dict[str, int] = {}
    for row in rows:
        try:
            place_type = json.loads(row["payload_json"]).get("place_type")
        except (TypeError, ValueError):
            continue
        if not place_type:
            continue
        place_types[place_type] = place_types.get(place_type, 0) + (1 if row["rating"] == "up" else -1)
    return {"place_types": place_types} if place_types else {}


def preference_profile(anonymous_id: str | None, store=_storage) -> dict[str, Any]:
    """Personal Preference Fit profile for one anonymous user."""
    if not anonymous_id:
        return {}
    rows = store.feedback.feedback_with_payload(anonymous_id=anonymous_id)
    return _profile_from_rows(rows)


def preference_profile_for_account(account_id: int | None, store=_storage) -> dict[str, Any]:
    """Personal Preference Fit profile for one logged-in account."""
    if account_id is None:
        return {}
    rows = store.feedback.feedback_with_payload(account_id=account_id)
    return _profile_from_rows(rows)
