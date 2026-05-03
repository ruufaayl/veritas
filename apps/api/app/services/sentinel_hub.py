"""Sentinel Hub satellite imagery and NDVI vegetation index.

Uses OAuth2 client-credentials, the Statistical API for NDVI windows, and
the Process API for true-color RGB tiles. Always returns a structured dict
or None; never raises.
"""
from __future__ import annotations

import base64
import logging
import time
from datetime import datetime, timedelta
from typing import Any

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

CLASSIC_AUTH_URL = "https://services.sentinel-hub.com/auth/realms/main/protocol/openid-connect/token"
CLASSIC_BASE = "https://services.sentinel-hub.com"
CDSE_AUTH_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
CDSE_BASE = "https://sh.dataspace.copernicus.eu"

# Resolved at runtime by get_auth_token(): "classic" or "cdse"
_active_realm: str = "classic"

NDVI_EVALSCRIPT = """//VERSION=3
function setup() { return { input: ["B04","B08","dataMask"], output: { bands: 1 } } }
function evaluatePixel(s) { return [(s.B08-s.B04)/(s.B08+s.B04)] }
"""

TRUECOLOR_EVALSCRIPT = """//VERSION=3
function setup() {
  return {
    input: [{ bands: ["B02","B03","B04"] }],
    output: { bands: 3, sampleType: "AUTO" }
  };
}
function evaluatePixel(s) { return [s.B04*2.5, s.B03*2.5, s.B02*2.5]; }
"""


def _ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def _bbox(lat: float, lon: float, half: float = 0.1) -> list[float]:
    return [lon - half, lat - half, lon + half, lat + half]


def _stats_endpoint() -> str:
    return f"{CDSE_BASE if _active_realm == 'cdse' else CLASSIC_BASE}/api/v1/statistics"


def _process_endpoint() -> str:
    return f"{CDSE_BASE if _active_realm == 'cdse' else CLASSIC_BASE}/api/v1/process"


async def get_auth_token() -> str:
    """Fetch a SentinelHub OAuth2 access token. Tries classic SH first,
    falls back to Copernicus Dataspace (CDSE). Returns "" on failure.
    """
    global _active_realm
    started = time.perf_counter()
    client_id = settings.SENTINEL_HUB_CLIENT_ID
    client_secret = settings.SENTINEL_HUB_CLIENT_SECRET
    if not (client_id and client_secret):
        logger.warning("sentinel auth: client id/secret not configured")
        return ""

    body = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
    }
    hdrs = {"Content-Type": "application/x-www-form-urlencoded"}

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            for realm, url in (("classic", CLASSIC_AUTH_URL), ("cdse", CDSE_AUTH_URL)):
                resp = await client.post(url, data=body, headers=hdrs)
                if resp.status_code == 200:
                    token = resp.json().get("access_token") or ""
                    if token:
                        _active_realm = realm
                        logger.info("sentinel auth ok via %s (%dms)", realm, _ms(started))
                        return token
                logger.info(
                    "sentinel auth %s -> HTTP %d (%s)",
                    realm,
                    resp.status_code,
                    resp.text[:120],
                )
    except Exception as exc:  # noqa: BLE001
        logger.warning("sentinel auth exception: %s (%dms)", exc, _ms(started))
        return ""
    logger.warning("sentinel auth: both realms refused (%dms)", _ms(started))
    return ""


def _stats_request(bbox: list[float], from_date: str, to_date: str) -> dict[str, Any]:
    return {
        "input": {
            "bounds": {
                "bbox": bbox,
                "properties": {"crs": "http://www.opengis.net/def/crs/EPSG/0/4326"},
            },
            "data": [
                {
                    "type": "sentinel-2-l2a",
                    "dataFilter": {"maxCloudCoverage": 30},
                }
            ],
        },
        "aggregation": {
            "timeRange": {
                "from": f"{from_date}T00:00:00Z",
                "to": f"{to_date}T23:59:59Z",
            },
            "aggregationInterval": {"of": "P30D"},
            "evalscript": NDVI_EVALSCRIPT,
            "resx": 0.0001,
            "resy": 0.0001,
        },
        "calculations": {
            "default": {"statistics": {"default": {"stats": ["mean"]}}}
        },
    }


def _extract_mean(payload: dict[str, Any]) -> float | None:
    try:
        for window in payload.get("data", []):
            outputs = window.get("outputs", {})
            for output in outputs.values():
                bands = output.get("bands", {})
                for band in bands.values():
                    stats = band.get("stats", {})
                    if "mean" in stats:
                        return float(stats["mean"])
    except (KeyError, TypeError, ValueError):
        return None
    return None


async def _ndvi_for_window(
    client: httpx.AsyncClient,
    token: str,
    bbox: list[float],
    from_date: str,
    to_date: str,
    label: str,
) -> float | None:
    started = time.perf_counter()
    try:
        response = await client.post(
            _stats_endpoint(),
            json=_stats_request(bbox, from_date, to_date),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
        if response.status_code != 200:
            logger.warning(
                "sentinel ndvi[%s] -> HTTP %d (%dms)",
                label,
                response.status_code,
                _ms(started),
            )
            return None
        mean = _extract_mean(response.json())
        logger.info(
            "sentinel ndvi[%s] -> %s (%dms)",
            label,
            f"{mean:.3f}" if mean is not None else "none",
            _ms(started),
        )
        return mean
    except Exception as exc:  # noqa: BLE001
        logger.warning("sentinel ndvi[%s] failed: %s", label, exc)
        return None


def _classify_health(current: float | None, change_pct: float | None) -> str:
    if current is None:
        return "UNKNOWN"
    change_pct = change_pct if change_pct is not None else 0
    if current >= 0.5 and change_pct > -10:
        return "HEALTHY"
    if current >= 0.3 and change_pct > -25:
        return "DEGRADED"
    return "CRITICAL"


async def get_ndvi_timeseries(lat: float, lon: float) -> dict:
    started = time.perf_counter()
    base = {
        "current_ndvi": None,
        "ndvi_1yr_ago": None,
        "ndvi_3yr_ago": None,
        "vegetation_change_pct": None,
        "deforestation_flag": False,
        "health_status": "UNKNOWN",
        "chart_data": [],
        "error": None,
    }

    token = await get_auth_token()
    if not token:
        base["error"] = "Sentinel Hub authentication failed"
        return base

    today = datetime.utcnow().date()
    bbox = _bbox(lat, lon)

    windows = {
        "current": (today - timedelta(days=30), today),
        "one_year_ago": (today - timedelta(days=395), today - timedelta(days=365)),
        "three_years_ago": (
            today - timedelta(days=365 * 3 + 30),
            today - timedelta(days=365 * 3),
        ),
    }

    results: dict[str, float | None] = {}
    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            for label, (start, end) in windows.items():
                results[label] = await _ndvi_for_window(
                    client, token, bbox, start.isoformat(), end.isoformat(), label
                )
    except Exception as exc:  # noqa: BLE001
        base["error"] = f"NDVI request failed: {exc}"
        return base

    current = results.get("current")
    one = results.get("one_year_ago")
    three = results.get("three_years_ago")

    change_pct = None
    if current is not None and three is not None and three != 0:
        change_pct = round(((current - three) / abs(three)) * 100, 2)

    base.update(
        {
            "current_ndvi": current,
            "ndvi_1yr_ago": one,
            "ndvi_3yr_ago": three,
            "vegetation_change_pct": change_pct,
            "deforestation_flag": change_pct is not None and change_pct < -15,
            "health_status": _classify_health(current, change_pct),
            "chart_data": [
                {"period": "3y_ago", "ndvi": three},
                {"period": "1y_ago", "ndvi": one},
                {"period": "current", "ndvi": current},
            ],
        }
    )
    logger.info(
        "sentinel ndvi summary: current=%s change=%s (%dms)",
        current,
        change_pct,
        _ms(started),
    )
    return base


async def _fetch_truecolor_b64(
    lat: float, lon: float,
    from_date: str, to_date: str,
    label: str,
) -> str | None:
    """Shared Process API call for true-color Sentinel-2 RGB tiles.

    Returns base64-encoded JPEG or None on any failure. `label` is a
    short tag used in logs to disambiguate concurrent requests
    (e.g. "current" vs "3yr_ago").
    """
    started = time.perf_counter()
    token = await get_auth_token()
    if not token:
        logger.warning("sentinel image[%s]: no token", label)
        return None

    body = {
        "input": {
            "bounds": {
                "bbox": _bbox(lat, lon, half=0.05),
                "properties": {"crs": "http://www.opengis.net/def/crs/EPSG/0/4326"},
            },
            "data": [
                {
                    "type": "sentinel-2-l2a",
                    "dataFilter": {
                        "timeRange": {
                            "from": f"{from_date}T00:00:00Z",
                            "to": f"{to_date}T23:59:59Z",
                        },
                        "maxCloudCoverage": 20,
                        "mosaickingOrder": "leastCC",
                    },
                }
            ],
        },
        "output": {
            "width": 512,
            "height": 512,
            "responses": [
                {"identifier": "default", "format": {"type": "image/jpeg"}}
            ],
        },
        "evalscript": TRUECOLOR_EVALSCRIPT,
    }

    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            response = await client.post(
                _process_endpoint(),
                json=body,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "Accept": "image/jpeg",
                },
            )
            if response.status_code != 200:
                logger.warning(
                    "sentinel image[%s] -> HTTP %d (%dms)",
                    label, response.status_code, _ms(started),
                )
                return None
            encoded = base64.b64encode(response.content).decode("ascii")
            logger.info(
                "sentinel image[%s]: %d bytes (%dms)",
                label, len(response.content), _ms(started),
            )
            return encoded
    except Exception as exc:  # noqa: BLE001
        logger.warning("sentinel image[%s] failed: %s", label, exc)
        return None


async def get_satellite_image_b64(lat: float, lon: float) -> str | None:
    """Most-recent true-color Sentinel-2 RGB tile (last 90 days)."""
    today = datetime.utcnow().date()
    from_date = (today - timedelta(days=90)).isoformat()
    to_date = today.isoformat()
    return await _fetch_truecolor_b64(lat, lon, from_date, to_date, "current")


async def get_historical_image_b64(
    lat: float, lon: float, years_ago: int = 3
) -> str | None:
    """Historical true-color tile from N years ago.

    Uses a 30-day window starting at the target date so the mosaicking
    pass has multiple revisits to pick the cleanest cloud-free image
    from. Never raises — returns None on auth failure, HTTP non-200,
    or transport error.
    """
    today = datetime.utcnow().date()
    start = today - timedelta(days=years_ago * 365)
    end = start + timedelta(days=30)
    return await _fetch_truecolor_b64(
        lat, lon,
        start.isoformat(), end.isoformat(),
        f"{years_ago}yr_ago",
    )
