"""Capture golden eval fixtures from real recommendation runs.

Writes one `{request, recommendations}` JSON per scenario into eval/golden/.
Uses offline sample data by default so it needs no network; flip use_live_data
to capture richer real-world cases (sunny/rainy, near/far, missing-data, RU).
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from app.schemas import AdventureRequest
from app.services.llm.template import TemplateProvider
from app.services.recommendations import build_recommendations

GOLDEN = Path(__file__).parent / "golden"

SCENARIOS = {
    "tivat_family_en": AdventureRequest(
        lat=42.4304, lon=18.6964, available_minutes=300, transport_mode="car", group_type="family",
        children_ages=[6, 13], interests=["history", "fortresses", "viewpoints"], max_walking_km=3,
        use_live_data=False, limit=4, lang="en",
    ),
    "tivat_solo_ru": AdventureRequest(
        lat=42.4304, lon=18.6964, available_minutes=120, transport_mode="walk", group_type="solo",
        interests=["nature", "water"], use_live_data=False, limit=4, lang="ru",
    ),
}


async def main() -> None:
    GOLDEN.mkdir(parents=True, exist_ok=True)
    # Templates only — fixtures should capture computed facts, not a model's output.
    for name, request in SCENARIOS.items():
        response = await build_recommendations(request, provider=TemplateProvider())
        payload = {
            "request": json.loads(request.json()),
            "recommendations": [json.loads(rec.json()) for rec in response.recommendations],
        }
        path = GOLDEN / f"{name}.json"
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
        print("wrote", path)


if __name__ == "__main__":
    asyncio.run(main())
