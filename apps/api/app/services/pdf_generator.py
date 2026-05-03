"""WeasyPrint PDF rendering for completed audit reports.

**Deployment:** This service assumes a Linux runtime with the GTK/cairo/
pango stack already on the system (libpango-1.0-0, libcairo2, etc).
That's exactly what the api Dockerfile installs — no extra setup is
needed when the API is deployed via Docker on Railway, Fly, Render, etc.

On Windows hosts WeasyPrint will fail to import the gobject-2.0 native
library at first call. We guard the WeasyPrint import so the rest of
the API still boots, and the PDF endpoint returns a clear 503 instead
of a 500 stack trace.
"""
from __future__ import annotations

import html as html_lib
import logging
import time
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# Lazy import — WeasyPrint won't load on Windows hosts without GTK.
# We capture the import error once and surface it cleanly when callers
# request a PDF, instead of crashing the whole module on first import.
_WEASY_AVAILABLE = True
_WEASY_IMPORT_ERROR: str | None = None
try:
    from weasyprint import HTML  # type: ignore
except Exception as exc:  # noqa: BLE001
    HTML = None  # type: ignore
    _WEASY_AVAILABLE = False
    _WEASY_IMPORT_ERROR = str(exc)
    logger.warning(
        "WeasyPrint unavailable on this host (%s). PDF generation will "
        "return 503 — works in the Linux Docker image.", exc,
    )


class PdfUnavailableError(RuntimeError):
    """Raised when WeasyPrint can't run on this host (e.g. Windows w/o GTK).
    Caller should map to HTTP 503."""


def _ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def _esc(value: Any) -> str:
    if value is None:
        return ""
    return html_lib.escape(str(value))


def _risk_color(level: str | None) -> str:
    return {
        "LOW":      "#2f7d4d",
        "MEDIUM":   "#a36a16",
        "HIGH":     "#a83232",
        "CRITICAL": "#5a0a0a",
    }.get((level or "").upper(), "#444")


# ────────────────────────────────────────────────────────────────────────────
# HTML template — single string, deliberately self-contained so we don't
# pull in jinja2 just for one document.

_CSS = """
@page { size: A4; margin: 18mm 16mm; }
body { font-family: 'Helvetica', sans-serif; color: #1a1a1a; font-size: 10.5pt; line-height: 1.45; }
h1 { font-size: 22pt; margin: 0 0 6pt; letter-spacing: -0.4pt; }
h2 { font-size: 14pt; margin: 18pt 0 6pt; border-bottom: 1px solid #ccc; padding-bottom: 3pt; }
h3 { font-size: 11pt; margin: 12pt 0 4pt; color: #555; text-transform: uppercase; letter-spacing: 0.5pt; }
.meta { color: #666; font-size: 9pt; margin-bottom: 18pt; }
.kv { display: table; width: 100%; border-collapse: collapse; margin: 8pt 0; }
.kv > div { display: table-row; }
.kv .k { display: table-cell; padding: 3pt 8pt 3pt 0; color: #666; width: 38%; }
.kv .v { display: table-cell; padding: 3pt 0; }
.score-banner {
  border: 2pt solid #888; border-radius: 4pt; padding: 10pt 14pt; margin: 12pt 0 18pt;
  display: table; width: 100%;
}
.score-banner .score { font-size: 36pt; font-weight: bold; line-height: 1; }
.score-banner .level { font-size: 14pt; text-transform: uppercase; letter-spacing: 1pt; }
.section p { margin: 4pt 0 8pt; }
.flag { background: #fff3e0; padding: 4pt 8pt; margin: 3pt 0; border-left: 3pt solid #e8a44a; }
.positive { background: #e8f5e9; padding: 4pt 8pt; margin: 3pt 0; border-left: 3pt solid #4caf7d; }
.footnote { font-size: 8.5pt; color: #777; margin-top: 24pt; border-top: 1px solid #ddd; padding-top: 6pt; }
.approximate { display: inline-block; background: #fff3cd; color: #6b5a16;
  padding: 1pt 5pt; border-radius: 2pt; font-size: 8.5pt; }
"""


def _kv_row(label: str, value: Any) -> str:
    return f'<div><div class="k">{_esc(label)}</div><div class="v">{_esc(value) or "—"}</div></div>'


def _section(title: str, body: str) -> str:
    return f'<div class="section"><h3>{_esc(title)}</h3><p>{_esc(body)}</p></div>'


def _flag_block(items: list[str], css_class: str) -> str:
    if not items:
        return ""
    return "".join(f'<div class="{css_class}">{_esc(item)}</div>' for item in items)


def _approx_badge(data: dict[str, Any] | None) -> str:
    if data and data.get("approximate"):
        return ' <span class="approximate">regional / country centroid</span>'
    return ""


def _build_html(payload: dict[str, Any]) -> str:
    project = payload.get("project") or {}
    risk = payload.get("risk_score") or {}
    fire = payload.get("fire_data") or {}
    veg = payload.get("vegetation_data") or {}
    forest = payload.get("forest_data") or {}
    climate = payload.get("climate_data") or {}
    narrative = payload.get("narrative") or {}

    overall = risk.get("overall_score")
    level = risk.get("risk_level") or "—"
    rec = risk.get("recommendation") or "—"
    color = _risk_color(level)
    confidence = risk.get("confidence")
    component = risk.get("component_scores") or {}

    coords_label = "—"
    if project.get("lat") is not None and project.get("lon") is not None:
        coords_label = f'{project["lat"]:.4f}, {project["lon"]:.4f}'
        if project.get("coordinates_approximate"):
            coords_label += " (approx)"

    flags_html = _flag_block(risk.get("key_flags") or [], "flag")
    positives_html = _flag_block(risk.get("positive_indicators") or [], "positive")

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>VERITAS audit {_esc(project.get("serial"))}</title>
<style>{_CSS}</style></head>
<body>

<h1>VERITAS Oracle — Audit Report</h1>
<div class="meta">
  Serial <strong>{_esc(project.get("serial"))}</strong> &middot;
  Audit ID <code>{_esc(payload.get("audit_id"))}</code> &middot;
  Generated {generated_at}
</div>

<div class="score-banner" style="border-color: {color}; color: {color};">
  <span class="score">{_esc(overall) if overall is not None else "—"}</span>
  <span style="color: #666; font-size: 11pt;"> / 100 &nbsp; · &nbsp; </span>
  <span class="level">{_esc(level)}</span>
  <span style="color: #666; font-size: 11pt; margin-left: 12pt;">
    Recommendation: <strong>{_esc(rec)}</strong>
    &nbsp; (confidence {f"{confidence:.0%}" if isinstance(confidence, (int, float)) else "—"})
  </span>
</div>

<h2>Project</h2>
<div class="kv">
  {_kv_row("Project name", project.get("project_name"))}
  {_kv_row("Registry", project.get("registry"))}
  {_kv_row("Country", project.get("country"))}
  {_kv_row("Coordinates", coords_label)}
  {_kv_row("Methodology", project.get("methodology"))}
  {_kv_row("Credits issued", project.get("credits_issued"))}
  {_kv_row("Last verification", project.get("last_verification_date"))}
  {_kv_row("Data source", project.get("data_source"))}
</div>

<h2>Executive summary</h2>
<p>{_esc(narrative.get("executive_summary") or risk.get("audit_summary"))}</p>

<h2>Component scores</h2>
<div class="kv">
  {_kv_row("Fire risk",            component.get("fire_risk"))}
  {_kv_row("Vegetation integrity", component.get("vegetation_integrity"))}
  {_kv_row("Deforestation risk",   component.get("deforestation_risk"))}
  {_kv_row("Climate stress",       component.get("climate_stress"))}
  {_kv_row("Registry compliance",  component.get("registry_compliance"))}
</div>

<h2>Key flags</h2>
{flags_html or "<p>No specific flags surfaced.</p>"}

<h2>Positive indicators</h2>
{positives_html or "<p>No positive indicators recorded.</p>"}

<h2>Fire analysis{_approx_badge(fire)}</h2>
{_section("Findings", narrative.get("fire_analysis", ""))}
<div class="kv">
  {_kv_row("Hotspot count", fire.get("hotspot_count"))}
  {_kv_row("High-confidence", fire.get("high_confidence_count"))}
  {_kv_row("Most recent fire", fire.get("most_recent_fire_date"))}
  {_kv_row("Nearest fire (km)", fire.get("nearest_fire_km"))}
  {_kv_row("Source", fire.get("source"))}
</div>

<h2>Vegetation analysis{_approx_badge(veg)}</h2>
{_section("Findings", narrative.get("vegetation_analysis", ""))}
<div class="kv">
  {_kv_row("Current NDVI", veg.get("current_ndvi"))}
  {_kv_row("NDVI 1yr ago", veg.get("ndvi_1yr_ago"))}
  {_kv_row("NDVI 3yr ago", veg.get("ndvi_3yr_ago"))}
  {_kv_row("Change %",     veg.get("vegetation_change_pct"))}
  {_kv_row("Health",       veg.get("health_status"))}
</div>

<h2>Forest cover{_approx_badge(forest)}</h2>
<div class="kv">
  {_kv_row("Total loss (5yr, ha)", forest.get("total_loss_5yr_ha"))}
  {_kv_row("Annual loss rate %",   forest.get("annual_loss_rate_pct"))}
  {_kv_row("Intact forest %",      forest.get("intact_forest_pct"))}
  {_kv_row("Trend",                forest.get("trend"))}
</div>

<h2>Climate context{_approx_badge(climate)}</h2>
{_section("Findings", narrative.get("climate_context", ""))}
<div class="kv">
  {_kv_row("Nearest station",  climate.get("nearest_station"))}
  {_kv_row("Temp anomaly °C",  climate.get("temp_anomaly_c"))}
  {_kv_row("Precip anomaly mm", climate.get("precipitation_anomaly_mm"))}
  {_kv_row("Climate risk",     climate.get("climate_risk_level"))}
</div>

<h2>Registry assessment</h2>
<p>{_esc(narrative.get("registry_assessment"))}</p>

<h2>Recommendation</h2>
<p>{_esc(narrative.get("recommendation_text"))}</p>

<div class="footnote">
  Methodology note: {_esc(narrative.get("methodology_note") or "Generated by VERITAS Oracle.")}<br>
  Audit duration: {_esc(payload.get("audit_duration_ms"))} ms.
  Approximate-coordinate sections used the country centroid (Nominatim) and reflect
  regional, not project-specific, signals.
</div>

</body></html>"""


def render_pdf_bytes(payload: dict[str, Any]) -> bytes:
    """Render the audit report HTML and return PDF bytes.

    Raises:
        PdfUnavailableError: WeasyPrint is not importable on this host
            (typically Windows without the GTK runtime). The deployed
            Docker image installs GTK so this never fires in production.
    """
    if not _WEASY_AVAILABLE:
        raise PdfUnavailableError(
            f"WeasyPrint unavailable: {_WEASY_IMPORT_ERROR}. "
            "PDF generation requires the Linux runtime in the api Dockerfile."
        )

    started = time.perf_counter()
    html_str = _build_html(payload)
    pdf_bytes = HTML(string=html_str).write_pdf()  # type: ignore[union-attr]
    logger.info("rendered audit pdf: %d bytes (%dms)", len(pdf_bytes), _ms(started))
    return pdf_bytes
