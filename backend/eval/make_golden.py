"""Capture golden eval fixtures from real recommendation runs.

Writes one `{request, recommendations}` JSON per scenario × language into
golden/, plus an adversarial slice into golden/adversarial/ with weather,
forecast and warnings stripped (to test that a model says "unavailable"
instead of inventing them). Uses offline sample data, so it needs no network.

    cd backend && python -m eval.make_golden
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from app.schemas import AdventureRequest
from app.services.llm.template import TemplateProvider
from app.services.recommendations import build_recommendations

GOLDEN = Path(__file__).parent / "golden"
ADVERSARIAL = GOLDEN / "adversarial"
TIVAT = dict(lat=42.4304, lon=18.6964)

# Curated coverage: transport (walk/car/bike), time (30/120/300), contexts
# (solo/couple/family+children/dog/elderly/reduced mobility), interests, EN+RU.
_BASE: list[tuple[str, dict]] = [
    ("walk_solo_nature_30", dict(available_minutes=30, transport_mode="walk", group_type="solo", interests=["nature", "water"])),
    ("walk_couple_views_60", dict(available_minutes=60, transport_mode="walk", group_type="couple", interests=["viewpoints", "nature"])),
    ("car_family_history_300", dict(available_minutes=300, transport_mode="car", group_type="family", children_ages=[6, 13], interests=["history", "fortresses"])),
    ("car_family_dog_120", dict(available_minutes=120, transport_mode="car", group_type="family", children_ages=[8], with_dog=True, interests=["nature"])),
    ("bike_solo_food_120", dict(available_minutes=120, transport_mode="bike", group_type="solo", interests=["food"])),
    ("car_elderly_history_240", dict(available_minutes=240, transport_mode="car", group_type="couple", with_elderly=True, interests=["history", "viewpoints"])),
    ("walk_reduced_mobility_60", dict(available_minutes=60, transport_mode="walk", group_type="solo", reduced_mobility=True, interests=["viewpoints"])),
    ("car_solo_surprise_300", dict(available_minutes=300, transport_mode="car", group_type="solo", interests=["surprise me"])),
    ("car_couple_water_120", dict(available_minutes=120, transport_mode="car", group_type="couple", interests=["water", "nature"])),
    ("walk_family_history_120", dict(available_minutes=120, transport_mode="walk", group_type="family", children_ages=[10], interests=["history"])),
]


def _request(extra: dict, lang: str) -> AdventureRequest:
    return AdventureRequest(**TIVAT, use_live_data=False, limit=4, lang=lang, **extra)


async def _capture(request: AdventureRequest) -> dict:
    response = await build_recommendations(request, provider=TemplateProvider())
    return {
        "request": json.loads(request.json()),
        "recommendations": [json.loads(rec.json()) for rec in response.recommendations],
    }


def _strip(payload: dict) -> dict:
    out = {"request": payload["request"], "recommendations": []}
    for rec in payload["recommendations"]:
        rec = dict(rec)
        rec["arrival_weather"] = None
        rec["forecast"] = []
        rec["warnings"] = []
        out["recommendations"].append(rec)
    return out


def _clear(directory: Path) -> None:
    for path in directory.glob("*.json"):
        path.unlink()


async def main() -> None:
    GOLDEN.mkdir(parents=True, exist_ok=True)
    ADVERSARIAL.mkdir(parents=True, exist_ok=True)
    _clear(GOLDEN)
    _clear(ADVERSARIAL)

    for suffix, extra in _BASE:
        for lang in ("en", "ru"):
            payload = await _capture(_request(extra, lang))
            (GOLDEN / f"{suffix}_{lang}.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False))
        # Adversarial slice: EN only, with weather/forecast/warnings removed.
        adversarial = _strip(await _capture(_request(extra, "en")))
        (ADVERSARIAL / f"{suffix}_en.json").write_text(json.dumps(adversarial, indent=2, ensure_ascii=False))

    print(f"wrote {len(list(GOLDEN.glob('*.json')))} golden + {len(list(ADVERSARIAL.glob('*.json')))} adversarial fixtures")


if __name__ == "__main__":
    asyncio.run(main())
