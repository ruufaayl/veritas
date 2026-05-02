"""NASA FIRMS active-fire and thermal anomaly detection.

Hits both VIIRS_SNPP_NRT and MODIS_NRT for cross-validation, parses the
returned CSV, and aggregates hotspot counts, recency, and proximity to
the project center. Always returns a structured dict — never raises.
"""
from __future__ import annotations

import csv
import io
import logging
import math
import time

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

FIRMS_BASE = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"
SOURCES = ["VIIRS_SNPP_NRT", "MODIS_NRT"]


def _ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def _bbox(lat: float, lon: float, half: float = 0.5) -> str:
    return f"{lon - half},{lat - half},{lon + half},{lat + half}"


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_km = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lam = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(d_lam / 2) ** 2
    return 2 * radius_km * math.asin(math.sqrt(a))


def _classify_confidence(raw: str) -> str:
    val = (raw or "").strip().lower()
    if val in ("h", "high"):
        return "high"
    if val in ("n", "nominal", "m", "medium"):
        return "medium"
    if val in ("l", "low"):
        return "low"
    try:
        numeric = int(float(val))
    except ValueError:
        return "low"
    if numeric >= 80:
        return "high"
    if numeric >= 30:
        return "medium"
    return "low"


async def _fetch_source(
    client: httpx.AsyncClient, api_key: str, source: str, bbox: str, day_range: int
) -> list[dict]:
    url = f"{FIRMS_BASE}/{api_key}/{source}/{bbox}/{day_range}"
    started = time.perf_counter()
    try:
        response = await client.get(url)
        if response.status_code != 200:
            logger.warning("FIRMS %s -> HTTP %d (%dms)", source, response.status_code, _ms(started))
            return []
        text = response.text or ""
        if "Invalid" in text or "MAP_KEY" in text or text.startswith("<!"):
            logger.warning("FIRMS %s -> invalid response (%dms)", source, _ms(started))
            return []
        rows = list(csv.DictReader(io.StringIO(text)))
        logger.info("FIRMS %s -> %d rows (%dms)", source, len(rows), _ms(started))
        return rows
    except Exception as exc:  # noqa: BLE001
        logger.warning("FIRMS %s failed: %s (%dms)", source, exc, _ms(started))
        return []


async def get_fire_alerts(lat: float, lon: float, day_range: int = 90) -> dict:
    started = time.perf_counter()
    base = {
        "hotspot_count": 0,
        "high_confidence_count": 0,
        "medium_confidence_count": 0,
        "most_recent_fire_date": None,
        "nearest_fire_km": None,
        "fire_risk_flag": False,
        "hotspots": [],
        "day_range_queried": day_range,
        "source": "VIIRS_SNPP_NRT + MODIS_NRT",
        "error": None,
    }

    api_key = settings.NASA_FIRMS_API_KEY
    if not api_key:
        base["error"] = "NASA_FIRMS_API_KEY not configured"
        return base

    bbox = _bbox(lat, lon)
    rows: list[dict] = []
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            for source in SOURCES:
                rows.extend(await _fetch_source(client, api_key, source, bbox, day_range))
    except Exception as exc:  # noqa: BLE001
        base["error"] = f"FIRMS request failed: {exc}"
        return base

    high = medium = 0
    most_recent: str | None = None
    nearest: float | None = None
    sample: list[dict] = []
    seen: set[tuple] = set()

    for row in rows:
        try:
            r_lat = float(row.get("latitude") or row.get("lat") or 0)
            r_lon = float(row.get("longitude") or row.get("lon") or 0)
        except (TypeError, ValueError):
            continue
        if r_lat == 0 and r_lon == 0:
            continue

        date = (row.get("acq_date") or row.get("date") or "").strip()
        confidence = _classify_confidence(row.get("confidence") or "")
        try:
            brightness = float(row.get("brightness") or row.get("bright_ti4") or 0)
        except (TypeError, ValueError):
            brightness = 0.0

        key = (r_lat, r_lon, date)
        if key in seen:
            continue
        seen.add(key)

        if confidence == "high":
            high += 1
        elif confidence == "medium":
            medium += 1

        if not most_recent or date > most_recent:
            most_recent = date

        distance_km = _haversine_km(lat, lon, r_lat, r_lon)
        if nearest is None or distance_km < nearest:
            nearest = distance_km

        if len(sample) < 50:
            sample.append(
                {
                    "lat": r_lat,
                    "lon": r_lon,
                    "date": date,
                    "confidence": confidence,
                    "brightness": brightness,
                }
            )

    base.update(
        {
            "hotspot_count": len(seen),
            "high_confidence_count": high,
            "medium_confidence_count": medium,
            "most_recent_fire_date": most_recent,
            "nearest_fire_km": round(nearest, 2) if nearest is not None else None,
            "fire_risk_flag": high >= 5 or (nearest is not None and nearest < 5),
            "hotspots": sample,
        }
    )
    logger.info(
        "FIRMS aggregate: %d hotspots in 1deg box (%dms)", len(seen), _ms(started)
    )
    return base
