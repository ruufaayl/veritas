/**
 * ReportView — standalone viewer for a completed audit.
 *
 * Route: /report/:auditId. Fetches GET /audit/:auditId, renders the
 * same six data cards AuditFlow shows (project, fire, NDVI, forest,
 * risk score, narrative), but without the running terminal — this is
 * the "share a finished audit" surface.
 *
 * Glassmorphism + gold aesthetic to match Dashboard and the redesigned
 * PDF preview. Bottom action bar: Download PDF · Share · New Audit.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { motion } from "framer-motion";
import {
  Area,
  AreaChart,
  Bar,
  CartesianGrid,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import ScoreRing from "../components/audit/ScoreRing.jsx";
import { getAuditById, reportPdfUrl } from "../lib/api.js";

const GOLD = "#C9A84C";
const GOLD_BRIGHT = "#E8C96A";
const GREEN = "#4CAF7D";
const AMBER = "#E8A44A";
const RED = "#E85A4A";

const RISK_COLOR = {
  LOW: GREEN,
  MEDIUM: AMBER,
  HIGH: RED,
  CRITICAL: "#C42B2B",
};

const RECOMMENDATION_BADGE = {
  ISSUE: { label: "FULL ISSUANCE", color: GREEN },
  FULL_ISSUANCE: { label: "FULL ISSUANCE", color: GREEN },
  FLAG: { label: "PARTIAL FLAG", color: AMBER },
  PARTIAL_FLAG: { label: "PARTIAL FLAG", color: AMBER },
  HOLD: { label: "HOLD", color: AMBER },
  REVIEW: { label: "MANUAL REVIEW", color: AMBER },
  REJECT: { label: "REJECT", color: RED },
};

function pickBadge(rec) {
  if (!rec) return { label: "—", color: "rgba(245,240,232,0.5)" };
  const key = String(rec).toUpperCase().replace(/\s+/g, "_");
  return RECOMMENDATION_BADGE[key] || { label: String(rec).toUpperCase(), color: GOLD_BRIGHT };
}

function mapboxImage(lat, lon, approximate) {
  const token = import.meta.env.VITE_MAPBOX_TOKEN;
  if (!token || lat == null || lon == null) return null;
  const zoom = approximate ? 5 : 8;
  return `https://api.mapbox.com/styles/v1/mapbox/satellite-v9/static/${lon},${lat},${zoom},0/700x420@2x?access_token=${token}`;
}

const COUNTRY_FLAGS = {
  Brazil: "🇧🇷", Kenya: "🇰🇪", Indonesia: "🇮🇩", India: "🇮🇳", Cambodia: "🇰🇭",
  Peru: "🇵🇪", Colombia: "🇨🇴", Mexico: "🇲🇽", Tanzania: "🇹🇿", Uganda: "🇺🇬",
  USA: "🇺🇸", "United States": "🇺🇸", Vietnam: "🇻🇳", Thailand: "🇹🇭",
};

function flagFor(country) {
  return country ? (COUNTRY_FLAGS[country] || "🌍") : "🌍";
}

export default function ReportView() {
  const { auditId } = useParams();
  const [audit, setAudit] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [shareLabel, setShareLabel] = useState("Share");
  const shareTimerRef = useRef(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    getAuditById(auditId)
      .then((data) => {
        if (cancelled) return;
        if (data?.status === "running") {
          setError("Audit is still running. Watch progress on /audit.");
        } else {
          setAudit(data);
        }
      })
      .catch((err) => {
        if (cancelled) return;
        const detail = err?.response?.data?.detail || err?.message || "Audit not found";
        setError(detail);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
      if (shareTimerRef.current) clearTimeout(shareTimerRef.current);
    };
  }, [auditId]);

  const project = audit?.project || {};
  const risk = audit?.risk_score || {};
  const fire = audit?.fire_data || {};
  const veg = audit?.vegetation_data || {};
  const forest = audit?.forest_data || {};
  const narrative = audit?.narrative || {};

  const lat = project.lat;
  const lon = project.lon;
  const approximate = !!project.coordinates_approximate;
  const fireMapUrl = useMemo(
    () => mapboxImage(lat, lon, approximate || !!fire.approximate),
    [lat, lon, approximate, fire.approximate]
  );

  const ndviSeries = useMemo(() => {
    const { ndvi_3yr_ago, ndvi_1yr_ago, current_ndvi } = veg;
    if (ndvi_3yr_ago == null && ndvi_1yr_ago == null && current_ndvi == null) return null;
    return [
      { period: "3 Years Ago", ndvi: ndvi_3yr_ago ?? null },
      { period: "1 Year Ago", ndvi: ndvi_1yr_ago ?? null },
      { period: "Current", ndvi: current_ndvi ?? null },
    ];
  }, [veg]);

  const ndviStroke = useMemo(() => {
    const s = String(veg.health_status || "").toUpperCase();
    if (s === "HEALTHY" || s === "GOOD") return GREEN;
    if (s === "DEGRADED" || s === "MODERATE") return AMBER;
    if (s === "CRITICAL" || s === "POOR") return RED;
    return GREEN;
  }, [veg.health_status]);

  const forestSeries = useMemo(() => {
    const raw = forest.annual_loss_ha;
    if (!Array.isArray(raw) || raw.length === 0) return null;
    return raw.map((row, i) => {
      if (typeof row === "number") return { year: `Y-${raw.length - i}`, loss_ha: row };
      return {
        year: row.year ?? `Y-${raw.length - i}`,
        loss_ha: row.loss_ha ?? row.ha ?? row.value ?? 0,
      };
    });
  }, [forest]);

  const componentBars = useMemo(() => {
    const cs = risk.component_scores || {};
    return [
      { key: "fire_risk", label: "Fire risk", value: cs.fire_risk },
      { key: "vegetation_integrity", label: "Vegetation integrity", value: cs.vegetation_integrity },
      { key: "deforestation_risk", label: "Deforestation risk", value: cs.deforestation_risk },
      { key: "climate_stress", label: "Climate stress", value: cs.climate_stress },
      { key: "registry_compliance", label: "Registry compliance", value: cs.registry_compliance },
    ];
  }, [risk]);

  const handleShare = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(window.location.href);
      setShareLabel("Copied ✓");
    } catch {
      setShareLabel("Copy failed");
    }
    if (shareTimerRef.current) clearTimeout(shareTimerRef.current);
    shareTimerRef.current = setTimeout(() => setShareLabel("Share"), 2200);
  }, []);

  const handleDownload = useCallback(() => {
    if (!auditId) return;
    window.open(reportPdfUrl(auditId), "_blank", "noopener,noreferrer");
  }, [auditId]);

  const recBadge = pickBadge(risk.recommendation);

  return (
    <main className="vr-page">
      <ReportStyles />

      <header className="vr-head">
        <Link to="/" className="vr-wordmark">
          VERITAS<span className="vr-wordmark-sub">ORACLE</span>
        </Link>

        {audit && (
          <div className="vr-meta-bar">
            <MetaCell label="Serial" value={project.serial} />
            <MetaCell label="Registry" value={project.registry} />
            <MetaCell label="Country" value={`${flagFor(project.country)} ${project.country || "—"}`} />
            <MetaCell
              label="Score"
              value={
                <span style={{ color: RISK_COLOR[risk.risk_level] || GOLD_BRIGHT }}>
                  {risk.overall_score ?? "—"}
                  <span className="vr-meta-of">/100</span>
                </span>
              }
            />
            <MetaCell
              label="Date"
              value={
                audit?.completed_at || audit?.started_at
                  ? new Date(audit.completed_at || audit.started_at).toLocaleDateString()
                  : "—"
              }
            />
          </div>
        )}
      </header>

      {loading && (
        <div className="vr-skeletons">
          <SkeletonCard />
          <SkeletonCard />
          <SkeletonCard />
        </div>
      )}

      {!loading && error && (
        <div className="vr-error">
          <h2>Audit unavailable</h2>
          <p>{error}</p>
          <Link to="/audit" className="vr-cta">RUN A NEW AUDIT</Link>
        </div>
      )}

      {!loading && !error && audit && (
        <>
          <div className="vr-cards">
            <Card title="Project Identity">
              <ProjectTable project={project} />
            </Card>

            <Card title="Fire Detection — NASA FIRMS" approximate={!!fire.approximate}>
              <FireBlock fire={fire} mapUrl={fireMapUrl} lat={lat} lon={lon} />
            </Card>

            <Card title="Vegetation Index — Sentinel-2 NDVI" approximate={!!veg.approximate}>
              <NdviBlock veg={veg} series={ndviSeries} stroke={ndviStroke} />
            </Card>

            <Card title="Forest Cover — Global Forest Watch" approximate={!!forest.approximate}>
              <ForestBlock forest={forest} series={forestSeries} />
            </Card>

            <Card title="Risk Score — Llama 3.3 70B">
              <RiskBlock
                score={risk.overall_score ?? 0}
                level={risk.risk_level}
                recBadge={recBadge}
                components={componentBars}
                confidence={risk.confidence}
                keyFlags={risk.key_flags}
              />
            </Card>

            <Card title="Audit Narrative — Claude Opus">
              <NarrativeBlock narrative={narrative} />
            </Card>
          </div>

          <div className="vr-actions">
            <button type="button" className="vr-btn vr-btn-primary" onClick={handleDownload}>
              ↓ DOWNLOAD PDF
            </button>
            <button type="button" className="vr-btn" onClick={handleShare}>
              {shareLabel.toUpperCase()}
            </button>
            <Link to="/audit" className="vr-btn">+ NEW AUDIT</Link>
          </div>
        </>
      )}
    </main>
  );
}

// ────────────────────────────────────────────────────────────────────
// Sub-blocks
// ────────────────────────────────────────────────────────────────────

function MetaCell({ label, value }) {
  return (
    <div className="vr-meta-cell">
      <span className="vr-meta-k">{label}</span>
      <span className="vr-meta-v">{value || "—"}</span>
    </div>
  );
}

function Card({ title, children, approximate = false }) {
  return (
    <section className="vr-card">
      <header className="vr-card-head">
        <h3>{title}</h3>
        {approximate && (
          <span
            className="vr-approx"
            title="Coordinates not provided by the registry; geo-services were run against the country centroid."
          >
            ⚠ APPROXIMATE
          </span>
        )}
      </header>
      <div className="vr-card-body">{children}</div>
    </section>
  );
}

function SkeletonCard() {
  return (
    <div className="vr-card vr-skel">
      <div className="vr-skel-line" />
      <div className="vr-skel-line short" />
      <div className="vr-skel-line" />
      <div className="vr-skel-line short" />
    </div>
  );
}

function ProjectTable({ project }) {
  const rows = [
    ["Serial", project.serial],
    ["Project name", project.project_name],
    ["Registry", project.registry],
    ["Country", `${flagFor(project.country)} ${project.country || "—"}`],
    [
      "Coordinates",
      typeof project.lat === "number" && typeof project.lon === "number"
        ? `${project.lat.toFixed(4)}, ${project.lon.toFixed(4)}${project.coordinates_approximate ? " (centroid)" : ""}`
        : "—",
    ],
    ["Methodology", project.methodology],
    ["Credits issued", project.credits_issued],
    ["Last verification", project.last_verification_date],
  ];
  return (
    <table className="vr-kv-table">
      <tbody>
        {rows.map(([k, v]) => (
          <tr key={k}>
            <td>{k}</td>
            <td>{v ?? "—"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function FireBlock({ fire, mapUrl, lat, lon }) {
  const flagged =
    fire.fire_risk_flag === true ||
    (fire.fire_risk_flag === undefined && (fire.high_confidence_count ?? 0) > 0);
  return (
    <div>
      <div className="vr-map-frame">
        {mapUrl ? (
          <img src={mapUrl} alt="Project satellite tile" />
        ) : (
          <div className="vr-map-placeholder">
            <span className="vr-map-eyebrow">MAPBOX TILE UNAVAILABLE</span>
            <code>
              {typeof lat === "number" && typeof lon === "number"
                ? `${lat.toFixed(4)}, ${lon.toFixed(4)}`
                : "no coordinates"}
            </code>
          </div>
        )}
        <div className="vr-map-badge">
          {fire.hotspot_count ?? 0} HOTSPOTS · {fire.high_confidence_count ?? 0} HIGH-CONF
        </div>
        {flagged && <div className="vr-fire-flag">FIRE RISK</div>}
      </div>
      <div className="vr-stat-grid">
        <Stat label="Most recent" value={fire.most_recent_fire_date} />
        <Stat label="Nearest fire"
              value={fire.nearest_fire_km != null ? `${fire.nearest_fire_km} km` : "—"} />
        <Stat label="Source" value={fire.source || "VIIRS_SNPP_NRT"} />
        <Stat label="Days scanned" value={fire.days_scanned ?? 90} />
      </div>
    </div>
  );
}

function Stat({ label, value }) {
  return (
    <div>
      <span className="vr-stat-k">{label}</span>
      <span className="vr-stat-v">{value ?? "—"}</span>
    </div>
  );
}

const tooltipStyle = {
  background: "rgba(8,8,8,0.92)",
  border: `1px solid rgba(201,168,76,0.3)`,
  borderRadius: 6,
  padding: "6px 10px",
  fontFamily: "var(--font-mono)",
  fontSize: 11,
  color: "var(--color-text-primary)",
};

function NdviBlock({ veg, series, stroke }) {
  const change = veg.vegetation_change_pct;
  const arrow = typeof change === "number" ? (change >= 0 ? "↑" : "↓") : "";
  const changeColor =
    typeof change === "number" ? (change >= 0 ? GREEN : RED) : "rgba(245,240,232,0.4)";
  return (
    <div>
      <div className="vr-veg-callout">
        <span className="vr-veg-num" style={{ color: changeColor }}>
          {arrow} {typeof change === "number" ? `${change.toFixed(1)}%` : "—"}
        </span>
        <span className="vr-veg-status">
          NDVI {(veg.health_status || "unknown").toLowerCase()}
        </span>
      </div>
      {series ? (
        <ResponsiveContainer width="100%" height={170}>
          <AreaChart data={series}>
            <defs>
              <linearGradient id="vr-ndvi" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={stroke} stopOpacity={0.45} />
                <stop offset="100%" stopColor={stroke} stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="2 4" stroke="rgba(245,240,232,0.06)" />
            <XAxis dataKey="period" stroke="rgba(245,240,232,0.4)" fontSize={10} tickLine={false} axisLine={false} />
            <YAxis stroke="rgba(245,240,232,0.4)" fontSize={10} tickLine={false} axisLine={false} domain={[0, 1]} />
            <Tooltip contentStyle={tooltipStyle} cursor={{ stroke: "rgba(201,168,76,0.3)" }} />
            <Area type="monotone" dataKey="ndvi" stroke={stroke} strokeWidth={2} fill="url(#vr-ndvi)" />
          </AreaChart>
        </ResponsiveContainer>
      ) : (
        <p className="vr-empty">NDVI series unavailable.</p>
      )}
    </div>
  );
}

function ForestBlock({ forest, series }) {
  return (
    <div>
      <div className="vr-veg-callout">
        <span className="vr-veg-num">
          {forest.total_loss_5yr_ha ?? "—"}
          <span className="vr-veg-num-sub"> ha lost (5yr)</span>
        </span>
        {forest.trend && (
          <span className="vr-trend-pill">{forest.trend}</span>
        )}
      </div>
      {series ? (
        <ResponsiveContainer width="100%" height={170}>
          <ComposedChart data={series}>
            <CartesianGrid strokeDasharray="2 4" stroke="rgba(245,240,232,0.06)" />
            <XAxis dataKey="year" stroke="rgba(245,240,232,0.4)" fontSize={10} tickLine={false} axisLine={false} />
            <YAxis stroke="rgba(245,240,232,0.4)" fontSize={10} tickLine={false} axisLine={false} />
            <Tooltip contentStyle={tooltipStyle} cursor={{ fill: "rgba(201,168,76,0.05)" }} />
            <Bar dataKey="loss_ha" fill="rgba(201,168,76,0.45)" radius={[3, 3, 0, 0]} />
            <Line type="monotone" dataKey="loss_ha" stroke={GOLD_BRIGHT} strokeWidth={2} dot={false} />
          </ComposedChart>
        </ResponsiveContainer>
      ) : (
        <p className="vr-empty">Annual loss series unavailable.</p>
      )}
      <div className="vr-stat-grid">
        <Stat label="Annual loss rate"
              value={forest.annual_loss_rate_pct != null ? `${forest.annual_loss_rate_pct}%` : "—"} />
        <Stat label="Intact forest"
              value={forest.intact_forest_pct != null ? `${forest.intact_forest_pct}%` : "—"} />
      </div>
    </div>
  );
}

function RiskBlock({ score, level, recBadge, components, confidence, keyFlags }) {
  return (
    <div>
      <div className="vr-risk-row">
        <ScoreRing score={score ?? 0} animating size={170} />
        <div className="vr-risk-meta">
          <div className="vr-risk-pill" style={{ background: recBadge.color, color: "#0a0a0a" }}>
            {recBadge.label}
          </div>
          <div className="vr-risk-sub">
            {(level || "—").toString().toUpperCase()} ·{" "}
            CONFIDENCE {typeof confidence === "number" ? `${Math.round(confidence * 100)}%` : "—"}
          </div>
          <div className="vr-cmp-stack">
            {components.map((c) => (
              <ComponentBar key={c.key} label={c.label} value={c.value} />
            ))}
          </div>
        </div>
      </div>
      {Array.isArray(keyFlags) && keyFlags.length > 0 && (
        <div className="vr-flags">
          <span className="vr-flags-label">KEY FLAGS</span>
          <ul>
            {keyFlags.map((f, i) => <li key={i}>{f}</li>)}
          </ul>
        </div>
      )}
    </div>
  );
}

function ComponentBar({ label, value }) {
  const numeric = typeof value === "number" ? value : 0;
  const v = Math.max(0, Math.min(100, numeric));
  const color = v >= 70 ? GREEN : v >= 40 ? AMBER : RED;
  return (
    <div className="vr-cmp">
      <div className="vr-cmp-head">
        <span>{label}</span>
        <span className="vr-cmp-val">{typeof value === "number" ? value : "—"}</span>
      </div>
      <div className="vr-cmp-track">
        <motion.div
          initial={{ width: 0 }}
          animate={{ width: `${v}%` }}
          transition={{ duration: 0.8, ease: [0.2, 0.8, 0.2, 1] }}
          className="vr-cmp-fill"
          style={{ background: color }}
        />
      </div>
    </div>
  );
}

function NarrativeBlock({ narrative }) {
  const summary = narrative.executive_summary || "No executive summary available.";
  return (
    <div>
      <blockquote className="vr-quote">
        <span className="vr-quote-mark">“</span>
        {summary}
      </blockquote>
      {narrative.recommendation_text && (
        <div className="vr-rec-text">
          <span className="vr-rec-label">RECOMMENDATION</span>
          <p>{narrative.recommendation_text}</p>
        </div>
      )}
    </div>
  );
}

// ────────────────────────────────────────────────────────────────────
// Inline scoped CSS
// ────────────────────────────────────────────────────────────────────

function ReportStyles() {
  return (
    <style>{`
      .vr-page {
        min-height: 100vh;
        padding: 24px 32px 96px;
        color: var(--color-text-primary);
        background:
          radial-gradient(ellipse 60% 40% at 50% -10%, rgba(201,168,76,0.10), transparent 65%),
          radial-gradient(ellipse 70% 40% at 100% 110%, rgba(76,175,125,0.06), transparent 60%),
          var(--color-bg);
        background-attachment: fixed;
      }

      .vr-head {
        display: flex;
        flex-direction: column;
        gap: 16px;
        margin-bottom: 24px;
        padding-bottom: 16px;
        border-bottom: 1px solid rgba(201,168,76,0.12);
      }
      .vr-wordmark {
        font-family: var(--font-display);
        font-size: 1.5rem;
        font-weight: 600;
        letter-spacing: 0.04em;
        color: var(--color-gold-bright);
        text-decoration: none;
      }
      .vr-wordmark-sub {
        font-family: var(--font-mono);
        font-size: 0.7rem;
        letter-spacing: 0.4em;
        margin-left: 8px;
        color: var(--color-text-secondary);
      }
      .vr-meta-bar {
        display: grid;
        grid-template-columns: repeat(5, 1fr);
        gap: 16px;
        background: rgba(255,255,255,0.025);
        border: 1px solid rgba(201,168,76,0.15);
        border-radius: 14px;
        padding: 14px 18px;
        backdrop-filter: blur(20px) saturate(140%);
        -webkit-backdrop-filter: blur(20px) saturate(140%);
      }
      @media (max-width: 720px) { .vr-meta-bar { grid-template-columns: repeat(2, 1fr); } }
      .vr-meta-cell { display: flex; flex-direction: column; gap: 3px; min-width: 0; }
      .vr-meta-k {
        font-family: var(--font-mono);
        font-size: 0.62rem;
        letter-spacing: 0.2em;
        text-transform: uppercase;
        color: var(--color-text-muted);
      }
      .vr-meta-v {
        font-family: var(--font-mono);
        font-size: 0.92rem;
        color: var(--color-text-primary);
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      .vr-meta-of {
        color: var(--color-text-muted);
        font-size: 0.7rem;
        margin-left: 3px;
      }

      .vr-cards {
        display: grid;
        grid-template-columns: repeat(2, 1fr);
        gap: 16px;
      }
      @media (max-width: 900px) { .vr-cards { grid-template-columns: 1fr; } }

      .vr-card {
        background: rgba(255,255,255,0.025);
        border: 1px solid rgba(201,168,76,0.15);
        border-radius: 14px;
        padding: 18px 20px;
        backdrop-filter: blur(20px) saturate(140%);
        -webkit-backdrop-filter: blur(20px) saturate(140%);
        transition: border-color 0.22s ease;
      }
      .vr-card:hover { border-color: rgba(201,168,76,0.32); }
      .vr-card-head {
        display: flex;
        justify-content: space-between;
        align-items: center;
        gap: 10px;
        margin-bottom: 12px;
      }
      .vr-card-head h3 {
        font-family: var(--font-display);
        font-size: 1.1rem;
        font-weight: 500;
        margin: 0;
        color: var(--color-text-primary);
      }
      .vr-approx {
        font-family: var(--font-mono);
        font-size: 0.62rem;
        letter-spacing: 0.18em;
        color: var(--color-risk-medium);
        background: rgba(232,164,74,0.08);
        border: 1px solid rgba(232,164,74,0.35);
        padding: 3px 7px;
        border-radius: 2px;
      }

      .vr-kv-table {
        width: 100%;
        border-collapse: collapse;
        font-family: var(--font-mono);
        font-size: 0.85rem;
      }
      .vr-kv-table td {
        padding: 6px 0;
        border-bottom: 1px solid rgba(245,240,232,0.04);
      }
      .vr-kv-table td:first-child {
        color: var(--color-text-muted);
        width: 38%;
      }

      .vr-map-frame {
        position: relative;
        border-radius: 12px;
        overflow: hidden;
        border: 1px solid rgba(201,168,76,0.18);
        background: #0a0a0a;
        aspect-ratio: 5 / 3;
      }
      .vr-map-frame img { width: 100%; height: 100%; object-fit: cover; display: block; }
      .vr-map-placeholder {
        height: 100%;
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        gap: 4px;
        color: var(--color-text-muted);
      }
      .vr-map-eyebrow {
        font-family: var(--font-mono);
        font-size: 0.62rem;
        letter-spacing: 0.22em;
        color: var(--color-gold);
      }
      .vr-map-placeholder code {
        font-family: var(--font-mono);
        font-size: 0.8rem;
        color: var(--color-gold-bright);
      }
      .vr-map-badge {
        position: absolute;
        top: 12px;
        left: 12px;
        background: rgba(0,0,0,0.65);
        color: var(--color-gold-bright);
        padding: 4px 10px;
        font-family: var(--font-mono);
        font-size: 0.7rem;
        letter-spacing: 0.12em;
        border-radius: 2px;
      }
      .vr-fire-flag {
        position: absolute;
        top: 12px;
        right: 12px;
        background: rgba(232,90,74,0.85);
        color: #fff;
        padding: 3px 9px;
        font-family: var(--font-mono);
        font-size: 0.65rem;
        letter-spacing: 0.16em;
        border-radius: 2px;
      }

      .vr-stat-grid {
        display: grid;
        grid-template-columns: repeat(2, 1fr);
        gap: 10px 18px;
        margin-top: 14px;
      }
      .vr-stat-grid > div { display: flex; flex-direction: column; gap: 2px; }
      .vr-stat-k {
        font-family: var(--font-mono);
        font-size: 0.62rem;
        text-transform: uppercase;
        letterSpacing: 0.12em;
        color: var(--color-text-muted);
      }
      .vr-stat-v {
        font-family: var(--font-mono);
        font-size: 0.85rem;
        color: var(--color-text-primary);
      }

      .vr-veg-callout {
        display: flex;
        align-items: baseline;
        justify-content: space-between;
        gap: 14px;
        margin-bottom: 12px;
        flex-wrap: wrap;
      }
      .vr-veg-num {
        font-family: var(--font-display);
        font-size: 1.9rem;
        font-weight: 500;
        color: var(--color-text-primary);
      }
      .vr-veg-num-sub {
        color: var(--color-text-muted);
        font-size: 0.85rem;
        margin-left: 4px;
      }
      .vr-veg-status {
        font-family: var(--font-mono);
        font-size: 0.7rem;
        letter-spacing: 0.18em;
        text-transform: uppercase;
        color: var(--color-text-muted);
      }
      .vr-trend-pill {
        font-family: var(--font-mono);
        font-size: 0.65rem;
        letter-spacing: 0.18em;
        padding: 3px 9px;
        background: rgba(201,168,76,0.08);
        color: var(--color-gold-bright);
        border-radius: 2px;
      }

      .vr-risk-row {
        display: grid;
        grid-template-columns: auto 1fr;
        gap: 24px;
        align-items: center;
      }
      @media (max-width: 720px) { .vr-risk-row { grid-template-columns: 1fr; justify-items: center; } }
      .vr-risk-meta { display: flex; flex-direction: column; gap: 8px; min-width: 0; }
      .vr-risk-pill {
        align-self: flex-start;
        font-family: var(--font-mono);
        font-size: 0.7rem;
        letter-spacing: 0.22em;
        font-weight: 600;
        padding: 5px 12px;
        border-radius: 2px;
      }
      .vr-risk-sub {
        font-family: var(--font-mono);
        font-size: 0.65rem;
        letter-spacing: 0.15em;
        color: var(--color-text-muted);
      }
      .vr-cmp-stack {
        display: flex;
        flex-direction: column;
        gap: 6px;
        margin-top: 8px;
      }
      .vr-cmp-head {
        display: flex;
        justify-content: space-between;
        font-family: var(--font-mono);
        font-size: 0.74rem;
        margin-bottom: 3px;
        color: rgba(245,240,232,0.85);
      }
      .vr-cmp-val { color: var(--color-text-primary); }
      .vr-cmp-track {
        height: 4px;
        background: rgba(245,240,232,0.06);
        border-radius: 1px;
        overflow: hidden;
      }
      .vr-cmp-fill { height: 100%; }

      .vr-flags { margin-top: 14px; }
      .vr-flags-label {
        font-family: var(--font-mono);
        font-size: 0.62rem;
        letter-spacing: 0.2em;
        color: var(--color-gold);
        display: block;
        margin-bottom: 6px;
      }
      .vr-flags ul {
        list-style: none;
        padding: 0;
        margin: 0;
        font-family: var(--font-mono);
        font-size: 0.78rem;
        color: rgba(245,240,232,0.85);
      }
      .vr-flags li {
        background: rgba(232,164,74,0.06);
        border-left: 2px solid var(--color-risk-medium);
        padding: 5px 10px;
        margin-bottom: 4px;
      }

      .vr-quote {
        margin: 0 0 14px;
        padding: 6px 0 6px 22px;
        font-family: var(--font-display);
        font-style: italic;
        font-size: 1.1rem;
        line-height: 1.55;
        position: relative;
        color: var(--color-text-primary);
      }
      .vr-quote-mark {
        position: absolute;
        left: -4px;
        top: -8px;
        font-size: 2.6rem;
        color: var(--color-gold);
        font-family: var(--font-display);
        line-height: 1;
      }
      .vr-rec-text {
        background: rgba(255,255,255,0.025);
        border-left: 2px solid var(--color-gold);
        padding: 10px 14px;
        margin-top: 10px;
      }
      .vr-rec-label {
        font-family: var(--font-mono);
        font-size: 0.62rem;
        letter-spacing: 0.22em;
        color: var(--color-gold);
      }
      .vr-rec-text p {
        margin: 4px 0 0;
        font-family: var(--font-body);
        font-size: 0.92rem;
        color: rgba(245,240,232,0.85);
        line-height: 1.5;
      }

      .vr-actions {
        position: sticky;
        bottom: 18px;
        margin-top: 24px;
        display: flex;
        gap: 10px;
        justify-content: center;
        z-index: 10;
      }
      .vr-btn {
        background: rgba(255,255,255,0.04);
        border: 1px solid rgba(201,168,76,0.32);
        color: var(--color-gold-bright);
        font-family: var(--font-mono);
        font-size: 0.75rem;
        font-weight: 500;
        letter-spacing: 0.22em;
        padding: 12px 20px;
        border-radius: 6px;
        backdrop-filter: blur(14px);
        -webkit-backdrop-filter: blur(14px);
        cursor: pointer;
        text-decoration: none;
        transition: background 0.18s ease, border-color 0.18s ease;
      }
      .vr-btn:hover {
        background: rgba(201,168,76,0.14);
        border-color: var(--color-gold-bright);
      }
      .vr-btn-primary {
        background: var(--color-gold);
        color: #0a0a0a;
        border-color: var(--color-gold);
      }
      .vr-btn-primary:hover {
        background: var(--color-gold-bright);
        border-color: var(--color-gold-bright);
        color: #0a0a0a;
      }

      .vr-empty {
        font-family: var(--font-mono);
        font-size: 0.78rem;
        color: var(--color-text-muted);
        margin: 16px 0;
      }

      /* Skeleton */
      @keyframes vr-shim {
        0%   { background-position: -200px 0; }
        100% { background-position: 200px 0; }
      }
      .vr-skeletons {
        display: grid;
        grid-template-columns: repeat(2, 1fr);
        gap: 16px;
      }
      @media (max-width: 900px) { .vr-skeletons { grid-template-columns: 1fr; } }
      .vr-skel { display: flex; flex-direction: column; gap: 10px; min-height: 220px; }
      .vr-skel-line {
        height: 12px;
        border-radius: 6px;
        background: linear-gradient(
          90deg,
          rgba(201,168,76,0.05) 25%,
          rgba(201,168,76,0.18) 50%,
          rgba(201,168,76,0.05) 75%
        );
        background-size: 400px 100%;
        animation: vr-shim 1.5s linear infinite;
      }
      .vr-skel-line.short { width: 60%; }

      .vr-error {
        max-width: 480px;
        margin: 80px auto 0;
        text-align: center;
        background: rgba(232,90,74,0.06);
        border: 1px solid rgba(232,90,74,0.32);
        padding: 28px 32px;
        border-radius: 14px;
      }
      .vr-error h2 {
        font-family: var(--font-display);
        font-size: 1.6rem;
        margin: 0 0 10px;
        color: var(--color-risk-high);
      }
      .vr-error p {
        font-family: var(--font-mono);
        font-size: 0.85rem;
        color: var(--color-text-secondary);
        margin: 0 0 18px;
      }
      .vr-cta {
        display: inline-block;
        background: var(--color-gold);
        color: #0a0a0a;
        padding: 10px 22px;
        border-radius: 3px;
        font-family: var(--font-mono);
        font-size: 0.72rem;
        font-weight: 600;
        letter-spacing: 0.22em;
        text-decoration: none;
      }
    `}</style>
  );
}
