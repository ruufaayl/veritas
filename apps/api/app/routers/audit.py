"""Audit orchestration + Server-Sent Events streaming.

Flow:
  POST /audit/start      -> kicks off background pipeline, returns audit_id
  GET  /audit/{id}/stream-> SSE stream of pipeline progress events
  GET  /audit/{id}       -> stored audit dict (200) or 202 if still running
  GET  /audit/history    -> last 20 audits

In-flight audits live in ACTIVE_AUDITS as asyncio.Queue instances. The
background task pushes progress events into the queue; the SSE endpoint
reads from the queue and yields `data: <json>\\n\\n` frames. A None
sentinel signals the stream to close.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import desc, select

from app.core.database import AsyncSessionLocal
from app.models.audit import Audit
import httpx

from app.services import (
    claude_narrative,
    global_forest_watch,
    groq_scorer,
    nasa_firms,
    noaa_climate,
    registry_scraper,
    sentinel_hub,
)
# Re-use the scraper's Nominatim helper so we have one country-geocode
# implementation, not two. (Acceptable use of an underscore-prefixed
# binding — registry_scraper.py is owned by us and stable.)
from app.services.registry_scraper import _geocode_country

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/audit", tags=["audit"])

# audit_id -> asyncio.Queue[dict | None]   (None = end-of-stream sentinel)
ACTIVE_AUDITS: dict[str, asyncio.Queue] = {}
# audit_id -> final assembled dict, kept briefly for late /audit/{id} calls
# even when the DB is unavailable
_RECENT_RESULTS: dict[str, dict[str, Any]] = {}
_RECENT_LIMIT = 50

TOTAL_STEPS = 8


class AuditStartRequest(BaseModel):
    serial: str = Field(..., min_length=3)


class AuditStartResponse(BaseModel):
    audit_id: str
    message: str = "Audit initiated"


# ────────────────────────────────────────────────────────────────────────────
# Helpers


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _progress(step: int) -> int:
    return int(round(100 * step / TOTAL_STEPS))


async def _emit(
    queue: asyncio.Queue,
    *,
    step: int,
    step_name: str,
    status_: str,
    message: str = "",
    data: dict | None = None,
    extra: dict | None = None,
) -> None:
    payload = {
        "step": step,
        "total_steps": TOTAL_STEPS,
        "step_name": step_name,
        "status": status_,
        "data": data,
        "progress_pct": _progress(step) if status_ == "complete" else _progress(max(step - 1, 0)),
        "message": message,
        "ts": _now_iso(),
    }
    if extra:
        payload.update(extra)
    await queue.put(payload)


def _trim_recent_cache() -> None:
    if len(_RECENT_RESULTS) > _RECENT_LIMIT:
        for k in list(_RECENT_RESULTS.keys())[: -_RECENT_LIMIT]:
            _RECENT_RESULTS.pop(k, None)


async def _save_audit(audit_id: str, payload: dict[str, Any]) -> None:
    """Best-effort DB write. Logs and continues on any failure (so the
    SSE stream still completes when Postgres isn't running locally)."""
    try:
        async with AsyncSessionLocal() as session:
            project = payload.get("project") or {}
            risk = payload.get("risk_score") or {}
            audit = Audit(
                id=uuid.UUID(audit_id),
                serial=project.get("serial") or "",
                project_name=project.get("project_name"),
                registry=project.get("registry"),
                country=project.get("country"),
                lat=project.get("lat"),
                lon=project.get("lon"),
                overall_score=risk.get("overall_score"),
                risk_level=risk.get("risk_level"),
                recommendation=risk.get("recommendation"),
                fire_data=payload.get("fire_data"),
                vegetation_data=payload.get("vegetation_data"),
                forest_data=payload.get("forest_data"),
                climate_data=payload.get("climate_data"),
                risk_breakdown=risk.get("component_scores"),
                narrative=payload.get("narrative"),
                pdf_url=payload.get("pdf_url"),
                pdf_ready=bool(payload.get("pdf_ready")),
                audit_duration_ms=payload.get("audit_duration_ms"),
            )
            session.add(audit)
            await session.commit()
            logger.info("audit %s persisted", audit_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("audit %s persist skipped: %s", audit_id, exc)


# ────────────────────────────────────────────────────────────────────────────
# The pipeline itself


async def _run_pipeline(audit_id: str, serial: str, queue: asyncio.Queue) -> None:
    started_total = time.perf_counter()
    assembled: dict[str, Any] = {
        "audit_id": audit_id,
        "serial": serial,
        "started_at": _now_iso(),
    }

    try:
        # ── Step 1: Registry lookup ─────────────────────────────────────
        await _emit(queue, step=1, step_name="Searching registry", status_="running",
                    message=f"Looking up {serial}")
        project = await registry_scraper.lookup_serial(serial)
        assembled["project"] = project

        if not project.get("found"):
            err_msg = (
                "Serial not found in registry cache. Try a GS- serial or "
                "wait for Verra cache refresh."
            )
            await _emit(queue, step=1, step_name="Searching registry", status_="error",
                        message=err_msg, data=project,
                        extra={"final": True, "http_status": 404, "error": err_msg})
            assembled["status"] = "not_found"
            assembled["error"] = err_msg
            assembled["http_status"] = 404
            _RECENT_RESULTS[audit_id] = assembled
            _trim_recent_cache()
            return

        await _emit(queue, step=1, step_name="Searching registry", status_="complete",
                    message=f"Found: {project.get('project_name') or '(unnamed)'}",
                    data={"registry": project.get("registry"),
                          "project_name": project.get("project_name"),
                          "country": project.get("country"),
                          "data_source": project.get("data_source")})

        # ── Step 2: Coordinate verification ─────────────────────────────
        lat, lon = project.get("lat"), project.get("lon")
        country = project.get("country")
        approximate = bool(project.get("coordinates_approximate"))

        await _emit(queue, step=2, step_name="Verifying coordinates", status_="running",
                    message="Validating project geocoordinates")

        # If the registry didn't yield coords but did give us a country,
        # geocode it ourselves. The scraper already tries this, but this
        # is a defensive second pass — and it keeps the policy in one
        # place even when the project comes from the SQLite cache or a
        # future registry adapter that doesn't geocode.
        if (not isinstance(lat, (int, float)) or not isinstance(lon, (int, float))) and country:
            try:
                async with httpx.AsyncClient(
                    timeout=15.0, follow_redirects=True,
                    headers={"User-Agent": "VeritasOracle/1.0"},
                ) as client:
                    g_lat, g_lon = await _geocode_country(client, country)
                if isinstance(g_lat, float) and isinstance(g_lon, float):
                    lat, lon = g_lat, g_lon
                    approximate = True
                    project["lat"], project["lon"] = lat, lon
                    project["coordinates_approximate"] = True
            except Exception as exc:  # noqa: BLE001
                logger.warning("step 2 country-geocode failed: %s", exc)

        coords_ok = isinstance(lat, (int, float)) and isinstance(lon, (int, float))
        if coords_ok:
            label = "approximate (country centroid)" if approximate else "exact (project polygon)"
            await _emit(queue, step=2, step_name="Verifying coordinates", status_="complete",
                        message=f"Coordinates: {lat:.4f}, {lon:.4f} — {label}",
                        data={"lat": lat, "lon": lon, "approximate": approximate,
                              "country": country})
        else:
            # No registry coords AND no country to fall back on — only
            # then do we skip geo-services. The user instruction was:
            # don't skip on missing coords, geocode the country first.
            await _emit(queue, step=2, step_name="Verifying coordinates", status_="complete",
                        message=("No coordinates and no country in registry response — "
                                 "geo-services will be skipped"),
                        data={"lat": None, "lon": None, "skipped_downstream": True,
                              "country": None})

        # All four geo-services share the same approximate-coords envelope:
        # if `approximate=True`, the lat/lon are a country centroid and the
        # service result is regional, not project-specific. Mark every
        # result with `approximate: <bool>` so the UI can label it.
        approx_suffix = " (regional, country centroid)" if approximate else ""

        # ── Step 3: NASA FIRMS ──────────────────────────────────────────
        await _emit(queue, step=3, step_name="Scanning NASA fire data", status_="running",
                    message=f"Querying VIIRS + MODIS hotspots{approx_suffix}")
        if coords_ok:
            fire_data = await nasa_firms.get_fire_alerts(lat, lon)
        else:
            fire_data = {"error": "skipped: no coordinates and no country",
                         "hotspot_count": 0}
        fire_data["approximate"] = approximate and coords_ok
        assembled["fire_data"] = fire_data
        await _emit(queue, step=3, step_name="Scanning NASA fire data", status_="complete",
                    message=(f"{fire_data.get('hotspot_count', 0)} hotspots "
                             f"(nearest {fire_data.get('nearest_fire_km')} km){approx_suffix}"),
                    data=fire_data)

        # ── Step 4: Sentinel image ──────────────────────────────────────
        await _emit(queue, step=4, step_name="Pulling satellite imagery", status_="running",
                    message=f"Fetching Sentinel-2 true-color tile{approx_suffix}")
        if coords_ok:
            try:
                image_b64 = await sentinel_hub.get_satellite_image_b64(lat, lon)
            except Exception as exc:  # noqa: BLE001
                logger.warning("sentinel image failed: %s", exc)
                image_b64 = None
        else:
            image_b64 = None
        assembled["satellite_image_b64"] = image_b64
        await _emit(queue, step=4, step_name="Pulling satellite imagery", status_="complete",
                    message=("Imagery acquired" if image_b64 else "Imagery unavailable") + approx_suffix,
                    data={"image_acquired": bool(image_b64),
                          "size_bytes": len(image_b64) if image_b64 else 0,
                          "approximate": approximate and bool(image_b64)})

        # ── Step 5: NDVI ────────────────────────────────────────────────
        await _emit(queue, step=5, step_name="Analyzing vegetation index", status_="running",
                    message=f"Computing 3-window NDVI delta{approx_suffix}")
        if coords_ok:
            vegetation_data = await sentinel_hub.get_ndvi_timeseries(lat, lon)
        else:
            vegetation_data = {"error": "skipped: no coordinates and no country",
                               "health_status": "UNKNOWN"}
        vegetation_data["approximate"] = approximate and coords_ok
        assembled["vegetation_data"] = vegetation_data
        await _emit(queue, step=5, step_name="Analyzing vegetation index", status_="complete",
                    message=(f"NDVI {vegetation_data.get('current_ndvi')} "
                             f"({vegetation_data.get('health_status')}){approx_suffix}"),
                    data=vegetation_data)

        # ── Step 6: Forest cover ────────────────────────────────────────
        await _emit(queue, step=6, step_name="Checking forest cover", status_="running",
                    message=f"Querying Global Forest Watch tree-cover-loss{approx_suffix}")
        if coords_ok:
            forest_data = await global_forest_watch.get_forest_loss(lat, lon)
        else:
            forest_data = {"error": "skipped: no coordinates and no country",
                           "trend": "STABLE"}
        forest_data["approximate"] = approximate and coords_ok
        assembled["forest_data"] = forest_data
        await _emit(queue, step=6, step_name="Checking forest cover", status_="complete",
                    message=(f"5-yr loss {forest_data.get('total_loss_5yr_ha')} ha "
                             f"({forest_data.get('trend')}){approx_suffix}"),
                    data=forest_data)

        # ── (silent) NOAA climate ──────────────────────────────────────
        # No visible step in the spec, but groq_scorer needs climate signal,
        # so fetch it between forest and AI scoring.
        if coords_ok:
            climate_data = await noaa_climate.get_climate_data(lat, lon)
        else:
            climate_data = {"error": "skipped: no coordinates and no country",
                            "climate_risk_level": "LOW"}
        climate_data["approximate"] = approximate and coords_ok
        assembled["climate_data"] = climate_data

        # ── Step 7: Groq risk score ─────────────────────────────────────
        await _emit(queue, step=7, step_name="Running AI risk model", status_="running",
                    message="Synthesizing signals via Llama 3.3 70B")
        risk_score = await groq_scorer.calculate_risk_score(
            project, fire_data, vegetation_data, forest_data, climate_data
        )
        assembled["risk_score"] = risk_score
        await _emit(queue, step=7, step_name="Running AI risk model", status_="complete",
                    message=(f"Score {risk_score.get('overall_score')}/100 "
                             f"({risk_score.get('risk_level')}) -> "
                             f"{risk_score.get('recommendation')}"),
                    data=risk_score)

        # ── Step 8: Claude narrative ────────────────────────────────────
        await _emit(queue, step=8, step_name="Generating audit narrative", status_="running",
                    message="Drafting Bloomberg-style verdict via Claude")
        narrative = await claude_narrative.generate_narrative(
            {**project, "fire_data": fire_data, "vegetation_data": vegetation_data,
             "forest_data": forest_data, "climate_data": climate_data},
            risk_score,
        )
        assembled["narrative"] = narrative

        # Final assembly + persistence
        assembled["audit_duration_ms"] = int((time.perf_counter() - started_total) * 1000)
        assembled["pdf_ready"] = False
        assembled["pdf_url"] = None
        assembled["completed_at"] = _now_iso()
        assembled["status"] = "complete"

        await _save_audit(audit_id, assembled)
        _RECENT_RESULTS[audit_id] = assembled
        _trim_recent_cache()

        await _emit(queue, step=8, step_name="Generating audit narrative", status_="complete",
                    message="Audit complete", data={"executive_summary": narrative.get("executive_summary")},
                    extra={"final": True, "result": assembled})

    except Exception as exc:  # noqa: BLE001
        logger.exception("audit %s pipeline crashed", audit_id)
        await _emit(queue, step=0, step_name="Pipeline failure", status_="error",
                    message=f"Unexpected error: {exc}",
                    extra={"final": True, "error": str(exc)})
    finally:
        await queue.put(None)  # SSE stream sentinel


# ────────────────────────────────────────────────────────────────────────────
# Routes (specific paths first, then /{audit_id})


@router.post("/start", response_model=AuditStartResponse, status_code=202)
async def start_audit(req: AuditStartRequest) -> AuditStartResponse:
    audit_id = str(uuid.uuid4())
    queue: asyncio.Queue = asyncio.Queue()
    ACTIVE_AUDITS[audit_id] = queue
    asyncio.create_task(_run_pipeline(audit_id, req.serial, queue))
    logger.info("audit %s started for serial %s", audit_id, req.serial)
    return AuditStartResponse(audit_id=audit_id, message="Audit initiated")


@router.get("/history")
async def history() -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Audit).order_by(desc(Audit.created_at)).limit(20)
            )
            for audit in result.scalars().all():
                rows.append(
                    {
                        "audit_id": str(audit.id),
                        "serial": audit.serial,
                        "project_name": audit.project_name,
                        "country": audit.country,
                        "overall_score": audit.overall_score,
                        "risk_level": audit.risk_level,
                        "recommendation": audit.recommendation,
                        "created_at": audit.created_at.isoformat() if audit.created_at else None,
                    }
                )
    except Exception as exc:  # noqa: BLE001
        # DB unavailable — fall back to in-memory recent cache so the
        # endpoint stays useful for local development.
        logger.warning("history: DB unavailable (%s) — using in-memory recent cache", exc)
        for audit_id, payload in list(_RECENT_RESULTS.items())[-20:]:
            project = payload.get("project") or {}
            risk = payload.get("risk_score") or {}
            rows.append(
                {
                    "audit_id": audit_id,
                    "serial": project.get("serial") or payload.get("serial"),
                    "project_name": project.get("project_name"),
                    "country": project.get("country"),
                    "overall_score": risk.get("overall_score"),
                    "risk_level": risk.get("risk_level"),
                    "recommendation": risk.get("recommendation"),
                    "created_at": payload.get("completed_at") or payload.get("started_at"),
                }
            )
    return {"audits": rows, "count": len(rows)}


@router.get("/{audit_id}/stream")
async def stream_audit(audit_id: str):
    if audit_id not in ACTIVE_AUDITS:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Unknown audit_id (or stream already closed)",
        )

    queue = ACTIVE_AUDITS[audit_id]

    async def gen():
        try:
            yield "retry: 1500\n\n"  # SSE reconnect hint
            while True:
                event = await queue.get()
                if event is None:  # sentinel — stream done
                    yield "event: end\ndata: {}\n\n"
                    return
                yield f"data: {json.dumps(event, default=str)}\n\n"
        finally:
            ACTIVE_AUDITS.pop(audit_id, None)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.get("/{audit_id}")
async def get_audit(audit_id: str) -> dict[str, Any]:
    # Still running?
    if audit_id in ACTIVE_AUDITS:
        return {"status": "running", "audit_id": audit_id}

    # Recently completed but DB write may have failed — check in-memory cache
    cached = _RECENT_RESULTS.get(audit_id)
    if cached:
        # If pipeline ended with not_found, surface as 404 per spec
        if cached.get("status") == "not_found":
            raise HTTPException(status_code=404, detail=cached.get("error"))
        return cached

    # DB lookup
    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Audit).where(Audit.id == uuid.UUID(audit_id))
            )
            audit = result.scalar_one_or_none()
            if audit is None:
                raise HTTPException(status_code=404, detail="Audit not found")
            return {
                "audit_id": str(audit.id),
                "serial": audit.serial,
                "project": {
                    "project_name": audit.project_name,
                    "registry": audit.registry,
                    "country": audit.country,
                    "lat": audit.lat,
                    "lon": audit.lon,
                },
                "risk_score": {
                    "overall_score": audit.overall_score,
                    "risk_level": audit.risk_level,
                    "recommendation": audit.recommendation,
                    "component_scores": audit.risk_breakdown,
                },
                "fire_data": audit.fire_data,
                "vegetation_data": audit.vegetation_data,
                "forest_data": audit.forest_data,
                "climate_data": audit.climate_data,
                "narrative": audit.narrative,
                "pdf_ready": audit.pdf_ready,
                "pdf_url": audit.pdf_url,
                "audit_duration_ms": audit.audit_duration_ms,
                "created_at": audit.created_at.isoformat() if audit.created_at else None,
            }
    except HTTPException:
        raise
    except ValueError:
        raise HTTPException(status_code=400, detail="audit_id is not a valid UUID")
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_audit: DB unavailable (%s)", exc)
        raise HTTPException(status_code=404, detail="Audit not found (no persistence)")
