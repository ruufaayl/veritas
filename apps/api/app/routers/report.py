"""Audit PDF report endpoints.

GET /report/{audit_id}      -> application/pdf (Content-Disposition: attachment)
GET /report/{audit_id}.html -> raw HTML (handy for visual debugging)

Uses WeasyPrint via app.services.pdf_generator. WeasyPrint needs GTK
on the host; the api Dockerfile installs it. On a bare Windows host
the endpoint returns 503 with a clear message instead of a 500.
"""
from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.audit import Audit
from app.routers.audit import _RECENT_RESULTS, ACTIVE_AUDITS
from app.services import pdf_generator

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/report", tags=["report"])


async def _load_payload(audit_id: str) -> dict:
    """Resolve the assembled audit dict in the same order audit.py does:
    1. In-flight  -> 202
    2. Recent in-memory cache (DB may be down)
    3. Postgres
    4. 404
    """
    if audit_id in ACTIVE_AUDITS:
        raise HTTPException(status_code=status.HTTP_202_ACCEPTED,
                            detail="Audit still running")

    cached = _RECENT_RESULTS.get(audit_id)
    if cached:
        if cached.get("status") == "not_found":
            raise HTTPException(status_code=404, detail=cached.get("error"))
        return cached

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
                "project": {
                    "serial": audit.serial,
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
                "audit_duration_ms": audit.audit_duration_ms,
            }
    except HTTPException:
        raise
    except ValueError:
        raise HTTPException(status_code=400, detail="audit_id is not a valid UUID")
    except Exception as exc:  # noqa: BLE001
        logger.warning("report: DB lookup failed: %s", exc)
        raise HTTPException(status_code=404,
                            detail="Audit not found (no persistence layer)")


@router.get("/{audit_id}.html")
async def render_report_html(audit_id: str) -> Response:
    """Browser preview — interactive slide viewer (not the PDF layout).

    The PDF still uses pdf_generator._build_html() via render_pdf_bytes;
    this route serves a different, JS-driven, fullscreen presentation
    of the same 12 pages so layout iteration doesn't require Docker.
    """
    payload = await _load_payload(audit_id)
    html_str = pdf_generator._build_preview_html(payload)
    return Response(content=html_str, media_type="text/html; charset=utf-8")


@router.get("/{audit_id}")
async def download_report_pdf(audit_id: str) -> Response:
    payload = await _load_payload(audit_id)
    serial = (payload.get("project") or {}).get("serial") or "audit"
    try:
        pdf_bytes = pdf_generator.render_pdf_bytes(payload)
    except pdf_generator.PdfUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        )
    filename = f"veritas_{serial}_{audit_id[:8]}.pdf".replace("/", "_")
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
