"""Quick probe of Verra registry endpoints for ingestion design."""
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
        r = await c.get("https://registry.verra.org/mymodule/rpt/myrpt.asp?r=206")
        text = r.text

        # Pagination clues
        pg_links = re.findall(r"href=[\"']([^\"']*(?:page|next|prev|pg|skip|offset)[^\"']*)", text, re.I)
        print("page links:", set(pg_links[:10]) or "(none)")

        # Forms on the page
        forms = re.findall(r"<form[^>]*action=\"([^\"]+)\"[^>]*>(.*?)</form>", text, re.I | re.S)
        print(f"forms: {len(forms)}")
        for action, body in forms:
            inputs = re.findall(r"<input[^>]*name=\"([^\"]+)\"", body)
            selects = re.findall(r"<select[^>]*name=\"([^\"]+)\"", body)
            print(f"  action={action[:60]} inputs={inputs[:10]} selects={selects[:4]}")

        # JavaScript hints — paginate functions, etc.
        js_calls = set(re.findall(r"function\s+(\w+)\s*\(", text))
        print(f"js funcs: {sorted(js_calls)[:15]}")

        # Try filtering with a project ID
        for q in [
            "?r=206&idProject=197",
            "?r=206&searchString=197",
            "?r=206&pjID=197",
            "?r=206&projectID=197",
            "?r=206&prjID=197",
            "?r=206&id=197",
        ]:
            r2 = await c.get(f"https://registry.verra.org/mymodule/rpt/myrpt.asp{q}")
            counter = re.search(r"(\d+\s*-\s*\d+\s*:\s*\d+)", r2.text)
            print(f"  {q:40s} -> status={r2.status_code} len={len(r2.text)} counter={counter.group(1) if counter else '-'}")

        # Probe other report IDs
        for rid in [200, 201, 202, 203, 204, 205, 207, 208, 209, 210]:
            r3 = await c.get(f"https://registry.verra.org/mymodule/rpt/myrpt.asp?r={rid}")
            counter = re.search(r"(\d+\s*-\s*\d+\s*:\s*\d+)", r3.text)
            print(
                f"r={rid:3d} status={r3.status_code} len={len(r3.text)} "
                f"counter={counter.group(1) if counter else '-'}"
            )


asyncio.run(main())
