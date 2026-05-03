"""Sanity check Gold Standard scraping."""
from __future__ import annotations

import asyncio
import sys

import httpx
from bs4 import BeautifulSoup

sys.stdout.reconfigure(encoding="utf-8")


async def main() -> None:
    headers = {"User-Agent": "VeritasOracle/1.0"}
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True, headers=headers) as c:
        for url in [
            "https://registry.goldstandard.org/projects/details/1042",  # GS-1042
            "https://registry.goldstandard.org/projects?q=&page=1",
        ]:
            r = await c.get(url)
            print(f"\n=== {url} -> {r.status_code} {len(r.text)} bytes ===")
            soup = BeautifulSoup(r.text, "lxml")
            title = soup.title.text.strip() if soup.title else "(no title)"
            h1 = soup.find("h1")
            print(f"title: {title[:120]}")
            print(f"h1: {h1.get_text(strip=True)[:120] if h1 else '(none)'}")
            # First few labelled fields
            for label in soup.find_all(["dt", "th", "strong", "label"])[:10]:
                t = label.get_text(strip=True)
                if t and len(t) < 60:
                    sib = label.find_next_sibling()
                    sib_text = sib.get_text(strip=True)[:80] if sib else ""
                    print(f"  {t!r:40s} -> {sib_text!r}")


asyncio.run(main())
