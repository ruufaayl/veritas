"""Verra public-report cache.

Verra's modern project page is a JS SPA (no useful static HTML), and the
official partner API is gated. The legacy /mymodule/rpt reports, however,
still render server-side HTML tables.

We harvest two reports — both are open and need no auth:
  * r=209 — Buffer Pool (296 AFOLU/REDD+ projects, all on one page)
  * r=206 — Issued Credits, first page only (50 most-recent issuance rows
            covering ~30-50 unique projects across other categories)

The combined ~300-350 project records get persisted to a small SQLite
file and refreshed at most once per week. Lookups go: cache hit → done;
cache miss → caller falls back to live HTML scrape.
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).resolve().parents[2] / "data"
CACHE_DB = CACHE_DIR / "verra_cache.db"
CACHE_TTL_SECONDS = 7 * 24 * 60 * 60  # weekly refresh

R209_URL = "https://registry.verra.org/mymodule/rpt/myrpt.asp?r=209"
R206_URL = "https://registry.verra.org/mymodule/rpt/myrpt.asp?r=206"
HEADERS = {"User-Agent": "VeritasOracle/1.0 (+https://veritasoracle.vercel.app)"}

_refresh_lock = asyncio.Lock()


def _ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def _ensure_dir() -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _connect() -> sqlite3.Connection:
    _ensure_dir()
    conn = sqlite3.connect(CACHE_DB)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS projects (
            project_id            INTEGER PRIMARY KEY,
            project_name          TEXT,
            country               TEXT,
            project_type          TEXT,
            additional_certs      TEXT,
            buffer_contribution   INTEGER,
            buffer_available      INTEGER,
            credits_released      INTEGER,
            credits_cancelled     INTEGER,
            credits_on_hold       INTEGER,
            last_issuance_serial  TEXT,
            last_issuance_units   INTEGER,
            last_issuance_vintage TEXT,
            unit_type             TEXT,
            source_report         TEXT,
            refreshed_at          TEXT
        );

        CREATE TABLE IF NOT EXISTS cache_meta (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
        """
    )
    conn.commit()


def _is_stale(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT value FROM cache_meta WHERE key = 'last_refresh_ts'"
    ).fetchone()
    if not row:
        return True
    try:
        last = float(row["value"])
    except (TypeError, ValueError):
        return True
    return (time.time() - last) > CACHE_TTL_SECONDS


def _to_int(value: str | None) -> int | None:
    if value is None:
        return None
    cleaned = value.replace(",", "").strip()
    if not cleaned:
        return None
    try:
        return int(float(cleaned))
    except ValueError:
        return None


def _split_country(value: str | None) -> str | None:
    """Verra renders countries like 'Uruguay (UY)' — strip the code."""
    if not value:
        return None
    return value.split("(")[0].strip() or None


def _project_id_from_serial(serial: str | None) -> int | None:
    """Issuance serials look like '...-VCS-VCU-1491-VER-BR-1-197-...'.
    The project id is the integer field directly after the methodology id."""
    if not serial:
        return None
    parts = serial.split("-")
    # Pick the second-to-last numeric run that's <= 7 digits — that's the project id.
    # Heuristic: the id in the serial chunk after VCU sits at index 5 in the
    # canonical form, but layouts vary. Take the last small integer in parts.
    candidates = [p for p in parts if p.isdigit() and 1 <= len(p) <= 6]
    return int(candidates[-1]) if candidates else None


def _table_rows(html: str) -> list[list[str]]:
    soup = BeautifulSoup(html, "lxml")
    out: list[list[str]] = []
    seen_header = False
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        for row in rows:
            cells = [c.get_text(" ", strip=True) for c in row.find_all(["th", "td"])]
            if not cells:
                continue
            # First-cell duplicates the whole row in some Verra tables — skip it
            if len(cells) > 1 and cells[0].replace(" ", "") == "".join(c.replace(" ", "") for c in cells[1:]):
                cells = cells[1:]
            # Skip header repeats
            if cells[0].lower() in ("project id", "from vintage"):
                seen_header = True
                continue
            if seen_header and cells[0].isdigit():
                out.append(cells)
        if out:
            return out  # only need the first data table per report
    return out


async def _fetch_r209(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    """Buffer Pool report: 296 AFOLU/REDD+ projects."""
    started = time.perf_counter()
    response = await client.get(R209_URL)
    response.raise_for_status()
    rows = _table_rows(response.text)
    out: list[dict[str, Any]] = []
    # Columns: Project ID | Project Name | Buffer Contribution | Available Buffer
    #          | Released | Cancelled | On Hold | Project Type
    #          | Additional Certs | Country
    for cells in rows:
        cells = (cells + [""] * 10)[:10]
        pid = _to_int(cells[0])
        if not pid:
            continue
        out.append(
            {
                "project_id": pid,
                "project_name": cells[1] or None,
                "buffer_contribution": _to_int(cells[2]),
                "buffer_available": _to_int(cells[3]),
                "credits_released": _to_int(cells[4]),
                "credits_cancelled": _to_int(cells[5]),
                "credits_on_hold": _to_int(cells[6]),
                "project_type": cells[7] or None,
                "additional_certs": cells[8] or None,
                "country": _split_country(cells[9]),
                "source_report": "r=209",
            }
        )
    logger.info("verra r=209 -> %d projects (%dms)", len(out), _ms(started))
    return out


async def _fetch_r206(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    """Issued Credits, first page (50 issuances → ~30-50 unique projects)."""
    started = time.perf_counter()
    response = await client.get(R206_URL)
    response.raise_for_status()
    rows = _table_rows(response.text)
    seen: dict[int, dict[str, Any]] = {}
    # Columns: From Vintage | To Vintage | Serial Number | Quantity | Unit Type
    #          | Project ID | Project Name | Project Type
    for cells in rows:
        cells = (cells + [""] * 8)[:8]
        # Project ID lives at index 5 (after From/To/Serial/Quantity/UnitType)
        # — but the table also begins with project IDs in r=209. Detect by
        # looking for a 4-arg date pattern.
        pid = _to_int(cells[5])
        if not pid:
            continue
        entry = {
            "project_id": pid,
            "project_name": cells[6] or None,
            "project_type": cells[7] or None,
            "last_issuance_serial": cells[2] or None,
            "last_issuance_units": _to_int(cells[3]),
            "last_issuance_vintage": cells[1] or None,
            "unit_type": cells[4] or None,
            "source_report": "r=206",
        }
        # Keep the most recent (rows are already ordered most-recent-first)
        seen.setdefault(pid, entry)
    logger.info("verra r=206 -> %d unique projects (%dms)", len(seen), _ms(started))
    return list(seen.values())


def _upsert(conn: sqlite3.Connection, rows: list[dict[str, Any]]) -> None:
    now = datetime.now(timezone.utc).isoformat()
    for row in rows:
        existing = conn.execute(
            "SELECT * FROM projects WHERE project_id = ?", (row["project_id"],)
        ).fetchone()
        merged = dict(existing) if existing else {}
        for key, value in row.items():
            if value is not None:
                merged[key] = value
        merged["project_id"] = row["project_id"]
        merged["refreshed_at"] = now
        conn.execute(
            """
            INSERT INTO projects (
                project_id, project_name, country, project_type, additional_certs,
                buffer_contribution, buffer_available, credits_released,
                credits_cancelled, credits_on_hold, last_issuance_serial,
                last_issuance_units, last_issuance_vintage, unit_type,
                source_report, refreshed_at
            ) VALUES (
                :project_id, :project_name, :country, :project_type, :additional_certs,
                :buffer_contribution, :buffer_available, :credits_released,
                :credits_cancelled, :credits_on_hold, :last_issuance_serial,
                :last_issuance_units, :last_issuance_vintage, :unit_type,
                :source_report, :refreshed_at
            )
            ON CONFLICT(project_id) DO UPDATE SET
                project_name          = COALESCE(excluded.project_name, project_name),
                country               = COALESCE(excluded.country, country),
                project_type          = COALESCE(excluded.project_type, project_type),
                additional_certs      = COALESCE(excluded.additional_certs, additional_certs),
                buffer_contribution   = COALESCE(excluded.buffer_contribution, buffer_contribution),
                buffer_available      = COALESCE(excluded.buffer_available, buffer_available),
                credits_released      = COALESCE(excluded.credits_released, credits_released),
                credits_cancelled     = COALESCE(excluded.credits_cancelled, credits_cancelled),
                credits_on_hold       = COALESCE(excluded.credits_on_hold, credits_on_hold),
                last_issuance_serial  = COALESCE(excluded.last_issuance_serial, last_issuance_serial),
                last_issuance_units   = COALESCE(excluded.last_issuance_units, last_issuance_units),
                last_issuance_vintage = COALESCE(excluded.last_issuance_vintage, last_issuance_vintage),
                unit_type             = COALESCE(excluded.unit_type, unit_type),
                source_report         = COALESCE(excluded.source_report, source_report),
                refreshed_at          = excluded.refreshed_at
            """,
            {
                "project_id": merged["project_id"],
                "project_name": merged.get("project_name"),
                "country": merged.get("country"),
                "project_type": merged.get("project_type"),
                "additional_certs": merged.get("additional_certs"),
                "buffer_contribution": merged.get("buffer_contribution"),
                "buffer_available": merged.get("buffer_available"),
                "credits_released": merged.get("credits_released"),
                "credits_cancelled": merged.get("credits_cancelled"),
                "credits_on_hold": merged.get("credits_on_hold"),
                "last_issuance_serial": merged.get("last_issuance_serial"),
                "last_issuance_units": merged.get("last_issuance_units"),
                "last_issuance_vintage": merged.get("last_issuance_vintage"),
                "unit_type": merged.get("unit_type"),
                "source_report": merged.get("source_report"),
                "refreshed_at": now,
            },
        )
    conn.execute(
        "INSERT OR REPLACE INTO cache_meta (key, value) VALUES ('last_refresh_ts', ?)",
        (str(time.time()),),
    )
    conn.commit()


async def refresh_cache(force: bool = False) -> dict[str, Any]:
    """Idempotent refresh. Safe to call from many concurrent requests
    — the asyncio lock + on-disk meta key prevent duplicate work.
    """
    async with _refresh_lock:
        with closing(_connect()) as conn:
            _init_db(conn)
            if not force and not _is_stale(conn):
                count = conn.execute("SELECT COUNT(*) AS n FROM projects").fetchone()["n"]
                return {"refreshed": False, "project_count": count, "stale": False}

        try:
            async with httpx.AsyncClient(
                timeout=60.0, follow_redirects=True, headers=HEADERS
            ) as client:
                r209_rows, r206_rows = await asyncio.gather(
                    _fetch_r209(client), _fetch_r206(client), return_exceptions=True
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("verra refresh: fetch failed: %s", exc)
            return {"refreshed": False, "project_count": 0, "error": str(exc)}

        rows: list[dict[str, Any]] = []
        if isinstance(r209_rows, list):
            rows.extend(r209_rows)
        else:
            logger.warning("verra refresh: r=209 failed: %s", r209_rows)
        if isinstance(r206_rows, list):
            rows.extend(r206_rows)
        else:
            logger.warning("verra refresh: r=206 failed: %s", r206_rows)

        if not rows:
            return {"refreshed": False, "project_count": 0, "error": "no rows parsed"}

        with closing(_connect()) as conn:
            _init_db(conn)
            _upsert(conn, rows)
            count = conn.execute("SELECT COUNT(*) AS n FROM projects").fetchone()["n"]
        logger.info("verra cache refreshed -> %d total projects", count)
        return {"refreshed": True, "project_count": count}


async def lookup_project(project_id: int) -> dict[str, Any] | None:
    """Look up a single project by numeric ID. Triggers a cache refresh
    if the cache is missing or stale; returns None if still not found.
    """
    if project_id is None:
        return None

    with closing(_connect()) as conn:
        _init_db(conn)
        stale = _is_stale(conn)
        row = conn.execute(
            "SELECT * FROM projects WHERE project_id = ?", (project_id,)
        ).fetchone()
        if row and not stale:
            return dict(row)

    # Either no row or cache is stale — refresh once and try again
    await refresh_cache()
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT * FROM projects WHERE project_id = ?", (project_id,)
        ).fetchone()
        return dict(row) if row else None


__all__ = ["refresh_cache", "lookup_project", "_project_id_from_serial"]
