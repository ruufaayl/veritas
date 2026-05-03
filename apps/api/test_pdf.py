"""Smoke test + preview regenerator for the PDF / HTML viewer.

Usage:
    cd apps/api
    python test_pdf.py                                  # run as script
    # or, from anywhere:
    python -c "exec(open('apps/api/test_pdf.py').read())"

Writes apps/api/preview.html using the *interactive* viewer (the same
HTML that GET /report/{audit_id}.html serves). Open it in a browser
to iterate on layout — no uvicorn, no Docker required.

The PDF path (render_pdf_bytes) is exercised separately. On Windows
hosts WeasyPrint 503s; the Linux Docker image is the production path.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Allow running both as `python apps/api/test_pdf.py` and via exec(open(...)).
HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from app.services import pdf_generator  # noqa: E402


PAYLOAD: dict = {
    "audit_id": "0c3f2e0d-1234-5678-9abc-def012345678",
    "project": {
        "serial": "VCS-99999",
        "project_name": "Synthetic Atlantic Forest Conservation",
        "registry": "Verra (VCS)",
        "country": "Brazil",
        "lat": -10.3333,
        "lon": -53.2,
        "coordinates_approximate": True,
        "methodology": "VM0007 REDD+",
        "credits_issued": 1234567,
        "last_verification_date": "2024-08-12",
        "data_source": "Verra cache (synthetic)",
        "developer": "Acme Forest Co.",
        "vintage_year": 2023,
    },
    "fire_data": {
        "hotspot_count": 12,
        "high_confidence_count": 4,
        "fire_risk_flag": True,
        "nearest_fire_km": 8.4,
        "most_recent_fire_date": "2026-04-30",
        "source": "NASA FIRMS · VIIRS_SNPP_NRT",
        "approximate": True,
    },
    "vegetation_data": {
        "current_ndvi": 0.62,
        "ndvi_1yr_ago": 0.66,
        "ndvi_3yr_ago": 0.71,
        "vegetation_change_pct": -12.7,
        "health_status": "DEGRADED",
        "approximate": True,
    },
    "forest_data": {
        "annual_loss_ha": [
            {"year": 2020, "loss_ha": 380},
            {"year": 2021, "loss_ha": 412},
            {"year": 2022, "loss_ha": 520},
            {"year": 2023, "loss_ha": 615},
            {"year": 2024, "loss_ha": 493},
        ],
        "total_loss_5yr_ha": 2420,
        "annual_loss_rate_pct": 1.8,
        "intact_forest_pct": 71,
        "trend": "DECLINING",
        "approximate": True,
    },
    "climate_data": {
        "temp_anomaly_c": 1.42,
        "precipitation_anomaly_mm": -180,
        "climate_risk_level": "MEDIUM",
        "nearest_station": "BR-MT-CUIABA-INMET-83361",
        "approximate": True,
    },
    "risk_score": {
        "overall_score": 58,
        "risk_level": "MEDIUM",
        "recommendation": "PARTIAL_FLAG",
        "confidence": 0.82,
        "component_scores": {
            "fire_risk": 45,
            "vegetation_integrity": 62,
            "deforestation_risk": 40,
            "climate_stress": 65,
            "registry_compliance": 80,
        },
        "key_flags": [
            "Fire density elevated within 10km in last 90 days",
            "12.7% NDVI decline over 3-year window",
            "Forest loss trend DECLINING — 5yr loss 2,420 ha",
        ],
        "positive_indicators": [
            "Verra registry record current — verified 2024-08-12",
            "Intact forest still 71% of project area",
        ],
        "audit_summary": "Project shows degradation pressure in last 12mo.",
    },
    "narrative": {
        "executive_summary": (
            "Project Atlantic Forest Conservation shows medium integrity. "
            "NDVI delta and accelerating tree-cover loss in the broader "
            "region are the dominant drag on the score; the registry record "
            "itself is current and clean."
        ),
        "fire_analysis": (
            "12 hotspots in the trailing 90-day window with 4 at high VIIRS "
            "confidence. Nearest event ~8km — close enough that a partial "
            "flag is warranted pending boundary-precision verification."
        ),
        "vegetation_analysis": (
            "NDVI declined 0.71 → 0.62 over three years, a 12.7% drop. "
            "The trajectory is consistent with a degraded but recoverable "
            "canopy. Recovery is possible if fire pressure abates."
        ),
        "climate_context": (
            "Temperature anomaly +1.42°C and precipitation deficit of 180mm "
            "raise additionality concerns — baseline carbon stock is shifting "
            "under climate stress, which complicates counterfactual modelling."
        ),
        "registry_assessment": (
            "Verra record current, methodology VM0007 in good standing. "
            "No outstanding issuance freezes or buffer pool flags."
        ),
        "recommendation_text": (
            "Issue PARTIAL FLAG. Hold 15% of pending vintage in buffer pool "
            "pending project-boundary fire verification and a follow-up NDVI "
            "check in 6 months."
        ),
        "methodology_note": (
            "All geo-services were run against the country centroid "
            "(Nominatim) because the registry record did not include "
            "project polygon coordinates."
        ),
    },
    "audit_duration_ms": 8420,
    "satellite_image_b64": None,
}


def main() -> None:
    html = pdf_generator._build_preview_html(PAYLOAD)
    out = HERE / "preview.html"
    out.write_text(html, encoding="utf-8")
    size_kb = len(html.encode("utf-8")) / 1024
    print(f"Wrote {out} ({size_kb:.1f} KB)")
    if size_kb < 30:
        print(
            f"WARNING: preview is only {size_kb:.1f} KB; expected >30 KB. "
            "Did the template short-circuit?"
        )


main()
