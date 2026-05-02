"""Smoke test for the six VERITAS data services.

Runs each service against KPK Pakistan (lat=34.0, lon=73.0), prints the
full output, and reports which services returned real data vs errors.

Run from apps/api/ with the venv active:
    python test_services.py
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from app.services import (
    global_forest_watch,
    groq_scorer,
    nasa_firms,
    noaa_climate,
    registry_scraper,
    sentinel_hub,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

LAT = 34.0
LON = 73.0
SAMPLE_SERIAL = "VCS-1748"
TRIVIAL_SCALARS = {None, 0, 0.0, False, "UNKNOWN", "STABLE", "LOW"}


def _is_trivial(value: Any) -> bool:
    if isinstance(value, (list, dict)):
        return len(value) == 0
    return value in TRIVIAL_SCALARS


def _print_section(name: str, payload: Any) -> None:
    print(f"\n=== {name} ===")
    text = json.dumps(payload, default=str, indent=2)
    print(text if len(text) < 4000 else text[:4000] + "\n... [truncated]")


def _has_real_data(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    return any(not _is_trivial(value) for value in payload.values())


async def main() -> None:
    print(f"VERITAS service smoke test @ ({LAT}, {LON}) — serial {SAMPLE_SERIAL}")

    registry = await registry_scraper.lookup_serial(SAMPLE_SERIAL)
    fire = await nasa_firms.get_fire_alerts(LAT, LON)
    vegetation = await sentinel_hub.get_ndvi_timeseries(LAT, LON)
    forest = await global_forest_watch.get_forest_loss(LAT, LON)
    climate = await noaa_climate.get_climate_data(LAT, LON)
    score = await groq_scorer.calculate_risk_score(
        registry, fire, vegetation, forest, climate
    )

    _print_section("Registry", registry)
    _print_section("NASA FIRMS", fire)
    _print_section("Sentinel Hub NDVI", vegetation)
    _print_section("Global Forest Watch", forest)
    _print_section("NOAA Climate", climate)
    _print_section("Groq Risk Score", score)

    print("\n=== SUMMARY ===")
    results = {
        "registry_scraper": registry,
        "nasa_firms": fire,
        "sentinel_hub": vegetation,
        "global_forest_watch": forest,
        "noaa_climate": climate,
        "groq_scorer": score,
    }
    for name, payload in results.items():
        err = payload.get("error") if isinstance(payload, dict) else None
        if err:
            print(f"  [ERROR] {name:24s} {err}")
        elif _has_real_data(payload):
            print(f"  [OK]    {name}")
        else:
            print(f"  [EMPTY] {name:24s} no real data populated")


if __name__ == "__main__":
    asyncio.run(main())
