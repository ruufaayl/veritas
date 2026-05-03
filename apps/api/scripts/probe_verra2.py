"""Probe r=209 contents and pagination via mypage.asp."""
from __future__ import annotations

import asyncio
import re
import sys

import httpx
from bs4 import BeautifulSoup

sys.stdout.reconfigure(encoding="utf-8")

UA = {"User-Agent": "VeritasOracle/1.0"}


async def main() -> None:
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True, headers=UA) as c:
        # 1) Inspect r=209
        r = await c.get("https://registry.verra.org/mymodule/rpt/myrpt.asp?r=209")
        soup = BeautifulSoup(r.text, "lxml")
        tables = soup.find_all("table")
        print(f"r=209: {len(tables)} tables")
        for i, t in enumerate(tables[:3]):
            rows = t.find_all("tr")
            print(f"  table[{i}] rows={len(rows)}")
            for j, row in enumerate(rows[:4]):
                cells = [c.get_text(strip=True)[:80] for c in row.find_all(["th", "td"])]
                if cells:
                    print(f"    row{j}: {cells[:10]}")

        # 2) Probe more report IDs (211-240)
        print("\n--- more report IDs ---")
        for rid in range(211, 241):
            r2 = await c.get(f"https://registry.verra.org/mymodule/rpt/myrpt.asp?r={rid}")
            counter = re.search(r"(\d+\s*-\s*\d+\s*:\s*\d+)", r2.text)
            if counter:
                print(f"  r={rid:3d} len={len(r2.text):>7} counter={counter.group(1)}")

        # 3) Probe pagination via mypage.asp
        print("\n--- pagination probe ---")
        # Need to first hit r=206 in same client to set cookies
        r0 = await c.get("https://registry.verra.org/mymodule/rpt/myrpt.asp?r=206")
        for params in [
            {"page": 2},
            {"PageNumber": 2},
            {"p": 2},
            {"pg": 2},
            {"PageStart": 51},
            {"start": 51, "limit": 50},
            {"Skip": 50},
        ]:
            rp = await c.get("https://registry.verra.org/mymodule/mypage.asp", params=params)
            counter = re.search(r"(\d+\s*-\s*\d+\s*:\s*\d+)", rp.text)
            print(f"  mypage {params} -> status={rp.status_code} len={len(rp.text)} counter={counter.group(1) if counter else '-'}")


asyncio.run(main())
