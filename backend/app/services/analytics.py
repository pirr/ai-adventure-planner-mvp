from __future__ import annotations

from typing import Any

from app.services.storage import storage as _storage


def ab_summary(store=_storage) -> list[dict[str, Any]]:
    """Per explainer variant (llm/template): sessions, thumbs-up rate and
    maps-open rate, derived from the raw session<->feedback/event counts."""
    raw = store.search.ab_raw_counts()
    sessions, feedback, maps = raw["sessions"], raw["feedback"], raw["maps"]
    result = []
    for variant in sorted(sessions, key=lambda v: v or ""):
        n = sessions[variant]
        fb = feedback.get(variant)
        up, total = (fb["up"] or 0, fb["total"]) if fb else (0, 0)
        result.append({
            "variant": variant,
            "sessions": n,
            "feedback": total,
            "thumbs_up_rate": round(up / total, 3) if total else None,
            "maps_opened": maps.get(variant, 0),
            "maps_open_rate": round(maps.get(variant, 0) / n, 3) if n else None,
        })
    return result
