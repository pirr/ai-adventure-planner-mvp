"""Category-coverage audit: how often does a provider's category tree fail to map
to one of the app's place types (falling back to "place")?

Geoapify/LocationIQ expose curated categories, not raw OSM tags, so some places
land on the generic fallback and lose interest/quality signal. This prints the
fallback share per provider per city so those gaps are visible before reading the
benchmark numbers.

    docker compose exec app python -m eval.providers.category_audit --max-cities 6
"""
from __future__ import annotations

import argparse
import asyncio

from app.services.places import radius_for_request
from eval.city_benchmark import CITIES
from eval.provider_benchmark import PROFILES, PROVIDERS


async def _audit(provider_names: list[str], max_cities: int) -> None:
    cities = CITIES[:max_cities] if max_cities else CITIES
    pid, transport, minutes, interests = PROFILES[0]
    radius_km = radius_for_request(minutes, transport)
    providers = [PROVIDERS[name]() for name in provider_names]
    print(f"Category fallback audit  (profile={pid}, interests={interests})\n")
    for name, country, lat, lon in cities:
        for provider in providers:
            candidates = await provider.fetch(lat, lon, radius_km, interests, "en", f"audit-{name}")
            n = len(candidates)
            fallback = sum(1 for c in candidates if c.type == "place")
            pct = (100 * fallback / n) if n else 0.0
            print(f"  {provider.name:<12} {name + ', ' + country:<18} n={n:<4} "
                  f"fallback(place)={fallback:<3} ({pct:.0f}%)")


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--providers", nargs="+", default=list(PROVIDERS), choices=list(PROVIDERS))
    p.add_argument("--max-cities", type=int, default=6, help="Only the first N cities (0 = all)")
    args = p.parse_args(argv)
    asyncio.run(_audit(args.providers, args.max_cities))


if __name__ == "__main__":
    main()
