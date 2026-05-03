"""Dashboard summary endpoint.

Aggregates the seven panels listed in the spec into one JSON payload.
Caches the result in-memory for 5 minutes so the dashboard auto-refresh
loop doesn't burn through external API quota.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter
from sqlalchemy import desc, func, select

from app.core.database import AsyncSessionLocal
from app.models.audit import Audit
from app.services import nasa_firms, noaa_climate

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/dashboard", tags=["dashboard"])

CACHE_TTL_SECONDS = 5 * 60
_cache: dict[str, Any] = {"payload": None, "fetched_at": 0.0}
_cache_lock = asyncio.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _recent_audits(limit: int = 5) -> list[dict[str, Any]]:
    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Audit).order_by(desc(Audit.created_at)).limit(limit)
            )
            return [
                {
                    "audit_id": str(a.id),
                    "serial": a.serial,
                    "project_name": a.project_name,
                    "country": a.country,
                    "overall_score": a.overall_score,
                    "risk_level": a.risk_level,
                    "created_at": a.created_at.isoformat() if a.created_at else None,
                }
                for a in result.scalars().all()
            ]
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard.recent_audits: DB unavailable (%s)", exc)
        return []


async def _global_risk_map() -> list[dict[str, Any]]:
    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Audit.id, Audit.serial, Audit.lat, Audit.lon,
                       Audit.overall_score, Audit.risk_level)
                .where(Audit.lat.isnot(None), Audit.lon.isnot(None))
                .order_by(desc(Audit.created_at))
                .limit(500)
            )
            return [
                {"audit_id": str(row[0]), "serial": row[1],
                 "lat": row[2], "lon": row[3],
                 "overall_score": row[4], "risk_level": row[5]}
                for row in result.all()
            ]
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard.global_risk_map: DB unavailable (%s)", exc)
        return []


async def _registry_activity() -> dict[str, Any]:
    try:
        async with AsyncSessionLocal() as session:
            total = await session.scalar(select(func.count()).select_from(Audit))
            return {"total_audits": int(total or 0)}
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard.registry_activity: DB unavailable (%s)", exc)
        return {"total_audits": 0, "error": "db_unavailable"}


async def _active_fire_count() -> dict[str, Any]:
    """Spot-check using a representative bbox (Amazon basin, last 24h).
    Real prod would query a heat-aggregator; this is a representative
    indicator without burning the whole NASA FIRMS quota."""
    try:
        data = await nasa_firms.get_fire_alerts(lat=-3.0, lon=-60.0, day_range=1)
        return {
            "region": "Amazon basin (24h)",
            "hotspots": data.get("hotspot_count", 0),
            "high_confidence": data.get("high_confidence_count", 0),
            "error": data.get("error"),
        }
    except Exception as exc:  # noqa: BLE001
        return {"region": "Amazon basin (24h)", "error": str(exc), "hotspots": 0}


async def _climate_anomalies() -> dict[str, Any]:
    """Spot-check NOAA at a representative coordinate."""
    try:
        data = await noaa_climate.get_climate_data(lat=34.0, lon=-118.0)
        return {
            "sample_station": data.get("nearest_station"),
            "temp_anomaly_c": data.get("temp_anomaly_c"),
            "precipitation_anomaly_mm": data.get("precipitation_anomaly_mm"),
            "risk_level": data.get("climate_risk_level"),
            "error": data.get("error"),
        }
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc), "risk_level": "UNKNOWN"}


async def _build_payload() -> dict[str, Any]:
    fire_panel, climate_panel, recent, risk_map, registry = await asyncio.gather(
        _active_fire_count(),
        _climate_anomalies(),
        _recent_audits(limit=5),
        _global_risk_map(),
        _registry_activity(),
        return_exceptions=False,
    )
    return {
        "panels": {
            "carbon_intelligence": {
                "headline": "Live carbon-credit integrity feed",
                "summary": "Daily AI brief is computed on demand by the audit "
                           "endpoint; this panel shows aggregate signal.",
                "source": "veritas_oracle_aggregate",
            },
            "active_fire_count": fire_panel,
            "climate_anomalies": climate_panel,
            "recent_audits": recent,
            "global_risk_map": risk_map,
            "registry_activity": registry,
            "market_signals": {
                "status": "placeholder",
                "note": "Wire to a carbon-price feed (e.g. Refinitiv, ICE EUA) "
                        "in a future phase.",
            },
        },
        "last_updated": _now_iso(),
        "data_sources": [
            "NASA FIRMS",
            "NOAA NCDC",
            "VERITAS audit history",
        ],
    }


@router.get("")
async def get_dashboard() -> dict[str, Any]:
    async with _cache_lock:
        age = time.time() - _cache["fetched_at"]
        if _cache["payload"] is not None and age < CACHE_TTL_SECONDS:
            payload = dict(_cache["payload"])
            payload["cache_age_seconds"] = round(age, 1)
            return payload

        fresh = await _build_payload()
        _cache["payload"] = fresh
        _cache["fetched_at"] = time.time()
        fresh["cache_age_seconds"] = 0
        return fresh
