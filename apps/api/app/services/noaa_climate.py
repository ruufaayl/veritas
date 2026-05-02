"""NOAA NCDC CDO climate anomaly data.

Finds the nearest GHCND station, pulls last 12 months of TAVG / PRCP,
compares against a 30-year baseline, and classifies climate risk.
Always returns a structured dict.
"""
from __future__ import annotations

import logging
import statistics
import time
from datetime import date, timedelta

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

NOAA_BASE = "https://www.ncdc.noaa.gov/cdo-web/api/v2"
DATASET_ID = "GHCND"


def _ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def _classify_climate(temp_anomaly: float | None, drought: bool, extremes: int) -> str:
    score = 0
    if temp_anomaly is not None and abs(temp_anomaly) >= 1.5:
        score += 2
    if drought:
        score += 2
    if extremes >= 3:
        score += 1
    if score >= 4:
        return "HIGH"
    if score >= 2:
        return "MEDIUM"
    return "LOW"


async def _get(client: httpx.AsyncClient, path: str, params: dict, token: str) -> list[dict]:
    started = time.perf_counter()
    try:
        response = await client.get(
            f"{NOAA_BASE}{path}", params=params, headers={"token": token}
        )
        if response.status_code != 200:
            logger.warning(
                "NOAA %s -> HTTP %d (%dms)", path, response.status_code, _ms(started)
            )
            return []
        body = response.json() or {}
        results = body.get("results") or []
        logger.info("NOAA %s -> %d records (%dms)", path, len(results), _ms(started))
        return results
    except Exception as exc:  # noqa: BLE001
        logger.warning("NOAA %s failed: %s (%dms)", path, exc, _ms(started))
        return []


def _mean(rows: list[dict]) -> float | None:
    values = [r["value"] for r in rows if isinstance(r.get("value"), (int, float))]
    if not values:
        return None
    return statistics.mean(values)


async def get_climate_data(lat: float, lon: float) -> dict:
    started = time.perf_counter()
    base = {
        "nearest_station": None,
        "temp_anomaly_c": None,
        "precipitation_anomaly_mm": None,
        "drought_stress_flag": False,
        "climate_risk_level": "LOW",
        "recent_extremes_count": 0,
        "data_period": None,
        "error": None,
    }

    token = settings.NOAA_TOKEN
    if not token:
        base["error"] = "NOAA_TOKEN not configured"
        return base

    today = date.today()
    recent_end = today - timedelta(days=1)
    recent_start = recent_end - timedelta(days=365)
    baseline_end = recent_start - timedelta(days=1)
    baseline_start = baseline_end - timedelta(days=365 * 30)

    extent = f"{lat - 1:.4f},{lon - 1:.4f},{lat + 1:.4f},{lon + 1:.4f}"

    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            stations = await _get(
                client,
                "/stations",
                {"extent": extent, "limit": 1, "datasetid": DATASET_ID},
                token,
            )
            if not stations:
                base["error"] = "No NOAA stations near coordinates"
                return base
            station = stations[0]
            station_id = station.get("id")
            base["nearest_station"] = (
                f"{station.get('name', station_id)} ({station_id})"
            )

            common = {"datasetid": DATASET_ID, "stationid": station_id, "limit": 1000, "units": "metric"}
            recent_temp = await _get(
                client,
                "/data",
                {**common, "datatypeid": "TAVG", "startdate": recent_start.isoformat(), "enddate": recent_end.isoformat()},
                token,
            )
            baseline_temp = await _get(
                client,
                "/data",
                {**common, "datatypeid": "TAVG", "startdate": baseline_start.isoformat(), "enddate": baseline_end.isoformat()},
                token,
            )
            recent_precip = await _get(
                client,
                "/data",
                {**common, "datatypeid": "PRCP", "startdate": recent_start.isoformat(), "enddate": recent_end.isoformat()},
                token,
            )
            baseline_precip = await _get(
                client,
                "/data",
                {**common, "datatypeid": "PRCP", "startdate": baseline_start.isoformat(), "enddate": baseline_end.isoformat()},
                token,
            )
    except Exception as exc:  # noqa: BLE001
        base["error"] = f"NOAA request failed: {exc}"
        logger.warning("NOAA failed: %s (%dms)", exc, _ms(started))
        return base

    recent_temp_mean = _mean(recent_temp)
    baseline_temp_mean = _mean(baseline_temp)
    recent_precip_mean = _mean(recent_precip)
    baseline_precip_mean = _mean(baseline_precip)

    temp_anomaly = None
    if recent_temp_mean is not None and baseline_temp_mean is not None:
        temp_anomaly = round(recent_temp_mean - baseline_temp_mean, 2)

    precip_anomaly = None
    if recent_precip_mean is not None and baseline_precip_mean is not None:
        precip_anomaly = round(recent_precip_mean - baseline_precip_mean, 2)

    drought = precip_anomaly is not None and precip_anomaly < -2.0
    extremes = sum(
        1
        for r in recent_temp
        if isinstance(r.get("value"), (int, float)) and (r["value"] > 40 or r["value"] < -20)
    )

    base.update(
        {
            "temp_anomaly_c": temp_anomaly,
            "precipitation_anomaly_mm": precip_anomaly,
            "drought_stress_flag": drought,
            "recent_extremes_count": extremes,
            "data_period": (
                f"{recent_start.isoformat()} to {recent_end.isoformat()} "
                f"vs {baseline_start.isoformat()} to {baseline_end.isoformat()}"
            ),
            "climate_risk_level": _classify_climate(temp_anomaly, drought, extremes),
        }
    )
    logger.info(
        "NOAA: temp_anomaly=%s precip_anomaly=%s extremes=%d (%dms)",
        temp_anomaly,
        precip_anomaly,
        extremes,
        _ms(started),
    )
    return base
