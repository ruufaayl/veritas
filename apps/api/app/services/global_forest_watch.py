"""Global Forest Watch tree-cover loss.

GFW Data API is fully open — no API key, no Authorization header.
Returns annual loss totals for the last 5 years and computes a simple
loss-rate / trend classification. Always returns a structured dict.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

GFW_BASE = "https://data-api.globalforestwatch.org"
DATASET = "umd_tree_cover_loss"
TREE_LOSS_URL = f"{GFW_BASE}/dataset/{DATASET}/latest/query"
DATASET_META_URL = f"{GFW_BASE}/dataset/{DATASET}/latest"


def _ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def _polygon(lat: float, lon: float, half: float = 0.1) -> dict:
    return {
        "type": "Polygon",
        "coordinates": [
            [
                [lon - half, lat - half],
                [lon + half, lat - half],
                [lon + half, lat + half],
                [lon - half, lat + half],
                [lon - half, lat - half],
            ]
        ],
    }


def _classify_trend(rate_pct: float) -> str:
    if rate_pct >= 5.0:
        return "CRITICAL"
    if rate_pct >= 1.0:
        return "DECLINING"
    return "STABLE"


def _polygon_area_ha(half: float = 0.1) -> float:
    # Rough planar approximation: 1 deg ≈ 111 km. Box side = 2 * half deg.
    side_km = 2 * half * 111.0
    return side_km * side_km * 100  # km^2 -> ha


async def get_forest_loss(lat: float, lon: float) -> dict:
    started = time.perf_counter()
    base = {
        "annual_loss_ha": [],
        "total_loss_5yr_ha": 0.0,
        "annual_loss_rate_pct": 0.0,
        "intact_forest_pct": 0.0,
        "primary_forest_loss_ha": 0.0,
        "forest_loss_flag": False,
        "trend": "STABLE",
        "auth_required": False,
        "error": None,
    }

    sql = (
        "SELECT umd_tree_cover_loss__year, SUM(area__ha) AS area__ha "
        "FROM data "
        "WHERE umd_tree_cover_loss__threshold = 30 "
        "GROUP BY umd_tree_cover_loss__year "
        "ORDER BY umd_tree_cover_loss__year"
    )
    body = {"geometry": _polygon(lat, lon), "sql": sql}
    headers = {"Content-Type": "application/json"}
    if settings.GFW_API_KEY:
        headers["x-api-key"] = settings.GFW_API_KEY
        base["auth_required"] = True

    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            response = await client.post(TREE_LOSS_URL, json=body, headers=headers)
            if response.status_code == 404:
                logger.warning("GFW: query returned 404, confirming dataset metadata")
                meta = await client.get(DATASET_META_URL, headers=headers)
                if meta.status_code != 200:
                    base["error"] = f"GFW dataset metadata returned {meta.status_code}"
                    return base
                response = await client.post(TREE_LOSS_URL, json=body, headers=headers)
            if response.status_code != 200:
                base["error"] = (
                    f"GFW returned HTTP {response.status_code}: {response.text[:200]}"
                )
                logger.warning(
                    "GFW HTTP %d (%dms)", response.status_code, _ms(started)
                )
                return base
            payload = response.json()
    except Exception as exc:  # noqa: BLE001
        base["error"] = f"GFW request failed: {exc}"
        logger.warning("GFW failed: %s (%dms)", exc, _ms(started))
        return base

    rows: list[dict[str, Any]] = payload.get("data") or payload.get("rows") or []
    by_year: dict[int, float] = {}
    primary_loss = 0.0

    for row in rows:
        year = (
            row.get("umd_tree_cover_loss__year")
            or row.get("year")
            or row.get("loss_year")
        )
        loss = (
            row.get("area__ha")
            or row.get("loss_ha")
            or row.get("area_ha")
            or row.get("loss__ha")
            or 0
        )
        try:
            year_int = int(year)
            loss_float = float(loss)
        except (TypeError, ValueError):
            continue
        by_year[year_int] = by_year.get(year_int, 0.0) + loss_float
        if row.get("umd_regional_primary_forest_2001__exists") or row.get("primary_forest"):
            primary_loss += loss_float

    current_year = datetime.utcnow().year
    last_5_years = sorted(y for y in by_year if current_year - 5 <= y <= current_year)
    annual = [{"year": y, "loss_ha": round(by_year[y], 2)} for y in last_5_years]
    total_loss = sum(by_year[y] for y in last_5_years)

    poly_area_ha = _polygon_area_ha()
    rate_pct = round((total_loss / poly_area_ha) * 100, 3) if poly_area_ha > 0 else 0.0
    intact_pct = round(max(0.0, 100.0 - rate_pct), 2)

    base.update(
        {
            "annual_loss_ha": annual,
            "total_loss_5yr_ha": round(total_loss, 2),
            "annual_loss_rate_pct": rate_pct,
            "intact_forest_pct": intact_pct,
            "primary_forest_loss_ha": round(primary_loss, 2),
            "forest_loss_flag": rate_pct >= 1.0,
            "trend": _classify_trend(rate_pct),
        }
    )
    logger.info(
        "GFW: %d years, %.2f ha total loss (%dms)",
        len(annual),
        total_loss,
        _ms(started),
    )
    return base
