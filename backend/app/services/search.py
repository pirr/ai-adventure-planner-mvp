from __future__ import annotations

from app.schemas import AdventureRequest, AdventureResponse
from app.services.storage import storage as _storage


def save_response(
    request_id: str,
    request: AdventureRequest,
    response: AdventureResponse,
    account_id: int | None = None,
    *,
    store=_storage,
) -> None:
    """Persist a search and its cards in one transaction, and remember which
    places this user has now been shown (so a later "Show others" can rotate
    past them).

    The A/B ``explainer`` label is the one piece of business logic here: it is an
    *outcome* label — did the user actually see LLM explanations? — derived from
    the response, not stored policy."""
    response_json = response.json()
    explainer = "llm" if any(rec.summary for rec in response.recommendations) else "template"
    source_ids = [rec.source_id for rec in response.recommendations]
    with store.transaction() as conn:
        store.users.touch(conn, request.anonymous_id, request.lang)
        store.search.insert_session(conn, request_id, request, account_id, response_json, explainer)
        store.search.insert_recommendations(conn, request_id, response.recommendations)
        if account_id is not None:
            store.place_marks.record_account_seen(conn, account_id, source_ids)
        else:
            store.place_marks.record_seen(conn, request.anonymous_id, source_ids)
