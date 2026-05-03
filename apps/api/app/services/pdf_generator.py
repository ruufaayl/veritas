"""WeasyPrint PDF rendering for completed audit reports.

This module produces the 12-page client deliverable. It renders one
self-contained HTML document with inline SVG charts, and WeasyPrint
turns it into a PDF on the deployed Linux runtime.

**Public surface (do not break):**
    _build_html(payload)        -> str, used by /report/{id}.html preview
    render_pdf_bytes(payload)   -> bytes, used by /report/{id}
    PdfUnavailableError         -> raised on hosts without GTK/cairo/pango

**Layout strategy.** Each page is a `<section class="page">` sized to A4
with full-bleed dark background. Page breaks are forced via CSS so the
HTML preview shows the whole document scrollable, and the PDF still
paginates correctly. All charts are inline SVG — no canvas, no external
JS — so WeasyPrint and the browser preview render identically.

**Deployment.** WeasyPrint needs GTK/cairo/pango at runtime. The api
Dockerfile installs them. On Windows hosts the import guard below
captures the failure and the route returns 503 with a clean message
instead of a 500 stack trace.
"""
from __future__ import annotations

import html as html_lib
import logging
import math
import time
from datetime import datetime, timezone
from typing import Any

from app.core.config import settings

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


# ── tiny helpers ────────────────────────────────────────────────────────

def _ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def _esc(value: Any) -> str:
    if value is None:
        return ""
    return html_lib.escape(str(value))


def _fmt(value: Any, default: str = "—") -> str:
    if value is None or value == "":
        return default
    return _esc(value)


def _fmt_num(value: Any, suffix: str = "", default: str = "—") -> str:
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not value.is_integer():
            return f"{value:.2f}{suffix}"
        return f"{int(value)}{suffix}"
    return default


def _risk_color(level: str | None) -> str:
    return {
        "LOW":      "#4CAF7D",
        "MEDIUM":   "#E8A44A",
        "HIGH":     "#E85A4A",
        "CRITICAL": "#C42B2B",
    }.get((level or "").upper(), "#8c8c8c")


def _score_color(score: Any) -> str:
    if not isinstance(score, (int, float)):
        return "#8c8c8c"
    if score >= 70:
        return "#4CAF7D"
    if score >= 40:
        return "#E8A44A"
    if score >= 20:
        return "#E85A4A"
    return "#C42B2B"


def _approx_pill(approximate: bool) -> str:
    if not approximate:
        return ""
    return '<span class="approx-pill">⚠ Approximate · regional centroid</span>'


# ── inline SVG chart builders ───────────────────────────────────────────

def _score_ring_svg(score: Any, size: int = 320, stroke: int = 16) -> str:
    """Cover-page score ring. Big numerals, color-coded stroke."""
    if isinstance(score, (int, float)):
        s = max(0, min(100, int(score)))
        score_label = str(int(score))
    else:
        s = 0
        score_label = "—"
    color = _score_color(score)
    radius = size / 2 - stroke - 8
    circ = 2 * math.pi * radius
    dash = circ * s / 100
    return f"""
    <svg width="{size}" height="{size}" viewBox="0 0 {size} {size}" xmlns="http://www.w3.org/2000/svg">
      <circle cx="{size/2}" cy="{size/2}" r="{radius:.1f}" fill="none"
              stroke="rgba(245,240,232,0.08)" stroke-width="{stroke}"/>
      <circle cx="{size/2}" cy="{size/2}" r="{radius:.1f}" fill="none"
              stroke="{color}" stroke-width="{stroke}" stroke-linecap="round"
              stroke-dasharray="{dash:.2f} {circ - dash:.2f}"
              transform="rotate(-90 {size/2} {size/2})"/>
      <text x="50%" y="50%" text-anchor="middle" dominant-baseline="central"
            fill="{color}" font-family="'Playfair Display', Georgia, serif"
            font-size="{int(size*0.34)}" font-weight="700">{score_label}</text>
      <text x="50%" y="{size/2 + size*0.20:.0f}" text-anchor="middle"
            fill="rgba(245,240,232,0.5)" font-family="'IBM Plex Mono', monospace"
            font-size="{int(size*0.06)}" letter-spacing="3">/ 100</text>
    </svg>
    """


def _ndvi_chart_svg(veg: dict[str, Any]) -> str:
    """Three-bar NDVI chart: 3yr ago → 1yr ago → current."""
    series = [
        ("3 Years Ago", veg.get("ndvi_3yr_ago")),
        ("1 Year Ago",  veg.get("ndvi_1yr_ago")),
        ("Current",     veg.get("current_ndvi")),
    ]
    width, height = 600, 260
    pad_l, pad_r, pad_t, pad_b = 50, 30, 30, 60
    chart_w = width - pad_l - pad_r
    chart_h = height - pad_t - pad_b

    grids: list[str] = []
    for tick in (0.25, 0.5, 0.75, 1.0):
        gy = pad_t + chart_h * (1 - tick)
        grids.append(
            f'<line x1="{pad_l}" y1="{gy:.1f}" x2="{width - pad_r}" y2="{gy:.1f}" '
            f'stroke="rgba(245,240,232,0.06)" stroke-width="1"/>'
        )
        grids.append(
            f'<text x="{pad_l - 8}" y="{gy + 4:.1f}" text-anchor="end" '
            f'fill="rgba(245,240,232,0.4)" font-family="\'IBM Plex Mono\', monospace" '
            f'font-size="10">{tick:.2f}</text>'
        )

    bar_total_w = chart_w / len(series)
    bar_w = bar_total_w * 0.55
    bars: list[str] = []
    for i, (label, val) in enumerate(series):
        v = val if isinstance(val, (int, float)) else 0
        v = max(0.0, min(1.0, float(v)))
        bar_h = chart_h * v
        x = pad_l + i * bar_total_w + (bar_total_w - bar_w) / 2
        y = pad_t + chart_h - bar_h
        color = "#4CAF7D" if v > 0.5 else "#E8A44A" if v > 0.3 else "#E85A4A"
        bars.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" '
            f'height="{bar_h:.1f}" fill="{color}" rx="2"/>'
        )
        val_label = f"{val:.2f}" if isinstance(val, (int, float)) else "—"
        bars.append(
            f'<text x="{x + bar_w/2:.1f}" y="{max(y - 8, pad_t + 12):.1f}" '
            f'text-anchor="middle" fill="#F5F0E8" '
            f'font-family="\'IBM Plex Mono\', monospace" font-size="13">{val_label}</text>'
        )
        bars.append(
            f'<text x="{x + bar_w/2:.1f}" y="{height - 30:.1f}" text-anchor="middle" '
            f'fill="rgba(245,240,232,0.65)" font-family="\'IBM Plex Mono\', monospace" '
            f'font-size="11">{_esc(label)}</text>'
        )

    return (
        f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
        f'xmlns="http://www.w3.org/2000/svg">{"".join(grids + bars)}</svg>'
    )


def _forest_chart_svg(forest: dict[str, Any]) -> str:
    """Annual tree-cover-loss bar chart with overlaid trend line.

    Returns empty string if no series can be normalized.
    """
    raw = forest.get("annual_loss_ha")
    if not isinstance(raw, list) or not raw:
        return ""

    series: list[tuple[str, float]] = []
    n = len(raw)
    for i, row in enumerate(raw):
        if isinstance(row, (int, float)):
            series.append((f"Y-{n - i}", float(row)))
        elif isinstance(row, dict):
            label = str(row.get("year") or f"Y-{n - i}")
            v = row.get("loss_ha") or row.get("ha") or row.get("value") or 0
            series.append((label, float(v) if isinstance(v, (int, float)) else 0.0))

    if not series:
        return ""
    max_v = max((v for _, v in series), default=0) or 1.0

    width, height = 600, 260
    pad_l, pad_r, pad_t, pad_b = 60, 30, 30, 60
    chart_w = width - pad_l - pad_r
    chart_h = height - pad_t - pad_b

    grids: list[str] = []
    for tick in (0.25, 0.5, 0.75, 1.0):
        gy = pad_t + chart_h * (1 - tick)
        v_label = f"{int(max_v * tick)}"
        grids.append(
            f'<line x1="{pad_l}" y1="{gy:.1f}" x2="{width - pad_r}" y2="{gy:.1f}" '
            f'stroke="rgba(245,240,232,0.06)" stroke-width="1"/>'
        )
        grids.append(
            f'<text x="{pad_l - 8}" y="{gy + 4:.1f}" text-anchor="end" '
            f'fill="rgba(245,240,232,0.4)" font-family="\'IBM Plex Mono\', monospace" '
            f'font-size="10">{v_label}</text>'
        )

    bar_total_w = chart_w / len(series)
    bar_w = bar_total_w * 0.6
    parts: list[str] = []
    points: list[str] = []
    for i, (label, v) in enumerate(series):
        h = chart_h * (v / max_v)
        x = pad_l + i * bar_total_w + (bar_total_w - bar_w) / 2
        y = pad_t + chart_h - h
        parts.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" '
            f'height="{h:.1f}" fill="rgba(201,168,76,0.45)" rx="2"/>'
        )
        parts.append(
            f'<text x="{x + bar_w/2:.1f}" y="{height - 30:.1f}" text-anchor="middle" '
            f'fill="rgba(245,240,232,0.65)" font-family="\'IBM Plex Mono\', monospace" '
            f'font-size="11">{_esc(label)}</text>'
        )
        points.append(f"{x + bar_w/2:.1f},{y:.1f}")

    trend_line = (
        f'<polyline points="{" ".join(points)}" fill="none" '
        f'stroke="#E8C96A" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>'
        if len(points) > 1 else ""
    )

    return (
        f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
        f'xmlns="http://www.w3.org/2000/svg">{"".join(grids + parts)}{trend_line}</svg>'
    )


def _temp_anomaly_svg(climate: dict[str, Any]) -> str:
    """Horizontal gauge from -3°C to +3°C with marker for the anomaly."""
    anomaly = climate.get("temp_anomaly_c")
    width, height = 540, 130
    pad = 36
    bar_y, bar_h = 56, 14
    min_v, max_v = -3.0, 3.0

    grad = (
        '<defs><linearGradient id="temp-grad" x1="0" x2="1">'
        '<stop offset="0%" stop-color="#5fa8d3"/>'
        '<stop offset="50%" stop-color="#d4a347"/>'
        '<stop offset="100%" stop-color="#c42b2b"/>'
        '</linearGradient></defs>'
    )
    bar = (
        f'<rect x="{pad}" y="{bar_y}" width="{width - 2*pad}" '
        f'height="{bar_h}" fill="url(#temp-grad)" rx="7"/>'
    )

    ticks: list[str] = []
    for tick in (-3, -2, -1, 0, 1, 2, 3):
        tx = pad + ((tick - min_v) / (max_v - min_v)) * (width - 2 * pad)
        ticks.append(
            f'<line x1="{tx:.1f}" y1="{bar_y + bar_h}" x2="{tx:.1f}" '
            f'y2="{bar_y + bar_h + 5}" stroke="rgba(245,240,232,0.4)"/>'
        )
        ticks.append(
            f'<text x="{tx:.1f}" y="{bar_y + bar_h + 22}" text-anchor="middle" '
            f'fill="rgba(245,240,232,0.55)" font-family="\'IBM Plex Mono\', monospace" '
            f'font-size="10">{tick:+d}°C</text>'
        )

    marker = ""
    if isinstance(anomaly, (int, float)):
        a = max(min_v, min(max_v, float(anomaly)))
        mx = pad + ((a - min_v) / (max_v - min_v)) * (width - 2 * pad)
        marker = (
            f'<line x1="{mx:.1f}" y1="{bar_y - 6}" x2="{mx:.1f}" y2="{bar_y + bar_h + 6}" '
            f'stroke="#0a0a0a" stroke-width="3"/>'
            f'<circle cx="{mx:.1f}" cy="{bar_y + bar_h/2}" r="9" fill="#F5F0E8" '
            f'stroke="#0a0a0a" stroke-width="2"/>'
            f'<text x="{mx:.1f}" y="{bar_y - 14}" text-anchor="middle" fill="#F5F0E8" '
            f'font-family="\'Playfair Display\', Georgia, serif" font-size="22" '
            f'font-weight="600">{anomaly:+.2f}°C</text>'
        )

    return (
        f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
        f'xmlns="http://www.w3.org/2000/svg">{grad}{bar}{"".join(ticks)}{marker}</svg>'
    )


def _radar_svg(scores: dict[str, Any]) -> str:
    """Five-axis radar chart for component scores."""
    axes = [
        ("Fire risk",      scores.get("fire_risk")),
        ("Vegetation",     scores.get("vegetation_integrity")),
        ("Deforestation",  scores.get("deforestation_risk")),
        ("Climate stress", scores.get("climate_stress")),
        ("Compliance",     scores.get("registry_compliance")),
    ]
    size = 360
    cx = cy = size / 2
    r_max = size / 2 - 60
    n = len(axes)

    rings = []
    for ring in (0.25, 0.5, 0.75, 1.0):
        pts = []
        for i in range(n):
            ang = -math.pi / 2 + i * (2 * math.pi / n)
            pts.append(f"{cx + r_max * ring * math.cos(ang):.1f},"
                       f"{cy + r_max * ring * math.sin(ang):.1f}")
        rings.append(
            f'<polygon points="{" ".join(pts)}" fill="none" '
            f'stroke="rgba(245,240,232,0.08)" stroke-width="1"/>'
        )

    spokes = []
    for i in range(n):
        ang = -math.pi / 2 + i * (2 * math.pi / n)
        spokes.append(
            f'<line x1="{cx}" y1="{cy}" '
            f'x2="{cx + r_max * math.cos(ang):.1f}" '
            f'y2="{cy + r_max * math.sin(ang):.1f}" '
            f'stroke="rgba(245,240,232,0.08)" stroke-width="1"/>'
        )

    pts: list[str] = []
    labels: list[str] = []
    for i, (lab, val) in enumerate(axes):
        ang = -math.pi / 2 + i * (2 * math.pi / n)
        v = val if isinstance(val, (int, float)) else 0
        v_norm = max(0, min(100, v)) / 100
        x = cx + r_max * v_norm * math.cos(ang)
        y = cy + r_max * v_norm * math.sin(ang)
        pts.append(f"{x:.1f},{y:.1f}")
        lx = cx + (r_max + 28) * math.cos(ang)
        ly = cy + (r_max + 28) * math.sin(ang)
        labels.append(
            f'<text x="{lx:.1f}" y="{ly:.1f}" text-anchor="middle" '
            f'dominant-baseline="middle" fill="rgba(245,240,232,0.75)" '
            f'font-family="\'IBM Plex Mono\', monospace" font-size="11">{_esc(lab)}</text>'
            f'<text x="{lx:.1f}" y="{ly + 14:.1f}" text-anchor="middle" '
            f'dominant-baseline="middle" fill="#C9A84C" '
            f'font-family="\'IBM Plex Mono\', monospace" font-size="10">'
            f'{_fmt_num(val) if val is not None else "—"}</text>'
        )

    polygon = (
        f'<polygon points="{" ".join(pts)}" fill="rgba(201,168,76,0.18)" '
        f'stroke="#C9A84C" stroke-width="2" stroke-linejoin="round"/>'
    )
    dots = "".join(
        f'<circle cx="{p.split(",")[0]}" cy="{p.split(",")[1]}" r="3" fill="#E8C96A"/>'
        for p in pts
    )

    return (
        f'<svg width="{size}" height="{size}" viewBox="0 0 {size} {size}" '
        f'xmlns="http://www.w3.org/2000/svg">'
        f'{"".join(rings)}{"".join(spokes)}{polygon}{dots}{"".join(labels)}</svg>'
    )


def _component_bar(label: str, value: Any, weight_pct: int | None) -> str:
    """Horizontal score bar for the components page."""
    if isinstance(value, (int, float)):
        v = max(0, min(100, float(value)))
        v_label = str(int(value))
        color = _score_color(value)
    else:
        v = 0.0
        v_label = "—"
        color = "rgba(245,240,232,0.2)"
    weight_html = (
        f'<span class="cmp-weight">weight {weight_pct}%</span>'
        if weight_pct is not None else ""
    )
    return f"""
    <div class="cmp-bar">
      <div class="cmp-head">
        <span class="cmp-label">{_esc(label)}{weight_html}</span>
        <span class="cmp-value">{_esc(v_label)}<span class="cmp-of">/100</span></span>
      </div>
      <div class="cmp-track">
        <div class="cmp-fill" style="width:{v:.1f}%; background:{color};"></div>
      </div>
    </div>
    """


# ── reusable HTML fragments ─────────────────────────────────────────────

def _kv_row(label: str, value: Any) -> str:
    return (
        f'<tr><td class="kv-k">{_esc(label)}</td>'
        f'<td class="kv-v">{_fmt(value)}</td></tr>'
    )


def _section_heading(eyebrow: str, title: str) -> str:
    return f"""
    <header class="page-head">
      <span class="eyebrow">{_esc(eyebrow)}</span>
      <h2 class="page-title">{_esc(title)}</h2>
    </header>
    """


def _page_footer(page_num: int, total: int = 12) -> str:
    return f"""
    <footer class="page-foot">
      <span class="brand-mark">VERITAS ORACLE</span>
      <span class="page-num">{page_num:02d} / {total:02d}</span>
    </footer>
    """


# ── individual page builders ────────────────────────────────────────────

def _page_cover(payload: dict[str, Any]) -> str:
    project = payload.get("project") or {}
    risk = payload.get("risk_score") or {}
    overall = risk.get("overall_score")
    level = risk.get("risk_level") or "—"
    color = _risk_color(level)
    audit_date = datetime.now(timezone.utc).strftime("%d %B %Y")
    audit_id_short = (payload.get("audit_id") or "")[:8].upper() or "—"

    return f"""
    <section class="page page-cover">
      <header class="cover-head">
        <span class="wordmark">VERITAS<span class="wordmark-sub">ORACLE</span></span>
        <span class="confidential">CONFIDENTIAL · CARBON CREDIT INTEGRITY AUDIT</span>
      </header>

      <div class="cover-ring">{_score_ring_svg(overall, size=340)}</div>

      <div class="cover-meta">
        <div class="risk-pill" style="background:{color}; color:#0a0a0a;">
          {_esc(level).upper()}
        </div>
        <h1 class="cover-project">{_esc(project.get("project_name") or "Unnamed project")}</h1>
        <div class="cover-grid">
          <div><span class="ck">Country</span><span class="cv">{_fmt(project.get("country"))}</span></div>
          <div><span class="ck">Registry</span><span class="cv">{_fmt(project.get("registry"))}</span></div>
          <div><span class="ck">Serial</span><span class="cv">{_fmt(project.get("serial"))}</span></div>
          <div><span class="ck">Audit ID</span><span class="cv">{_esc(audit_id_short)}</span></div>
          <div><span class="ck">Audit date</span><span class="cv">{_esc(audit_date)}</span></div>
          <div><span class="ck">Recommendation</span><span class="cv">{_fmt(risk.get("recommendation"))}</span></div>
        </div>
      </div>

      <footer class="cover-foot">
        <span>CONFIDENTIAL AUDIT REPORT</span>
        <span>Generated by VERITAS Oracle · veritasoracle.vercel.app</span>
      </footer>
    </section>
    """


def _page_executive_summary(payload: dict[str, Any]) -> str:
    risk = payload.get("risk_score") or {}
    narrative = payload.get("narrative") or {}
    level = risk.get("risk_level") or "—"
    color = _risk_color(level)
    rec = risk.get("recommendation") or "—"
    confidence = risk.get("confidence")
    conf_text = (
        f"{int(confidence * 100)}%" if isinstance(confidence, (int, float)) else "—"
    )
    summary = narrative.get("executive_summary") or risk.get("audit_summary") \
        or "No executive summary returned by the model."

    flags = risk.get("key_flags") or []
    positives = risk.get("positive_indicators") or []

    flags_html = "".join(
        f'<li class="flag-item"><span class="flag-icon">⚠</span>{_esc(f)}</li>'
        for f in flags
    ) or '<li class="flag-empty">No specific flags surfaced.</li>'

    pos_html = "".join(
        f'<li class="pos-item"><span class="pos-icon">✓</span>{_esc(p)}</li>'
        for p in positives
    ) or '<li class="flag-empty">No positive indicators recorded.</li>'

    duration_ms = payload.get("audit_duration_ms")
    duration_s = (
        f"{duration_ms / 1000:.1f}s"
        if isinstance(duration_ms, (int, float)) else "—"
    )

    sources = []
    if (payload.get("fire_data") or {}).get("source"):
        sources.append(payload["fire_data"]["source"])
    if (payload.get("vegetation_data") or {}).get("current_ndvi") is not None:
        sources.append("Sentinel-2 NDVI")
    if (payload.get("forest_data") or {}).get("total_loss_5yr_ha") is not None:
        sources.append("Global Forest Watch")
    if (payload.get("climate_data") or {}).get("nearest_station"):
        sources.append("NOAA NCDC")
    sources_html = ", ".join(sources) or "—"

    return f"""
    <section class="page">
      {_section_heading("01 · Verdict", "Executive Summary")}

      <div class="es-row">
        <div class="risk-pill-lg" style="background:{color}; color:#0a0a0a;">
          {_esc(level).upper()} RISK
        </div>
        <div class="es-conf">
          <span class="ck">Confidence</span>
          <span class="cv">{_esc(conf_text)}</span>
        </div>
      </div>

      <div class="rec-box" style="border-color:{color};">
        <span class="rec-label">RECOMMENDATION</span>
        <span class="rec-value" style="color:{color};">{_esc(rec).upper()}</span>
        <p class="rec-text">{_esc(narrative.get("recommendation_text") or "—")}</p>
      </div>

      <h3 class="sub-h">Verdict</h3>
      <p class="prose">{_esc(summary)}</p>

      <div class="two-col">
        <div>
          <h3 class="sub-h">Key flags</h3>
          <ul class="flag-list">{flags_html}</ul>
        </div>
        <div>
          <h3 class="sub-h">Positive indicators</h3>
          <ul class="pos-list">{pos_html}</ul>
        </div>
      </div>

      <h3 class="sub-h">Audit metadata</h3>
      <table class="meta-table">
        <tr><td>Duration</td><td>{_esc(duration_s)}</td></tr>
        <tr><td>Data sources</td><td>{_esc(sources_html)}</td></tr>
        <tr><td>Confidence score</td><td>{_esc(conf_text)}</td></tr>
        <tr><td>Generated</td><td>{datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}</td></tr>
      </table>

      {_page_footer(2)}
    </section>
    """


def _page_project_identity(payload: dict[str, Any]) -> str:
    project = payload.get("project") or {}
    coord_label = "—"
    lat, lon = project.get("lat"), project.get("lon")
    if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
        coord_label = f"{lat:.4f}, {lon:.4f}"
        if project.get("coordinates_approximate"):
            coord_label += "  (country centroid)"

    rows = [
        ("Serial number",      project.get("serial")),
        ("Registry",           project.get("registry")),
        ("Project type",       project.get("project_type") or project.get("methodology_type")),
        ("Methodology",        project.get("methodology")),
        ("Country",            project.get("country")),
        ("Coordinates",        coord_label),
        ("Project area (ha)",  project.get("hectares") or project.get("area_ha")),
        ("Developer",          project.get("developer") or project.get("proponent")),
        ("Credits issued",     project.get("credits_issued")),
        ("Vintage year",       project.get("vintage") or project.get("vintage_year")),
        ("Last verification",  project.get("last_verification_date")),
        ("Status",             project.get("status") or "Active"),
    ]
    rows_html = "".join(_kv_row(k, v) for k, v in rows)

    registry_url = project.get("registry_url") or project.get("source_url") or "—"

    return f"""
    <section class="page">
      {_section_heading("02 · Identity", "Project Identity")}

      <p class="prose-muted">
        Pulled from {_esc(project.get("data_source") or project.get("registry") or "registry")}
        via VERITAS scrape/cache pipeline. Fields without a registry value are
        marked with an em-dash; downstream geo-services were run regardless.
      </p>

      <table class="kv-table"><tbody>{rows_html}</tbody></table>

      <h3 class="sub-h">Source link</h3>
      <code class="link-code">{_esc(registry_url)}</code>

      {_page_footer(3)}
    </section>
    """


def _satellite_image_block(image_b64: str | None, label: str, lat, lon) -> str:
    if image_b64:
        return f"""
        <div class="sat-tile">
          <img src="data:image/jpeg;base64,{image_b64}" alt="{_esc(label)}"/>
          <div class="sat-caption">{_esc(label)}</div>
        </div>
        """
    coord_text = (
        f"{lat:.4f}, {lon:.4f}" if isinstance(lat, (int, float)) and isinstance(lon, (int, float))
        else "no coordinates"
    )
    return f"""
    <div class="sat-tile sat-tile-empty">
      <div class="sat-empty">
        <span class="sat-empty-eyebrow">SATELLITE TILE UNAVAILABLE</span>
        <code>{_esc(coord_text)}</code>
      </div>
      <div class="sat-caption">{_esc(label)}</div>
    </div>
    """


def _page_satellite(payload: dict[str, Any]) -> str:
    project = payload.get("project") or {}
    lat, lon = project.get("lat"), project.get("lon")
    image_b64 = payload.get("satellite_image_b64")
    image_historical_b64 = payload.get("satellite_image_historical_b64")

    historical_label = (
        "3 Years Prior — historical Sentinel-2 acquisition"
        if image_historical_b64
        else "3 Years Prior — historical archive unavailable"
    )

    return f"""
    <section class="page">
      {_section_heading("03 · Imagery", "Satellite Verification")}

      <p class="prose-muted">
        ESA Sentinel-2 true-color (RGB) at 10m ground resolution. Side-by-side
        comparison: current vs. 3-year-prior cloud-filtered acquisitions for
        the project bounding box.
      </p>

      <div class="sat-grid">
        {_satellite_image_block(image_b64, "Current — most recent acquisition", lat, lon)}
        {_satellite_image_block(image_historical_b64, historical_label, lat, lon)}
      </div>

      <p class="source-note">
        ESA Sentinel-2 · 10m resolution · True Color RGB · Copernicus
        Dataspace Ecosystem (CDSE) realm
      </p>

      {_page_footer(4)}
    </section>
    """


def _mapbox_url(lat, lon, zoom: int = 8) -> str | None:
    token = settings.MAPBOX_TOKEN
    if not token or not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
        return None
    return (
        f"https://api.mapbox.com/styles/v1/mapbox/satellite-v9/static/"
        f"{lon},{lat},{zoom},0/700x400@2x?access_token={token}"
    )


def _page_fire(payload: dict[str, Any]) -> str:
    project = payload.get("project") or {}
    fire = payload.get("fire_data") or {}
    narrative = payload.get("narrative") or {}
    lat, lon = project.get("lat"), project.get("lon")
    approximate = bool(fire.get("approximate"))

    flagged = fire.get("fire_risk_flag")
    if flagged is None:
        flagged = (fire.get("high_confidence_count") or 0) > 0
    flag_color = "#E85A4A" if flagged else "#4CAF7D"
    flag_label = "FIRE RISK FLAGGED" if flagged else "NO ACTIVE FIRE RISK"

    map_zoom = 6 if approximate else 8
    map_url = _mapbox_url(lat, lon, zoom=map_zoom)
    map_block = (
        f'<img class="fire-map" src="{_esc(map_url)}" alt="Project satellite view"/>'
        if map_url else
        '<div class="fire-map fire-map-empty">'
        '<span>Mapbox tile unavailable — set MAPBOX_TOKEN</span>'
        '</div>'
    )

    return f"""
    <section class="page">
      {_section_heading("04 · Fire", "Thermal Anomaly & Fire Detection")}
      {_approx_pill(approximate)}

      <div class="fire-row">
        {map_block}
        <div class="fire-stats">
          <div class="stat-block">
            <span class="stat-k">Total hotspots (90d)</span>
            <span class="stat-v">{_fmt_num(fire.get("hotspot_count"))}</span>
          </div>
          <div class="stat-block">
            <span class="stat-k">High-confidence</span>
            <span class="stat-v">{_fmt_num(fire.get("high_confidence_count"))}</span>
          </div>
          <div class="stat-block">
            <span class="stat-k">Nearest fire</span>
            <span class="stat-v">{_fmt_num(fire.get("nearest_fire_km"), suffix=" km")}</span>
          </div>
          <div class="stat-block">
            <span class="stat-k">Most recent</span>
            <span class="stat-v stat-v-sm">{_fmt(fire.get("most_recent_fire_date"))}</span>
          </div>
        </div>
      </div>

      <div class="fire-verdict" style="border-color:{flag_color}; color:{flag_color};">
        <span class="fire-verdict-dot" style="background:{flag_color};"></span>
        {flag_label}
      </div>

      <h3 class="sub-h">Findings</h3>
      <p class="prose">{_esc(narrative.get("fire_analysis") or "No narrative available.")}</p>

      <p class="source-note">
        NASA FIRMS · VIIRS_SNPP_NRT (375m) cross-checked against MODIS_NRT (1km).
        Coverage window: trailing 90 days from audit timestamp.
      </p>

      {_page_footer(5)}
    </section>
    """


def _page_vegetation(payload: dict[str, Any]) -> str:
    veg = payload.get("vegetation_data") or {}
    narrative = payload.get("narrative") or {}
    approximate = bool(veg.get("approximate"))

    change = veg.get("vegetation_change_pct")
    if isinstance(change, (int, float)):
        arrow = "↑" if change >= 0 else "↓"
        change_color = "#4CAF7D" if change >= 0 else "#E85A4A"
        change_label = f"{change:+.1f}%"
    else:
        arrow = ""
        change_color = "rgba(245,240,232,0.4)"
        change_label = "—"

    return f"""
    <section class="page">
      {_section_heading("05 · Vegetation", "Vegetation Integrity Assessment")}
      {_approx_pill(approximate)}

      <div class="veg-callout">
        <div class="veg-change">
          <span class="veg-arrow" style="color:{change_color};">{arrow}</span>
          <span class="veg-pct" style="color:{change_color};">{_esc(change_label)}</span>
          <span class="veg-pct-sub">3-year delta</span>
        </div>
        <div class="veg-status">
          <span class="ck">Health status</span>
          <span class="cv">{_fmt(veg.get("health_status"))}</span>
        </div>
      </div>

      <div class="chart-wrap">{_ndvi_chart_svg(veg)}</div>

      <h3 class="sub-h">Interpretation</h3>
      <p class="prose">{_esc(narrative.get("vegetation_analysis") or "No narrative available.")}</p>

      <p class="source-note">
        ESA Sentinel-2 · NDVI = (NIR − RED) / (NIR + RED) · cloud-masked monthly
        composites · 10m native resolution
      </p>

      {_page_footer(6)}
    </section>
    """


def _page_forest(payload: dict[str, Any]) -> str:
    forest = payload.get("forest_data") or {}
    approximate = bool(forest.get("approximate"))
    chart_html = _forest_chart_svg(forest) or (
        '<div class="chart-empty">Annual loss series unavailable.</div>'
    )
    trend = (forest.get("trend") or "STABLE").upper()
    trend_color = {
        "STABLE":     "#4CAF7D",
        "DECLINING":  "#E8A44A",
        "CRITICAL":   "#E85A4A",
    }.get(trend, "#C9A84C")

    return f"""
    <section class="page">
      {_section_heading("06 · Forest", "Forest Cover & Deforestation")}
      {_approx_pill(approximate)}

      <div class="chart-wrap">{chart_html}</div>

      <div class="three-col">
        <div class="stat-block">
          <span class="stat-k">Total loss (5yr)</span>
          <span class="stat-v">{_fmt_num(forest.get("total_loss_5yr_ha"), suffix=" ha")}</span>
        </div>
        <div class="stat-block">
          <span class="stat-k">Annual loss rate</span>
          <span class="stat-v">{_fmt_num(forest.get("annual_loss_rate_pct"), suffix=" %")}</span>
        </div>
        <div class="stat-block">
          <span class="stat-k">Intact forest</span>
          <span class="stat-v">{_fmt_num(forest.get("intact_forest_pct"), suffix=" %")}</span>
        </div>
      </div>

      <div class="trend-pill" style="border-color:{trend_color}; color:{trend_color};">
        TREND · {_esc(trend)}
      </div>

      <p class="source-note">
        University of Maryland (Hansen et al.) tree-cover-loss via Global Forest
        Watch SQL API · 30m resolution · annual update cadence
      </p>

      {_page_footer(7)}
    </section>
    """


def _page_climate(payload: dict[str, Any]) -> str:
    climate = payload.get("climate_data") or {}
    narrative = payload.get("narrative") or {}
    approximate = bool(climate.get("approximate"))
    risk_level = (climate.get("climate_risk_level") or "—").upper()
    risk_col = _risk_color(risk_level)
    precip = climate.get("precipitation_anomaly_mm")

    return f"""
    <section class="page">
      {_section_heading("07 · Climate", "Climate Risk Assessment")}
      {_approx_pill(approximate)}

      <h3 class="sub-h">Temperature anomaly (vs 30-year baseline)</h3>
      <div class="chart-wrap chart-wrap-center">{_temp_anomaly_svg(climate)}</div>

      <div class="three-col">
        <div class="stat-block">
          <span class="stat-k">Precipitation anomaly</span>
          <span class="stat-v">{_fmt_num(precip, suffix=" mm")}</span>
        </div>
        <div class="stat-block">
          <span class="stat-k">Climate risk</span>
          <span class="stat-v" style="color:{risk_col};">{_esc(risk_level)}</span>
        </div>
        <div class="stat-block">
          <span class="stat-k">Nearest station</span>
          <span class="stat-v stat-v-sm">{_fmt(climate.get("nearest_station"))}</span>
        </div>
      </div>

      <h3 class="sub-h">Implications for additionality</h3>
      <p class="prose">{_esc(narrative.get("climate_context") or "No narrative available.")}</p>

      <p class="source-note">
        NOAA Climate Data Online (NCDC GHCND) · station-based daily aggregates
        compared against 30-year reference normal
      </p>

      {_page_footer(8)}
    </section>
    """


def _page_components(payload: dict[str, Any]) -> str:
    risk = payload.get("risk_score") or {}
    cs = risk.get("component_scores") or {}

    weights = {
        "fire_risk":              25,
        "vegetation_integrity":   20,
        "deforestation_risk":     25,
        "climate_stress":         15,
        "registry_compliance":    15,
    }
    bars = [
        _component_bar("Fire risk detection",   cs.get("fire_risk"),            weights["fire_risk"]),
        _component_bar("Vegetation integrity",  cs.get("vegetation_integrity"), weights["vegetation_integrity"]),
        _component_bar("Deforestation risk",    cs.get("deforestation_risk"),   weights["deforestation_risk"]),
        _component_bar("Climate stress",        cs.get("climate_stress"),       weights["climate_stress"]),
        _component_bar("Registry compliance",   cs.get("registry_compliance"),  weights["registry_compliance"]),
    ]

    return f"""
    <section class="page">
      {_section_heading("08 · Components", "Risk Score Components")}

      <p class="prose-muted">
        Five-pillar decomposition. Each component is normalized to a
        0–100 scale (higher = healthier) and then combined into the
        overall integrity score using the weights shown.
      </p>

      <div class="cmp-grid">
        <div class="cmp-bars">{"".join(bars)}</div>
        <div class="cmp-radar">{_radar_svg(cs)}</div>
      </div>

      <p class="source-note">
        Weights are tuned for AFOLU forestry projects. Fire and deforestation
        carry the highest weight because they capture observable, near-real-time
        permanence risk. Compliance is weighted lower because registry data is
        slower to update and self-reported.
      </p>

      {_page_footer(9)}
    </section>
    """


def _page_ai_reasoning(payload: dict[str, Any]) -> str:
    risk = payload.get("risk_score") or {}
    narrative = payload.get("narrative") or {}
    confidence = risk.get("confidence")
    conf_text = (
        f"{int(confidence * 100)}%" if isinstance(confidence, (int, float)) else "—"
    )
    flags = risk.get("key_flags") or []
    findings_html = "".join(
        f'<li>{_esc(f)}</li>' for f in flags
    ) or "<li>No driver-level flags surfaced.</li>"

    return f"""
    <section class="page">
      {_section_heading("09 · Reasoning", "AI Analysis & Reasoning Chain")}

      <table class="model-table">
        <tr>
          <td>Scoring engine</td>
          <td><code>llama-3.3-70b-versatile</code> (Groq, JSON mode)</td>
        </tr>
        <tr>
          <td>Narrative engine</td>
          <td><code>claude-opus-4-20250514</code> (Anthropic, prompt-cached)</td>
        </tr>
        <tr>
          <td>Confidence</td>
          <td>{_esc(conf_text)}</td>
        </tr>
      </table>

      <h3 class="sub-h">Key findings driving the score</h3>
      <ul class="reason-list">{findings_html}</ul>

      <h3 class="sub-h">Recommendation rationale</h3>
      <p class="prose">{_esc(narrative.get("recommendation_text") or "—")}</p>

      <h3 class="sub-h">Registry assessment</h3>
      <p class="prose">{_esc(narrative.get("registry_assessment") or "—")}</p>

      <h3 class="sub-h">Methodology note</h3>
      <p class="prose-muted">{_esc(narrative.get("methodology_note") or "—")}</p>

      {_page_footer(10)}
    </section>
    """


def _page_methodology(payload: dict[str, Any]) -> str:
    rows = [
        ("NASA FIRMS",           "NASA",                       "375m",       "Real-time",  "Global",         "API key"),
        ("Sentinel-2 imagery",   "ESA",                        "10m",        "5 days",     "Global",         "OAuth2 (CDSE)"),
        ("NDVI composites",      "ESA via Sentinel Hub",       "10m",        "Monthly",    "Global",         "OAuth2 (CDSE)"),
        ("Tree cover loss",      "UMD via Global Forest Watch","30m",        "Annual",     "Global",         "API key"),
        ("Climate normals",      "NOAA NCDC GHCND",            "Station",    "Daily",      "Global",         "Token"),
        ("Registry record",      "Verra · GS · ACR · CAR",     "Project",    "Real-time",  "Project-level",  "Public scrape"),
        ("Risk scoring",         "Groq (Llama 3.3 70B)",       "—",          "Per audit",  "—",              "API key"),
        ("Narrative",            "Anthropic (Claude Opus 4)",  "—",          "Per audit",  "—",              "API key"),
    ]
    rows_html = "".join(
        f'<tr><td>{_esc(r[0])}</td><td>{_esc(r[1])}</td><td>{_esc(r[2])}</td>'
        f'<td>{_esc(r[3])}</td><td>{_esc(r[4])}</td><td>{_esc(r[5])}</td></tr>'
        for r in rows
    )

    return f"""
    <section class="page">
      {_section_heading("10 · Methodology", "Audit Methodology & Data Sources")}

      <p class="prose-muted">
        Each VERITAS audit composes the eight signals below into a single
        integrity score. No single source can stand alone — fire alerts are
        cross-checked against vegetation deltas and forest-loss trend; the
        registry record is treated as one input, not the source of truth.
      </p>

      <table class="src-table">
        <thead>
          <tr>
            <th>Source</th><th>Provider</th><th>Resolution</th>
            <th>Update cadence</th><th>Coverage</th><th>Auth</th>
          </tr>
        </thead>
        <tbody>{rows_html}</tbody>
      </table>

      <h3 class="sub-h">Approximate-coordinate handling</h3>
      <p class="prose-muted">
        When the registry returns a project record without precise coordinates,
        VERITAS geocodes the country to its centroid (Nominatim / OpenStreetMap)
        and runs all geo-services against that point. Affected sections are
        tagged “⚠ Approximate · regional centroid” throughout this document;
        figures in those sections reflect regional, not project-specific, signal.
      </p>

      {_page_footer(11)}
    </section>
    """


def _page_recommendation(payload: dict[str, Any]) -> str:
    risk = payload.get("risk_score") or {}
    rec_raw = (risk.get("recommendation") or "—").upper()
    score = risk.get("overall_score")
    level = risk.get("risk_level") or "—"
    color = _risk_color(level)
    audit_date = datetime.now(timezone.utc).strftime("%d %B %Y")

    rec_color = {
        "ISSUE":         "#4CAF7D",
        "FULL_ISSUANCE": "#4CAF7D",
        "FULL ISSUANCE": "#4CAF7D",
        "FLAG":          "#E8A44A",
        "PARTIAL_FLAG":  "#E8A44A",
        "PARTIAL FLAG":  "#E8A44A",
        "HOLD":          "#E8A44A",
        "REVIEW":        "#E8A44A",
        "REJECT":        "#E85A4A",
    }.get(rec_raw, color)

    rec_pretty = {
        "ISSUE":         "FULL ISSUANCE",
        "FULL_ISSUANCE": "FULL ISSUANCE",
        "FLAG":          "PARTIAL FLAG",
        "PARTIAL_FLAG":  "PARTIAL FLAG",
        "HOLD":          "HOLD",
        "REVIEW":        "MANUAL REVIEW",
        "REJECT":        "REJECT",
    }.get(rec_raw, rec_raw)

    return f"""
    <section class="page page-final">
      <div class="final-tint" style="background:linear-gradient(180deg, {rec_color}1a 0%, transparent 60%);"></div>
      {_section_heading("11 · Verdict", "Final Recommendation")}

      <div class="final-rec" style="color:{rec_color}; border-color:{rec_color};">
        {_esc(rec_pretty)}
      </div>

      <div class="final-meta">
        <div>
          <span class="ck">Overall score</span>
          <span class="cv-lg" style="color:{_score_color(score)};">{_fmt_num(score)}/100</span>
        </div>
        <div>
          <span class="ck">Risk level</span>
          <span class="cv-lg" style="color:{color};">{_esc(level).upper()}</span>
        </div>
        <div>
          <span class="ck">Date</span>
          <span class="cv-lg">{_esc(audit_date)}</span>
        </div>
      </div>

      <div class="disclaimer">
        <span class="disc-label">DISCLAIMER</span>
        <p>
          This report is generated by automated AI analysis combining
          satellite, registry, and climate signals. It should be reviewed by
          qualified environmental auditors before any issuance, retirement, or
          enforcement decision. VERITAS Oracle accepts no liability for
          decisions made solely on the basis of this report.
        </p>
      </div>

      <footer class="final-foot">
        <span class="brand-mark-lg">VERITAS<span class="wordmark-sub">ORACLE</span></span>
        <span class="brand-tag">Carbon credit integrity, scored.</span>
      </footer>
    </section>
    """


# ── master CSS ──────────────────────────────────────────────────────────

_CSS = """
@page { size: A4; margin: 0; }

* { box-sizing: border-box; }

html, body {
  margin: 0;
  padding: 0;
  background: #080808;
  color: #F5F0E8;
  font-family: 'IBM Plex Sans', 'DejaVu Sans', 'Helvetica Neue', Arial, sans-serif;
  font-size: 10.5pt;
  line-height: 1.5;
  -webkit-print-color-adjust: exact;
  print-color-adjust: exact;
}

h1, h2, h3 {
  font-family: 'Playfair Display', Georgia, 'Times New Roman', serif;
  margin: 0;
  font-weight: 500;
  color: #F5F0E8;
}

code, pre, .mono {
  font-family: 'IBM Plex Mono', 'DejaVu Sans Mono', 'Courier New', monospace;
}

.page {
  width: 210mm;
  height: 297mm;
  padding: 22mm 20mm 20mm;
  background: #080808;
  position: relative;
  page-break-after: always;
  overflow: hidden;
}
.page:last-child { page-break-after: auto; }

/* ── page header / footer ─────────────────────────────────────── */
.page-head {
  border-bottom: 1px solid rgba(201, 168, 76, 0.25);
  padding-bottom: 8mm;
  margin-bottom: 8mm;
}
.eyebrow {
  font-family: 'IBM Plex Mono', monospace;
  font-size: 8pt;
  letter-spacing: 0.3em;
  color: #C9A84C;
  text-transform: uppercase;
  display: block;
  margin-bottom: 4pt;
}
.page-title {
  font-size: 26pt;
  line-height: 1.05;
  letter-spacing: -0.4pt;
  color: #F5F0E8;
}
.page-foot {
  position: absolute;
  bottom: 12mm;
  left: 20mm;
  right: 20mm;
  display: flex;
  justify-content: space-between;
  font-family: 'IBM Plex Mono', monospace;
  font-size: 8pt;
  letter-spacing: 0.2em;
  color: rgba(245, 240, 232, 0.4);
  border-top: 1px solid rgba(201, 168, 76, 0.15);
  padding-top: 4mm;
}
.brand-mark { color: #C9A84C; }

.sub-h {
  font-family: 'IBM Plex Mono', monospace;
  font-size: 9pt;
  letter-spacing: 0.25em;
  text-transform: uppercase;
  color: #C9A84C;
  margin: 14pt 0 6pt;
  font-weight: 500;
}

.prose { font-size: 10.5pt; line-height: 1.6; margin: 0 0 8pt; color: #F5F0E8; }
.prose-muted { font-size: 10pt; line-height: 1.55; color: rgba(245, 240, 232, 0.65); margin: 0 0 8pt; }

.source-note {
  position: absolute;
  bottom: 24mm;
  left: 20mm;
  right: 20mm;
  font-family: 'IBM Plex Mono', monospace;
  font-size: 8.5pt;
  color: rgba(245, 240, 232, 0.5);
  border-left: 2px solid #C9A84C;
  padding: 4pt 10pt;
  background: rgba(201, 168, 76, 0.04);
}

.approx-pill {
  display: inline-block;
  font-family: 'IBM Plex Mono', monospace;
  font-size: 8pt;
  letter-spacing: 0.2em;
  color: #E8A44A;
  background: rgba(232, 164, 74, 0.08);
  border: 1px solid rgba(232, 164, 74, 0.4);
  padding: 3pt 8pt;
  border-radius: 2pt;
  text-transform: uppercase;
  margin-bottom: 8pt;
}

/* ── COVER ─────────────────────────────────────────────────────── */
.page-cover {
  padding: 18mm 18mm;
  display: flex;
  flex-direction: column;
}
.cover-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  border-bottom: 1px solid rgba(201, 168, 76, 0.25);
  padding-bottom: 10pt;
}
.wordmark {
  font-family: 'Playfair Display', Georgia, serif;
  font-size: 30pt;
  color: #C9A84C;
  letter-spacing: 1pt;
  font-weight: 600;
}
.wordmark-sub {
  font-family: 'IBM Plex Mono', monospace;
  font-size: 11pt;
  letter-spacing: 0.4em;
  margin-left: 8pt;
  color: rgba(245, 240, 232, 0.6);
  vertical-align: middle;
  font-weight: 400;
}
.confidential {
  font-family: 'IBM Plex Mono', monospace;
  font-size: 8.5pt;
  letter-spacing: 0.3em;
  color: rgba(245, 240, 232, 0.5);
  text-align: right;
  max-width: 70mm;
  line-height: 1.4;
}
.cover-ring { margin: 10mm auto 0; text-align: center; }
.cover-meta { text-align: center; margin-top: 6mm; }
.risk-pill {
  display: inline-block;
  font-family: 'IBM Plex Mono', monospace;
  font-size: 11pt;
  letter-spacing: 0.3em;
  font-weight: 700;
  padding: 6pt 18pt;
  border-radius: 3pt;
}
.cover-project {
  font-size: 28pt;
  margin: 14pt 0 12pt;
  letter-spacing: -0.4pt;
}
.cover-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 14pt 24pt;
  margin: 0 auto;
  max-width: 160mm;
  text-align: left;
  font-family: 'IBM Plex Mono', monospace;
}
.cover-grid > div { display: flex; flex-direction: column; gap: 2pt; }
.ck {
  font-size: 8pt;
  letter-spacing: 0.22em;
  color: rgba(245, 240, 232, 0.5);
  text-transform: uppercase;
}
.cv {
  font-size: 11pt;
  color: #F5F0E8;
}
.cover-foot {
  position: absolute;
  bottom: 16mm;
  left: 18mm;
  right: 18mm;
  display: flex;
  justify-content: space-between;
  font-family: 'IBM Plex Mono', monospace;
  font-size: 8.5pt;
  letter-spacing: 0.25em;
  color: #C9A84C;
  border-top: 1px solid rgba(201, 168, 76, 0.25);
  padding-top: 8pt;
}

/* ── EXECUTIVE SUMMARY ─────────────────────────────────────────── */
.es-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12pt;
  flex-wrap: wrap;
  gap: 10pt;
}
.risk-pill-lg {
  font-family: 'IBM Plex Mono', monospace;
  font-size: 11pt;
  letter-spacing: 0.3em;
  font-weight: 700;
  padding: 7pt 18pt;
  border-radius: 2pt;
}
.es-conf { display: flex; flex-direction: column; align-items: flex-end; }
.rec-box {
  border: 2pt solid #C9A84C;
  border-radius: 3pt;
  padding: 14pt 18pt;
  margin: 6pt 0 12pt;
  background: rgba(201, 168, 76, 0.04);
}
.rec-label {
  display: block;
  font-family: 'IBM Plex Mono', monospace;
  font-size: 8pt;
  letter-spacing: 0.3em;
  color: rgba(245, 240, 232, 0.5);
  text-transform: uppercase;
}
.rec-value {
  display: block;
  font-family: 'Playfair Display', Georgia, serif;
  font-size: 22pt;
  letter-spacing: -0.2pt;
  margin: 3pt 0 5pt;
  font-weight: 600;
}
.rec-text {
  margin: 0;
  font-size: 10pt;
  line-height: 1.55;
  color: rgba(245, 240, 232, 0.85);
}
.two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 18pt; margin-top: 6pt; }
.flag-list, .pos-list, .reason-list {
  list-style: none;
  margin: 0;
  padding: 0;
  font-family: 'IBM Plex Mono', monospace;
  font-size: 9.5pt;
}
.flag-item, .pos-item {
  display: flex;
  align-items: flex-start;
  gap: 6pt;
  padding: 4pt 8pt;
  margin-bottom: 3pt;
}
.flag-item { background: rgba(232, 164, 74, 0.08); border-left: 2pt solid #E8A44A; }
.pos-item  { background: rgba(76, 175, 125, 0.08); border-left: 2pt solid #4CAF7D; }
.flag-icon { color: #E8A44A; font-weight: 700; }
.pos-icon  { color: #4CAF7D; font-weight: 700; }
.flag-empty {
  color: rgba(245, 240, 232, 0.4);
  font-style: italic;
  padding: 4pt 0;
  font-size: 9pt;
}
.meta-table, .kv-table, .src-table, .model-table {
  width: 100%;
  border-collapse: collapse;
  font-family: 'IBM Plex Mono', monospace;
  font-size: 9.5pt;
  margin-top: 6pt;
}
.meta-table td, .model-table td {
  padding: 5pt 0;
  border-bottom: 1px solid rgba(245, 240, 232, 0.06);
}
.meta-table td:first-child, .model-table td:first-child {
  color: rgba(245, 240, 232, 0.5);
  width: 35%;
}

/* ── KEY-VALUE ROWS ────────────────────────────────────────────── */
.kv-table td {
  padding: 6pt 0;
  border-bottom: 1px solid rgba(245, 240, 232, 0.06);
  font-size: 10pt;
}
.kv-k { color: rgba(245, 240, 232, 0.5); width: 40%; }
.kv-v { color: #F5F0E8; }

.link-code {
  display: block;
  font-family: 'IBM Plex Mono', monospace;
  font-size: 9pt;
  color: #C9A84C;
  background: rgba(201, 168, 76, 0.06);
  padding: 8pt 10pt;
  border-left: 2pt solid #C9A84C;
  word-break: break-all;
}

/* ── SATELLITE GRID ────────────────────────────────────────────── */
.sat-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 6mm;
  margin-top: 4mm;
}
.sat-tile {
  border: 1px solid rgba(201, 168, 76, 0.25);
  border-radius: 2pt;
  overflow: hidden;
  background: #0a0a0a;
}
.sat-tile img { width: 100%; display: block; }
.sat-empty {
  height: 70mm;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6pt;
  color: rgba(245, 240, 232, 0.4);
}
.sat-empty-eyebrow {
  font-family: 'IBM Plex Mono', monospace;
  font-size: 8pt;
  letter-spacing: 0.25em;
  color: rgba(245, 240, 232, 0.4);
}
.sat-empty code {
  color: #C9A84C;
  font-size: 10pt;
  letter-spacing: 0.05em;
}
.sat-caption {
  padding: 6pt 10pt;
  font-family: 'IBM Plex Mono', monospace;
  font-size: 9pt;
  color: rgba(245, 240, 232, 0.7);
  background: rgba(0, 0, 0, 0.5);
}

/* ── FIRE PAGE ─────────────────────────────────────────────────── */
.fire-row {
  display: grid;
  grid-template-columns: 110mm 1fr;
  gap: 6mm;
  margin: 4mm 0;
}
.fire-map {
  width: 100%;
  height: 65mm;
  object-fit: cover;
  border: 1px solid rgba(201, 168, 76, 0.25);
  border-radius: 2pt;
  background: #0a0a0a;
}
.fire-map-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  font-family: 'IBM Plex Mono', monospace;
  font-size: 9pt;
  color: rgba(245, 240, 232, 0.4);
}
.fire-stats {
  display: grid;
  grid-template-columns: 1fr 1fr;
  grid-template-rows: 1fr 1fr;
  gap: 4mm;
}
.stat-block {
  background: rgba(201, 168, 76, 0.04);
  border: 1px solid rgba(201, 168, 76, 0.15);
  padding: 7pt 10pt;
  border-radius: 2pt;
  display: flex;
  flex-direction: column;
  justify-content: center;
}
.stat-k {
  font-family: 'IBM Plex Mono', monospace;
  font-size: 8pt;
  letter-spacing: 0.2em;
  color: rgba(245, 240, 232, 0.5);
  text-transform: uppercase;
}
.stat-v {
  font-family: 'Playfair Display', Georgia, serif;
  font-size: 22pt;
  color: #F5F0E8;
  margin-top: 3pt;
  line-height: 1;
  font-weight: 500;
}
.stat-v-sm { font-size: 13pt; line-height: 1.2; }
.fire-verdict {
  display: flex;
  align-items: center;
  gap: 8pt;
  font-family: 'IBM Plex Mono', monospace;
  font-size: 11pt;
  letter-spacing: 0.25em;
  font-weight: 700;
  border: 2pt solid;
  border-radius: 2pt;
  padding: 8pt 14pt;
  margin: 4mm 0;
}
.fire-verdict-dot {
  width: 10pt;
  height: 10pt;
  border-radius: 50%;
}

/* ── VEGETATION PAGE ───────────────────────────────────────────── */
.veg-callout {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: rgba(201, 168, 76, 0.04);
  border-left: 2pt solid #C9A84C;
  padding: 10pt 16pt;
  margin: 4mm 0;
}
.veg-change { display: flex; align-items: baseline; gap: 6pt; }
.veg-arrow { font-size: 30pt; line-height: 1; font-family: 'Playfair Display', Georgia, serif; }
.veg-pct { font-size: 30pt; font-family: 'Playfair Display', Georgia, serif; font-weight: 600; line-height: 1; }
.veg-pct-sub {
  font-family: 'IBM Plex Mono', monospace;
  font-size: 8.5pt;
  letter-spacing: 0.2em;
  color: rgba(245, 240, 232, 0.5);
  margin-left: 8pt;
  text-transform: uppercase;
}
.veg-status { display: flex; flex-direction: column; align-items: flex-end; gap: 3pt; }

.chart-wrap { margin: 4mm 0; text-align: center; }
.chart-wrap-center { display: flex; justify-content: center; }
.chart-empty {
  text-align: center;
  font-family: 'IBM Plex Mono', monospace;
  font-size: 9pt;
  color: rgba(245, 240, 232, 0.4);
  padding: 10mm 0;
  background: rgba(245, 240, 232, 0.02);
  border-radius: 2pt;
}

/* ── FOREST PAGE ───────────────────────────────────────────────── */
.three-col {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr;
  gap: 4mm;
  margin: 4mm 0;
}
.trend-pill {
  display: inline-block;
  font-family: 'IBM Plex Mono', monospace;
  font-size: 10pt;
  letter-spacing: 0.3em;
  font-weight: 600;
  border: 2pt solid;
  border-radius: 2pt;
  padding: 6pt 14pt;
  margin-top: 4mm;
}

/* ── COMPONENTS PAGE ───────────────────────────────────────────── */
.cmp-grid {
  display: grid;
  grid-template-columns: 1fr 360px;
  gap: 8mm;
  align-items: center;
  margin-top: 4mm;
}
.cmp-bars { display: flex; flex-direction: column; gap: 6pt; }
.cmp-head {
  display: flex;
  justify-content: space-between;
  font-family: 'IBM Plex Mono', monospace;
  font-size: 9.5pt;
  margin-bottom: 3pt;
}
.cmp-label { color: rgba(245, 240, 232, 0.85); }
.cmp-weight {
  color: rgba(245, 240, 232, 0.4);
  margin-left: 8pt;
  font-size: 8pt;
  letter-spacing: 0.15em;
}
.cmp-value { color: #F5F0E8; }
.cmp-of { color: rgba(245, 240, 232, 0.4); margin-left: 3pt; font-size: 8pt; }
.cmp-track { height: 5pt; background: rgba(245, 240, 232, 0.06); border-radius: 1pt; }
.cmp-fill { height: 100%; border-radius: 1pt; }

/* ── REASONING / METHODOLOGY ───────────────────────────────────── */
.reason-list li {
  background: rgba(232, 164, 74, 0.05);
  border-left: 2pt solid #E8A44A;
  padding: 4pt 10pt;
  margin-bottom: 3pt;
  color: rgba(245, 240, 232, 0.9);
}
.src-table { font-size: 9pt; }
.src-table th {
  font-family: 'IBM Plex Mono', monospace;
  font-size: 8pt;
  letter-spacing: 0.2em;
  text-transform: uppercase;
  color: #C9A84C;
  text-align: left;
  padding: 6pt 8pt 6pt 0;
  border-bottom: 1px solid rgba(201, 168, 76, 0.3);
}
.src-table td {
  padding: 6pt 8pt 6pt 0;
  border-bottom: 1px solid rgba(245, 240, 232, 0.06);
  color: rgba(245, 240, 232, 0.85);
}

/* ── FINAL PAGE ────────────────────────────────────────────────── */
.page-final { display: flex; flex-direction: column; }
.final-tint { position: absolute; inset: 0; pointer-events: none; }
.final-rec {
  font-family: 'Playfair Display', Georgia, serif;
  font-size: 60pt;
  text-align: center;
  letter-spacing: -1pt;
  font-weight: 700;
  margin: 14mm 0 6mm;
  padding: 14pt 0;
  border-top: 2pt solid;
  border-bottom: 2pt solid;
  position: relative;
  z-index: 1;
}
.final-meta {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 14pt;
  text-align: center;
  margin: 8mm 0;
  position: relative;
  z-index: 1;
}
.final-meta > div { display: flex; flex-direction: column; gap: 5pt; }
.cv-lg {
  font-family: 'Playfair Display', Georgia, serif;
  font-size: 22pt;
  font-weight: 600;
}
.disclaimer {
  margin: 8mm 0;
  background: rgba(245, 240, 232, 0.04);
  border-left: 2pt solid rgba(245, 240, 232, 0.4);
  padding: 10pt 14pt;
  position: relative;
  z-index: 1;
}
.disc-label {
  display: block;
  font-family: 'IBM Plex Mono', monospace;
  font-size: 8pt;
  letter-spacing: 0.3em;
  color: rgba(245, 240, 232, 0.5);
  margin-bottom: 4pt;
  text-transform: uppercase;
}
.disclaimer p {
  margin: 0;
  font-size: 9.5pt;
  color: rgba(245, 240, 232, 0.75);
  line-height: 1.55;
}
.final-foot {
  margin-top: auto;
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  border-top: 1px solid rgba(201, 168, 76, 0.25);
  padding-top: 8pt;
  position: relative;
  z-index: 1;
}
.brand-mark-lg {
  font-family: 'Playfair Display', Georgia, serif;
  font-size: 22pt;
  color: #C9A84C;
  font-weight: 600;
  letter-spacing: 0.5pt;
}
.brand-tag {
  font-family: 'IBM Plex Mono', monospace;
  font-size: 9pt;
  letter-spacing: 0.2em;
  color: rgba(245, 240, 232, 0.5);
}
"""


# ── master HTML assembly ────────────────────────────────────────────────

def _build_html(payload: dict[str, Any]) -> str:
    """Assemble the full 12-page audit report as one HTML document.

    Each page is a `<section class="page">` with a forced page-break-after,
    so the HTML preview renders the document scrollable while the PDF
    paginates correctly. All charts are inline SVG; the only external
    assets are the Mapbox tile (page 5) and the Sentinel-2 satellite
    image (page 4, embedded as a base64 data URI when present).
    """
    project = payload.get("project") or {}
    serial_label = project.get("serial") or payload.get("serial") or "audit"

    pages = (
        _page_cover(payload)
        + _page_executive_summary(payload)
        + _page_project_identity(payload)
        + _page_satellite(payload)
        + _page_fire(payload)
        + _page_vegetation(payload)
        + _page_forest(payload)
        + _page_climate(payload)
        + _page_components(payload)
        + _page_ai_reasoning(payload)
        + _page_methodology(payload)
        + _page_recommendation(payload)
    )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <title>VERITAS audit · {_esc(serial_label)}</title>
  <link href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@500;600;700&family=IBM+Plex+Sans:wght@300;400;500&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet"/>
  <style>{_CSS}</style>
</head>
<body>
{pages}
</body>
</html>"""


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


# ── Interactive HTML preview (slide viewer) ─────────────────────────────
#
# The PDF path stays bit-for-bit identical (render_pdf_bytes → _build_html).
# This is a separate browser-only viewer: same 12 page builders, but wrapped
# in a fullscreen slide deck with vanilla-JS navigation + animations + an
# image lightbox. Glassmorphism (backdrop-filter blur) is layered on top of
# the existing CSS — works in browsers, ignored by WeasyPrint.

_PREVIEW_CSS = """
/* ── viewer chrome — only applies in preview mode ───────────────── */
body.preview {
  margin: 0;
  width: 100vw;
  height: 100vh;
  overflow: hidden;
  background: #080808;
  background-image:
    radial-gradient(ellipse 60% 40% at 50% -10%, rgba(201,168,76,0.10), transparent 65%),
    radial-gradient(ellipse 70% 40% at 100% 110%, rgba(76,175,125,0.06), transparent 60%);
  background-attachment: fixed;
}
body.preview::before {
  content: '';
  position: fixed; inset: 0;
  pointer-events: none;
  z-index: 0;
  background-image:
    linear-gradient(rgba(245,240,232,0.018) 1px, transparent 1px),
    linear-gradient(90deg, rgba(245,240,232,0.018) 1px, transparent 1px);
  background-size: 64px 64px;
  -webkit-mask-image: radial-gradient(ellipse 80% 70% at 50% 30%, #000, transparent 80%);
  mask-image: radial-gradient(ellipse 80% 70% at 50% 30%, #000, transparent 80%);
}
body.preview .slides-container {
  position: relative;
  width: 100%;
  height: 100%;
  z-index: 1;
}

/* Reskin .page → fullscreen slide */
body.preview .page {
  position: absolute !important;
  inset: 0 !important;
  width: 100% !important;
  height: 100% !important;
  padding: 7vh max(40px, calc(50% - 460px)) 14vh !important;
  page-break-after: auto !important;
  overflow-y: auto;
  overflow-x: hidden;
  opacity: 0;
  transform: translateY(20px);
  transition: opacity 400ms ease, transform 400ms ease;
  pointer-events: none;
  background: transparent !important;
}
body.preview .page.active {
  opacity: 1;
  transform: translateY(0);
  pointer-events: auto;
}
body.preview .page-foot,
body.preview .source-note { position: static !important; margin-top: 14pt; }

/* Keep cover full-bleed dark with the score ring centred */
body.preview .page-cover {
  display: flex !important;
  flex-direction: column;
  justify-content: space-between;
}
body.preview .cover-foot { position: static !important; margin-top: auto !important; padding-top: 12pt; }

/* Glassmorphism overlay on existing panels */
body.preview .stat-block,
body.preview .rec-box,
body.preview .veg-callout,
body.preview .sat-tile,
body.preview .approx-pill,
body.preview .fire-verdict,
body.preview .trend-pill,
body.preview .risk-pill,
body.preview .risk-pill-lg,
body.preview .meta-table,
body.preview .src-table,
body.preview .kv-table {
  border-radius: 14px !important;
}
body.preview .stat-block,
body.preview .rec-box,
body.preview .veg-callout,
body.preview .sat-tile {
  background: rgba(255,255,255,0.025) !important;
  backdrop-filter: blur(20px) saturate(140%);
  -webkit-backdrop-filter: blur(20px) saturate(140%);
  border: 1px solid rgba(201,168,76,0.18) !important;
  box-shadow: 0 8px 32px -16px rgba(0,0,0,0.6);
}
body.preview .fire-map {
  border-radius: 14px !important;
  border: 1px solid rgba(201,168,76,0.18) !important;
  box-shadow: 0 8px 32px -16px rgba(0,0,0,0.6);
}

/* ── nav bar ────────────────────────────────────────────────────── */
.vt-nav {
  position: fixed;
  bottom: 32px;
  left: 50%;
  transform: translateX(-50%);
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 14px;
  background: rgba(255,255,255,0.025);
  border: 1px solid rgba(201,168,76,0.18);
  border-radius: 999px;
  backdrop-filter: blur(20px) saturate(140%);
  -webkit-backdrop-filter: blur(20px) saturate(140%);
  z-index: 100;
  box-shadow: 0 12px 32px -12px rgba(0,0,0,0.7);
}
.vt-nav button {
  width: 38px; height: 38px;
  border-radius: 50%;
  border: 1px solid rgba(201,168,76,0.32);
  background: transparent;
  color: #C9A84C;
  cursor: pointer;
  font-size: 18px;
  font-family: 'IBM Plex Mono', monospace;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  transition: background 200ms ease, color 200ms ease, border-color 200ms ease, transform 200ms ease;
  -webkit-appearance: none;
  appearance: none;
  padding: 0;
}
.vt-nav button:hover:not(:disabled) {
  background: rgba(201,168,76,0.12);
  border-color: #C9A84C;
  color: #E8C96A;
}
.vt-nav button:active:not(:disabled) { transform: scale(0.94); }
.vt-nav button:disabled { opacity: 0.28; cursor: not-allowed; }
.vt-counter {
  font-family: 'IBM Plex Mono', monospace;
  font-size: 12px;
  color: rgba(245,240,232,0.82);
  letter-spacing: 0.2em;
  min-width: 86px;
  text-align: center;
  font-variant-numeric: tabular-nums;
}

/* ── progress rail ──────────────────────────────────────────────── */
.vt-progress-rail {
  position: fixed;
  bottom: 0; left: 0; right: 0;
  height: 2px;
  background: rgba(245,240,232,0.06);
  z-index: 99;
}
.vt-progress-fill {
  height: 100%;
  background: linear-gradient(90deg, #C9A84C 0%, #E8C96A 100%);
  width: 8.3333%;
  transition: width 420ms cubic-bezier(0.4, 0, 0.2, 1);
}

/* ── lightbox ──────────────────────────────────────────────────── */
.vt-lightbox {
  position: fixed;
  inset: 0;
  background: rgba(0,0,0,0.95);
  display: none;
  align-items: center;
  justify-content: center;
  z-index: 200;
  -webkit-tap-highlight-color: transparent;
}
.vt-lightbox.open { display: flex; }
.vt-lightbox img {
  max-width: 92vw;
  max-height: 86vh;
  transition: transform 220ms ease;
  box-shadow: 0 40px 80px -20px rgba(0,0,0,0.85);
  border-radius: 6px;
  user-select: none;
}
.vt-lb-controls {
  position: absolute;
  bottom: 28px;
  left: 50%;
  transform: translateX(-50%);
  display: flex;
  gap: 10px;
  background: rgba(255,255,255,0.04);
  border: 1px solid rgba(201,168,76,0.2);
  border-radius: 999px;
  padding: 8px 12px;
  backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px);
}
.vt-lb-btn {
  width: 38px; height: 38px;
  border-radius: 50%;
  border: 1px solid rgba(201,168,76,0.32);
  background: transparent;
  color: #C9A84C;
  cursor: pointer;
  font-size: 18px;
  font-family: 'IBM Plex Mono', monospace;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  transition: background 200ms ease, color 200ms ease;
}
.vt-lb-btn:hover { background: rgba(201,168,76,0.14); color: #E8C96A; }
.vt-lb-close {
  position: absolute;
  top: 24px;
  right: 24px;
  width: 44px; height: 44px;
  border-radius: 50%;
  border: 1px solid rgba(245,240,232,0.2);
  background: rgba(255,255,255,0.04);
  backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px);
  color: #f5f0e8;
  cursor: pointer;
  font-size: 24px;
  line-height: 1;
}
.vt-lb-close:hover { background: rgba(232,90,74,0.18); border-color: rgba(232,90,74,0.5); }
body.preview .sat-tile img,
body.preview img.fire-map {
  cursor: zoom-in;
  transition: transform 250ms ease, filter 250ms ease;
}
body.preview .sat-tile img:hover,
body.preview img.fire-map:hover {
  transform: scale(1.012);
  filter: brightness(1.08);
}

/* ── slide-enter animations ─────────────────────────────────────── */
/* Bar charts (NDVI, forest) — bars grow from baseline */
body.preview .page .chart-wrap svg rect {
  transform: scaleY(0);
  transform-origin: bottom;
  transform-box: fill-box;
  transition: transform 750ms cubic-bezier(0.4, 0, 0.2, 1);
}
body.preview .page.active .chart-wrap svg rect { transform: scaleY(1); }
body.preview .page.active .chart-wrap svg rect:nth-of-type(2) { transition-delay: 80ms; }
body.preview .page.active .chart-wrap svg rect:nth-of-type(3) { transition-delay: 160ms; }
body.preview .page.active .chart-wrap svg rect:nth-of-type(4) { transition-delay: 240ms; }
body.preview .page.active .chart-wrap svg rect:nth-of-type(5) { transition-delay: 320ms; }

/* Component bars (slide 9) — fill grows horizontally */
body.preview .page .cmp-fill {
  transform: scaleX(0);
  transform-origin: left;
  transition: transform 900ms 200ms cubic-bezier(0.4, 0, 0.2, 1);
}
body.preview .page.active .cmp-fill { transform: scaleX(1); }

/* Radar polygon (slide 9) — interpolates from centre */
body.preview .page .cmp-radar svg polygon[stroke="#C9A84C"] {
  transform: scale(0);
  transform-origin: center;
  transform-box: fill-box;
  transition: transform 800ms 250ms cubic-bezier(0.4, 0, 0.2, 1);
}
body.preview .page.active .cmp-radar svg polygon[stroke="#C9A84C"] { transform: scale(1); }
body.preview .page .cmp-radar svg circle { opacity: 0; transition: opacity 600ms 700ms ease; }
body.preview .page.active .cmp-radar svg circle { opacity: 1; }

/* Stagger key flags / positives on slide 2 */
body.preview .page .flag-list .flag-item,
body.preview .page .pos-list .pos-item,
body.preview .page .reason-list li {
  opacity: 0;
  transform: translateX(-8px);
  transition: opacity 500ms ease, transform 500ms ease;
}
body.preview .page.active .flag-list .flag-item,
body.preview .page.active .pos-list .pos-item,
body.preview .page.active .reason-list li {
  opacity: 1;
  transform: translateX(0);
}
body.preview .page.active .flag-list .flag-item:nth-child(1),
body.preview .page.active .pos-list .pos-item:nth-child(1),
body.preview .page.active .reason-list li:nth-child(1) { transition-delay: 120ms; }
body.preview .page.active .flag-list .flag-item:nth-child(2),
body.preview .page.active .pos-list .pos-item:nth-child(2),
body.preview .page.active .reason-list li:nth-child(2) { transition-delay: 220ms; }
body.preview .page.active .flag-list .flag-item:nth-child(3),
body.preview .page.active .pos-list .pos-item:nth-child(3),
body.preview .page.active .reason-list li:nth-child(3) { transition-delay: 320ms; }
body.preview .page.active .flag-list .flag-item:nth-child(4),
body.preview .page.active .pos-list .pos-item:nth-child(4),
body.preview .page.active .reason-list li:nth-child(4) { transition-delay: 420ms; }

/* Trend pill pulses once on enter */
body.preview .page.active .trend-pill {
  animation: vt-pulse-once 1.4s 200ms ease-out;
}
@keyframes vt-pulse-once {
  0%   { transform: scale(1); }
  30%  { transform: scale(1.06); }
  100% { transform: scale(1); }
}

/* Final recommendation slide — entrance */
body.preview .page-final .final-rec {
  opacity: 0;
  transform: scale(0.94);
  transition: opacity 700ms 200ms ease, transform 700ms 200ms cubic-bezier(0.34, 1.56, 0.64, 1);
}
body.preview .page-final.active .final-rec {
  opacity: 1;
  transform: scale(1);
}

/* Sat-tile placeholder scan-line shimmer */
@keyframes vt-scan {
  0%   { background-position: 0 0; }
  100% { background-position: 0 200px; }
}
body.preview .sat-tile-empty .sat-empty {
  background-image: linear-gradient(180deg, transparent 0%, rgba(201,168,76,0.04) 50%, transparent 100%);
  background-size: 100% 200px;
  background-repeat: repeat;
  animation: vt-scan 4s linear infinite;
}

/* Cover ring count-up gets a tiny breathing pulse after settle */
body.preview .page-cover .cover-ring svg {
  filter: drop-shadow(0 0 24px rgba(201,168,76,0.18));
}

/* ── responsive (mobile) ────────────────────────────────────────── */
@media (max-width: 768px) {
  body.preview .page {
    padding: 5vh 5vw 12vh !important;
  }
  body.preview .page-title { font-size: 19pt !important; }
  body.preview .cover-project { font-size: 22pt !important; }
  body.preview .stat-v { font-size: 18pt !important; }
  body.preview .final-rec { font-size: 38pt !important; }
  body.preview .two-col,
  body.preview .three-col,
  body.preview .cmp-grid,
  body.preview .fire-row,
  body.preview .sat-grid,
  body.preview .cover-grid { grid-template-columns: 1fr !important; }
  body.preview .cmp-radar { display: none; }
  .vt-nav {
    bottom: 14px;
    width: calc(100% - 24px);
    justify-content: space-between;
    border-radius: 12px;
    padding: 6px 10px;
  }
  .vt-counter { min-width: auto; font-size: 11px; }
}

@media (prefers-reduced-motion: reduce) {
  body.preview .page,
  body.preview .page * {
    transition-duration: 0.01ms !important;
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
  }
}

/* ════════════════════════════════════════════════════════════════════
   BRAND PARITY — worldmonitor / veritasoracle.vercel.app landing
   ────────────────────────────────────────────────────────────────────
   Lifted directly from public/landing.html so the preview reads as the
   same product. The PDF path (render_pdf_bytes / _CSS) keeps its
   print-friendly Playfair Display + IBM Plex stack untouched; these
   overrides only cascade onto body.preview elements in the browser
   viewer. Specificity escalations use !important because they have to
   beat the inline-styled rules emitted by the page builders.
   ════════════════════════════════════════════════════════════════════ */

body.preview {
  /* Brand tokens scoped to the preview surface. */
  --vt-bg:           #060a07;
  --vt-bg-elev:      #0a100c;
  --vt-ink:          #f5f0eb;
  --vt-ink-soft:     rgba(245, 240, 235, 0.65);
  --vt-ink-mute:     rgba(245, 240, 235, 0.42);
  --vt-ink-faint:    rgba(245, 240, 235, 0.22);
  --vt-gold:         #c8860a;
  --vt-gold-soft:    #d4943a;
  --vt-gold-bright:  #f5b541;
  --vt-gold-glow:    rgba(200, 134, 10, 0.18);
  --vt-emerald:      #4ade80;
  --vt-crimson:      #f87171;
  --vt-line:         rgba(245, 240, 235, 0.10);
  --vt-line-soft:    rgba(245, 240, 235, 0.06);
  --vt-glass-bg:     rgba(255, 255, 255, 0.025);
  --vt-glass-bg-strong: rgba(255, 255, 255, 0.022);
  --serif:           'Instrument Serif', 'Times New Roman', serif;
  --sans:            'Barlow', system-ui, -apple-system, 'Segoe UI', sans-serif;
  --mono:            'JetBrains Mono', 'SFMono-Regular', Menlo, monospace;
  --ease-out:        cubic-bezier(0.2, 0.7, 0.2, 1);
  --ease-spring:     cubic-bezier(0.34, 1.56, 0.64, 1);

  background-color: var(--vt-bg) !important;
  color: var(--vt-ink) !important;
  font-family: var(--sans) !important;
  font-weight: 300;
  letter-spacing: 0.005em;
}

/* Aurora + grid backdrop straight from landing.html */
body.preview {
  background-image:
    radial-gradient(ellipse 60% 38% at 50% -8%, rgba(200, 134, 10, 0.20), transparent 65%),
    radial-gradient(ellipse 70% 40% at 100% 100%, rgba(74, 222, 128, 0.08), transparent 60%),
    radial-gradient(ellipse 60% 40% at 0% 100%, rgba(200, 134, 10, 0.06), transparent 60%) !important;
  background-attachment: fixed !important;
}
body.preview::before {
  background-image:
    linear-gradient(rgba(245, 240, 235, 0.020) 1px, transparent 1px),
    linear-gradient(90deg, rgba(245, 240, 235, 0.020) 1px, transparent 1px) !important;
  background-size: 64px 64px !important;
  -webkit-mask-image: radial-gradient(ellipse 80% 70% at 50% 30%, #000, transparent 80%) !important;
          mask-image: radial-gradient(ellipse 80% 70% at 50% 30%, #000, transparent 80%) !important;
  animation: vt-aurora 22s ease-in-out infinite alternate;
}
@keyframes vt-aurora {
  0%   { transform: translate3d(-1.5%, -0.8%, 0) scale(1.015); }
  100% { transform: translate3d(1.5%, 0.8%, 0) scale(1); }
}

/* ── Type ──────────────────────────────────────────────────────── */
/* Display: Instrument Serif italic for hero-class headings */
body.preview h1,
body.preview h2,
body.preview .cover-project,
body.preview .page-title,
body.preview .rec-value,
body.preview .final-rec,
body.preview .veg-pct,
body.preview .veg-arrow,
body.preview .stat-v,
body.preview .cv-lg,
body.preview .wordmark {
  font-family: var(--serif) !important;
  font-style: italic !important;
  font-weight: 400 !important;
  letter-spacing: -0.01em !important;
  color: var(--vt-ink);
}
/* h3 sub-headings stay non-italic Instrument Serif */
body.preview h3:not(.sub-h):not(.cmp-label) {
  font-family: var(--serif) !important;
  font-weight: 400 !important;
  font-style: normal !important;
}

/* Mono: every label, code, eyebrow, table cell, nav, badge */
body.preview code, body.preview .mono,
body.preview .eyebrow,
body.preview .ck, body.preview .cv,
body.preview .sub-h,
body.preview .stat-k,
body.preview .approx-pill,
body.preview .source-note,
body.preview .meta-table, body.preview .meta-table td,
body.preview .kv-table,  body.preview .kv-table td,
body.preview .src-table, body.preview .src-table td, body.preview .src-table th,
body.preview .model-table, body.preview .model-table td,
body.preview .reason-list, body.preview .reason-list li,
body.preview .flag-list, body.preview .flag-item,
body.preview .pos-list, body.preview .pos-item,
body.preview .vt-counter,
body.preview .vt-nav button,
body.preview .vt-lb-btn,
body.preview .vt-lb-close,
body.preview .cmp-head, body.preview .cmp-label, body.preview .cmp-value,
body.preview .cmp-of,   body.preview .cmp-weight,
body.preview .risk-pill, body.preview .risk-pill-lg,
body.preview .trend-pill,
body.preview .fire-verdict,
body.preview .confidential, body.preview .cover-foot, body.preview .page-foot,
body.preview .veg-pct-sub, body.preview .veg-status,
body.preview .brand-tag,   body.preview .disc-label,
body.preview .link-code,
body.preview .rec-label,
body.preview .rec-text,
body.preview .prose-muted,
body.preview .wordmark-sub,
body.preview .stat-v-sm {
  font-family: var(--mono) !important;
  font-feature-settings: 'tnum' on, 'lnum' on;
}

/* Body prose stays Barlow (sans), readability-tuned */
body.preview .prose,
body.preview .disclaimer p,
body.preview .veg-callout {
  font-family: var(--sans) !important;
  font-weight: 300;
  line-height: 1.55;
  color: var(--vt-ink-soft);
}

/* Wordmark dot — gold */
body.preview .wordmark { color: var(--vt-ink) !important; }
body.preview .wordmark-sub {
  color: var(--vt-ink-soft) !important;
  letter-spacing: 0.4em !important;
}

/* Eyebrows / labels / source notes — gold mono-tag */
body.preview .eyebrow,
body.preview .sub-h,
body.preview .ck,
body.preview .stat-k,
body.preview .panel-eyebrow,
body.preview .brand-mark,
body.preview .rec-label,
body.preview .disc-label {
  color: var(--vt-gold) !important;
  text-transform: uppercase;
  letter-spacing: 0.22em !important;
}

/* ── Glass primitive — exact rim highlight from landing.html ──── */
body.preview .stat-block,
body.preview .rec-box,
body.preview .veg-callout,
body.preview .sat-tile,
body.preview .vt-nav,
body.preview .vt-lb-controls,
body.preview .vt-lb-close,
body.preview img.fire-map,
body.preview .fire-map-empty {
  background: var(--vt-glass-bg) !important;
  border: none !important;
  border-radius: 16px !important;
  box-shadow:
    inset 0 1px 1px rgba(255, 255, 255, 0.08),
    0 4px 30px rgba(0, 0, 0, 0.25) !important;
  position: relative;
  overflow: hidden;
}
body.preview .stat-block::before,
body.preview .rec-box::before,
body.preview .veg-callout::before,
body.preview .sat-tile::before,
body.preview .vt-nav::before,
body.preview .vt-lb-controls::before {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: inherit;
  padding: 1.2px;
  background: linear-gradient(180deg,
    rgba(255, 255, 255, 0.40) 0%,
    rgba(255, 255, 255, 0.10) 22%,
    rgba(255, 255, 255, 0)    45%,
    rgba(255, 255, 255, 0)    55%,
    rgba(255, 255, 255, 0.10) 78%,
    rgba(255, 255, 255, 0.40) 100%);
  -webkit-mask: linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0);
  -webkit-mask-composite: xor;
          mask-composite: exclude;
  pointer-events: none;
  z-index: 0;
}
body.preview .stat-block > *,
body.preview .rec-box > *,
body.preview .veg-callout > *,
body.preview .sat-tile > * { position: relative; z-index: 1; }

/* Pill-rounded borders for round elements */
body.preview .vt-nav { border-radius: 999px !important; }
body.preview .vt-lb-controls { border-radius: 999px !important; }
body.preview .approx-pill,
body.preview .risk-pill,
body.preview .risk-pill-lg,
body.preview .trend-pill,
body.preview .fire-verdict {
  border-radius: 999px !important;
}

/* Stronger glass for cover ring + final recommendation backdrop */
body.preview .cover-ring svg {
  filter: drop-shadow(0 0 32px rgba(200, 134, 10, 0.22));
}

/* ── Buttons / nav — landing-style pill ──────────────────────── */
body.preview .vt-nav button {
  border: 1px solid rgba(200, 134, 10, 0.32) !important;
  color: var(--vt-gold) !important;
  background: transparent !important;
  font-size: 14px !important;
}
body.preview .vt-nav button:hover:not(:disabled) {
  background: rgba(200, 134, 10, 0.14) !important;
  color: var(--vt-gold-bright) !important;
  border-color: var(--vt-gold) !important;
  box-shadow: 0 4px 16px rgba(200, 134, 10, 0.28);
}
body.preview .vt-counter { color: var(--vt-ink-soft) !important; letter-spacing: 0.22em !important; }

body.preview .vt-lb-btn {
  border-color: rgba(200, 134, 10, 0.32) !important;
  color: var(--vt-gold) !important;
  background: rgba(255, 255, 255, 0.04) !important;
}
body.preview .vt-lb-btn:hover {
  background: rgba(200, 134, 10, 0.18) !important;
  color: var(--vt-gold-bright) !important;
}

/* ── Recommendation — gold gradient text on final slide ──────── */
body.preview .final-rec {
  background: linear-gradient(180deg, var(--vt-gold-bright) 0%, var(--vt-gold) 90%) !important;
  -webkit-background-clip: text !important;
          background-clip: text !important;
  -webkit-text-fill-color: transparent !important;
  border-color: rgba(200, 134, 10, 0.30) !important;
}

/* Cover risk pill — gold treatment, rounded */
body.preview .risk-pill,
body.preview .risk-pill-lg {
  background: var(--vt-ink) !important;
  color: #0a0501 !important;
  font-weight: 500 !important;
  letter-spacing: 0.22em !important;
}

/* Trend pill / fire verdict — gold-on-dark instead of solid colour fill */
body.preview .trend-pill {
  background: rgba(200, 134, 10, 0.06) !important;
  color: var(--vt-gold-bright) !important;
  border: 1px solid rgba(200, 134, 10, 0.30) !important;
}

body.preview .fire-verdict {
  background: var(--vt-glass-bg) !important;
  border: 1px solid rgba(200, 134, 10, 0.22) !important;
  backdrop-filter: blur(14px) saturate(140%) !important;
  -webkit-backdrop-filter: blur(14px) saturate(140%) !important;
}

/* Approximate-data tag — soft gold instead of amber */
body.preview .approx-pill {
  background: rgba(200, 134, 10, 0.08) !important;
  color: var(--vt-gold-bright) !important;
  border: 1px solid rgba(200, 134, 10, 0.40) !important;
  letter-spacing: 0.22em !important;
}

/* ── Source notes — gold left rim ─────────────────────────────── */
body.preview .source-note {
  border-left: 2px solid var(--vt-gold) !important;
  background: rgba(200, 134, 10, 0.04) !important;
  color: var(--vt-ink-mute) !important;
  border-radius: 0 6px 6px 0 !important;
}

/* ── Bars / charts — emerald positives, crimson negatives ─────── */
body.preview .cmp-fill {
  /* keep the inline-style colour, just round the corners */
  border-radius: 999px !important;
}

/* Score-bar/cmp track — softer rail */
body.preview .cmp-track {
  background: rgba(245, 240, 235, 0.06) !important;
  border-radius: 999px !important;
}

/* Progress rail — landing-style faded gold gradient */
body.preview .vt-progress-fill {
  background: linear-gradient(90deg, transparent, var(--vt-gold) 30%, var(--vt-gold-bright) 70%, transparent) !important;
}

/* ── KV / data tables — refined separator + mono ──────────────── */
body.preview .kv-table td,
body.preview .meta-table td,
body.preview .src-table td {
  border-bottom: 1px solid var(--vt-line-soft) !important;
  font-size: 9.5pt;
}
body.preview .kv-k,
body.preview .meta-table td:first-child {
  color: var(--vt-ink-mute) !important;
}
body.preview .src-table th { color: var(--vt-gold) !important; }

/* ── Flag/pos lists — left-rim accent with brand colors ───────── */
body.preview .flag-item {
  background: rgba(212, 148, 58, 0.08) !important;
  border-left: 2px solid var(--vt-gold-soft) !important;
}
body.preview .pos-item {
  background: rgba(74, 222, 128, 0.08) !important;
  border-left: 2px solid var(--vt-emerald) !important;
}
body.preview .flag-icon { color: var(--vt-gold-soft) !important; }
body.preview .pos-icon  { color: var(--vt-emerald) !important; }

/* ── Cover page polish — wordmark + meta grid ─────────────────── */
body.preview .page-cover .wordmark {
  font-size: 38pt !important;
  background: linear-gradient(180deg, var(--vt-gold-bright) 0%, var(--vt-gold) 90%) !important;
  -webkit-background-clip: text !important;
          background-clip: text !important;
  -webkit-text-fill-color: transparent !important;
  font-style: italic !important;
}
body.preview .page-cover .confidential { color: var(--vt-ink-mute) !important; }
body.preview .page-cover .cover-foot {
  color: var(--vt-gold) !important;
  border-top-color: rgba(200, 134, 10, 0.25) !important;
}

/* ── Page heads — soft gold underline + larger eyebrow ────────── */
body.preview .page-head {
  border-bottom: 1px solid rgba(200, 134, 10, 0.20) !important;
}

/* ── Disclaimer panel ──────────────────────────────────────────── */
body.preview .disclaimer {
  background: rgba(255, 255, 255, 0.018) !important;
  border-left: 2px solid var(--vt-gold) !important;
  border-radius: 0 14px 14px 0 !important;
  backdrop-filter: blur(14px) saturate(140%);
  -webkit-backdrop-filter: blur(14px) saturate(140%);
}
body.preview .disclaimer p { color: var(--vt-ink-soft) !important; }

/* Brand mark on final slide */
body.preview .brand-mark-lg {
  font-family: var(--serif) !important;
  font-style: italic !important;
  background: linear-gradient(180deg, var(--vt-gold-bright) 0%, var(--vt-gold) 90%) !important;
  -webkit-background-clip: text !important;
          background-clip: text !important;
  -webkit-text-fill-color: transparent !important;
}
"""


_PREVIEW_JS = r"""
(function () {
  'use strict';
  var slides = Array.prototype.slice.call(
    document.querySelectorAll('body.preview .page')
  );
  var total = slides.length;
  if (total === 0) return;

  var counterEl = document.getElementById('vt-counter');
  var progressEl = document.getElementById('vt-progress-fill');
  var prevBtn = document.getElementById('vt-prev');
  var nextBtn = document.getElementById('vt-next');
  var lb = document.getElementById('vt-lightbox');
  var lbImg = document.getElementById('vt-lb-img');
  var lbClose = document.getElementById('vt-lb-close');
  var lbIn = document.getElementById('vt-lb-in');
  var lbOut = document.getElementById('vt-lb-out');

  var idx = 0;
  var lbZoom = 1;

  function pad(n) { return n < 10 ? '0' + n : '' + n; }

  function show(newIdx) {
    if (newIdx < 0 || newIdx >= total) return;
    if (slides[idx]) slides[idx].classList.remove('active');
    idx = newIdx;
    slides[idx].classList.add('active');
    counterEl.textContent = pad(idx + 1) + ' / ' + pad(total);
    progressEl.style.width = (((idx + 1) / total) * 100).toFixed(2) + '%';
    prevBtn.disabled = idx === 0;
    nextBtn.disabled = idx === total - 1;
    triggerEnter(slides[idx]);
  }

  function animateNumber(el, from, to, duration, formatter) {
    var start = performance.now();
    function step(now) {
      var t = Math.min(1, (now - start) / duration);
      var eased = 1 - Math.pow(1 - t, 3);
      var v = from + (to - from) * eased;
      el.textContent = formatter(v);
      if (t < 1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
  }

  function triggerEnter(slide) {
    var slideNum = parseInt(slide.getAttribute('data-slide'), 10);

    // Slide 1: animate the big score number inside the cover ring
    if (slideNum === 1) {
      var ringText = slide.querySelector('.cover-ring svg text:first-of-type');
      if (ringText && !ringText.dataset.vtCounted) {
        var target = parseInt(ringText.textContent, 10);
        if (isFinite(target)) {
          ringText.dataset.vtCounted = '1';
          ringText.textContent = '0';
          animateNumber(ringText, 0, target, 1500, function (v) {
            return Math.round(v).toString();
          });
        }
      }
    }

    // Slide 5: fire hotspot count
    if (slideNum === 5) {
      var statBlocks = slide.querySelectorAll('.fire-stats .stat-block');
      if (statBlocks.length) {
        var hotEl = statBlocks[0].querySelector('.stat-v');
        if (hotEl && !hotEl.dataset.vtCounted) {
          var hot = parseInt(hotEl.textContent, 10);
          if (isFinite(hot)) {
            hotEl.dataset.vtCounted = '1';
            hotEl.textContent = '0';
            animateNumber(hotEl, 0, hot, 1100, function (v) {
              return Math.round(v).toString();
            });
          }
        }
      }
    }

    // Slide 6: vegetation change %
    if (slideNum === 6) {
      var vegPct = slide.querySelector('.veg-pct');
      if (vegPct && !vegPct.dataset.vtCounted) {
        var raw = vegPct.textContent.replace('%', '').trim();
        var target2 = parseFloat(raw);
        if (isFinite(target2)) {
          vegPct.dataset.vtCounted = '1';
          var sign = target2 >= 0 ? '+' : '';
          vegPct.textContent = sign + '0.0%';
          animateNumber(vegPct, 0, target2, 1200, function (v) {
            return (v >= 0 ? '+' : '') + v.toFixed(1) + '%';
          });
        }
      }
    }
  }

  // ── nav handlers ──────────────────────────────────────────────
  prevBtn.addEventListener('click', function () { show(idx - 1); });
  nextBtn.addEventListener('click', function () { show(idx + 1); });

  document.addEventListener('keydown', function (e) {
    if (lb.classList.contains('open')) {
      if (e.key === 'Escape') closeLb();
      return;
    }
    if (e.key === 'ArrowLeft' || e.key === 'PageUp') {
      e.preventDefault(); show(idx - 1);
    } else if (e.key === 'ArrowRight' || e.key === 'PageDown' || e.key === ' ') {
      e.preventDefault(); show(idx + 1);
    } else if (e.key === 'Home') {
      e.preventDefault(); show(0);
    } else if (e.key === 'End') {
      e.preventDefault(); show(total - 1);
    }
  });

  // ── lightbox ──────────────────────────────────────────────────
  function openLb(src) {
    lbImg.src = src;
    lbZoom = 1;
    lbImg.style.transform = 'scale(1)';
    lb.classList.add('open');
  }
  function closeLb() {
    lb.classList.remove('open');
    lbImg.src = '';
  }
  function setZoom(z) {
    lbZoom = Math.max(0.5, Math.min(4, z));
    lbImg.style.transform = 'scale(' + lbZoom + ')';
  }
  lbClose.addEventListener('click', closeLb);
  lb.addEventListener('click', function (e) { if (e.target === lb) closeLb(); });
  lbIn.addEventListener('click', function () { setZoom(lbZoom + 0.25); });
  lbOut.addEventListener('click', function () { setZoom(lbZoom - 0.25); });

  // Wire up zoomable images: every <img> inside .sat-tile, plus img.fire-map
  Array.prototype.forEach.call(
    document.querySelectorAll('body.preview .sat-tile img, body.preview img.fire-map'),
    function (img) {
      if (!img.getAttribute('src')) return;
      img.addEventListener('click', function () { openLb(img.src); });
    }
  );

  // Boot — show the cover slide
  show(0);
})();
"""


def _build_preview_html(payload: dict[str, Any]) -> str:
    """Build the interactive single-slide-at-a-time browser viewer.

    Reuses the 12 page builders verbatim — same data, same layout — but
    wraps them in a slide deck with vanilla-JS navigation and animations.
    The PDF renderer (render_pdf_bytes / _build_html) is not affected.
    """
    import re

    project = payload.get("project") or {}
    serial_label = project.get("serial") or payload.get("serial") or "audit"

    pages_raw = (
        _page_cover(payload)
        + _page_executive_summary(payload)
        + _page_project_identity(payload)
        + _page_satellite(payload)
        + _page_fire(payload)
        + _page_vegetation(payload)
        + _page_forest(payload)
        + _page_climate(payload)
        + _page_components(payload)
        + _page_ai_reasoning(payload)
        + _page_methodology(payload)
        + _page_recommendation(payload)
    )

    # Inject data-slide="N" on every <section class="page..."> so the JS
    # can identify which slide is which when triggering enter animations.
    counter = [0]
    def _mark(match: "re.Match[str]") -> str:
        counter[0] += 1
        return match.group(0)[:-1] + f' data-slide="{counter[0]}">'
    slides_html = re.sub(r'<section class="page[^"]*">', _mark, pages_raw)

    # Brand fonts come from the worldmonitor landing page —
    # Instrument Serif (italic for h1/h2) + Barlow body + JetBrains Mono
    # data. The PDF path keeps its own print-friendly stack via _CSS;
    # this preview-only override layer pulls in the brand fonts so the
    # browser viewer matches veritasoracle.vercel.app exactly.
    head = (
        "<!doctype html>"
        "<html lang=\"en\"><head>"
        "<meta charset=\"utf-8\"/>"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"/>"
        f"<title>VERITAS audit · {_esc(serial_label)}</title>"
        "<link rel=\"preconnect\" href=\"https://fonts.googleapis.com\"/>"
        "<link rel=\"preconnect\" href=\"https://fonts.gstatic.com\" crossorigin/>"
        "<link href=\"https://fonts.googleapis.com/css2?"
        "family=Instrument+Serif:ital@0;1&"
        "family=Barlow:wght@200;300;400;500;600;700&"
        "family=JetBrains+Mono:wght@300;400;500&display=swap\" rel=\"stylesheet\"/>"
        f"<style>{_CSS}{_PREVIEW_CSS}</style>"
        "</head>"
    )

    nav = (
        '<nav class="vt-nav" aria-label="Slide navigation">'
        '<button id="vt-prev" type="button" aria-label="Previous slide">←</button>'
        '<span class="vt-counter" id="vt-counter" aria-live="polite">01 / 12</span>'
        '<button id="vt-next" type="button" aria-label="Next slide">→</button>'
        '</nav>'
        '<div class="vt-progress-rail" aria-hidden="true">'
        '<div class="vt-progress-fill" id="vt-progress-fill"></div>'
        '</div>'
    )

    lightbox = (
        '<div class="vt-lightbox" id="vt-lightbox" role="dialog" aria-label="Image lightbox">'
        '<button class="vt-lb-close" id="vt-lb-close" type="button" aria-label="Close lightbox">×</button>'
        '<img id="vt-lb-img" alt=""/>'
        '<div class="vt-lb-controls">'
        '<button class="vt-lb-btn" id="vt-lb-out" type="button" aria-label="Zoom out">−</button>'
        '<button class="vt-lb-btn" id="vt-lb-in" type="button" aria-label="Zoom in">+</button>'
        '</div>'
        '</div>'
    )

    body = (
        '<body class="preview">'
        f'<main class="slides-container">{slides_html}</main>'
        f'{nav}{lightbox}'
        f'<script>{_PREVIEW_JS}</script>'
        '</body></html>'
    )

    return head + body
