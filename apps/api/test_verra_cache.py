"""Smoke test for verra_cache + registry_scraper integration."""
from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

from app.services import registry_scraper, verra_cache  # noqa: E402

SERIALS = [
    "VCS-961",   # 'El Arriero' Afforestation, Uruguay - in r=209
    "VCS-1491",  # Kamiranga Ceramic Fuel Switching, BR - in r=206 first page
    "VCS-1748",  # not in either report - should fall back to HTML scrape
    "VCS-2558",  # ABC Norte REDD, Brazil - in r=209
]


async def main() -> None:
    # Force refresh first
    print("=== refresh_cache(force=True) ===")
    refresh_result = await verra_cache.refresh_cache(force=True)
    print(json.dumps(refresh_result, indent=2))

    # Direct DB lookup samples
    print("\n=== direct cache lookups ===")
    for pid in (961, 1491, 1748, 2558):
        row = await verra_cache.lookup_project(pid)
        if row:
            print(f"  pid={pid}: {row.get('project_name')[:60]} | {row.get('country')} | {row.get('source_report')}")
        else:
            print(f"  pid={pid}: (not in cache)")

    # End-to-end lookup_serial through the registry_scraper
    print("\n=== lookup_serial() integration ===")
    for serial in SERIALS:
        result = await registry_scraper.lookup_serial(serial)
        slim = {
            "serial": serial,
            "found": result.get("found"),
            "data_source": result.get("data_source"),
            "project_name": (result.get("project_name") or "")[:60],
            "country": result.get("country"),
            "methodology": (result.get("methodology") or "")[:50],
            "lat": result.get("lat"),
            "lon": result.get("lon"),
            "credits_issued": result.get("credits_issued"),
            "error": result.get("error"),
        }
        print(json.dumps(slim, indent=2))


asyncio.run(main())
