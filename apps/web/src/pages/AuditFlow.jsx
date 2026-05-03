/**
 * AuditFlow — the heart of the product.
 *
 * Left 40% : VERITAS logo, input form, terminal log, progress bar
 * Right 60%: live result cards that slide in as each pipeline step
 *            completes (project identity, fire map, NDVI, forest cover,
 *            risk score, narrative)
 *
 * All audit state lives in `useAudit()`. This page is mostly layout +
 * presentation; the hook handles SSE plumbing.
 */
import { useMemo } from "react";
import { Link } from "react-router-dom";
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

import useAudit from "../hooks/useAudit.js";
import DataCard from "../components/audit/DataCard.jsx";
import ScoreRing from "../components/audit/ScoreRing.jsx";
import TerminalLog from "../components/audit/TerminalLog.jsx";

// ────────────────────────────────────────────────────────────────────────
// Constants & small utilities
// ────────────────────────────────────────────────────────────────────────

const REGISTRY_PILLS = [
  { label: "VERRA", prefix: "VCS-" },
  { label: "GOLD STANDARD", prefix: "GS-" },
  { label: "ACR", prefix: "ACR-" },
  { label: "CAR", prefix: "CAR-" },
];

// Tiny lookup so the project card shows a flag without pulling in a 5MB
// flag library. Falls back to a globe for anything not listed.
const COUNTRY_FLAGS = {
  Brazil: "🇧🇷",
  Kenya: "🇰🇪",
  Indonesia: "🇮🇩",
  India: "🇮🇳",
  Cambodia: "🇰🇭",
  Peru: "🇵🇪",
  Colombia: "🇨🇴",
  Mexico: "🇲🇽",
  China: "🇨🇳",
  Honduras: "🇭🇳",
  Guatemala: "🇬🇹",
  Nicaragua: "🇳🇮",
  Argentina: "🇦🇷",
  USA: "🇺🇸",
  "United States": "🇺🇸",
  Tanzania: "🇹🇿",
  Uganda: "🇺🇬",
  Ethiopia: "🇪🇹",
  Ghana: "🇬🇭",
  Vietnam: "🇻🇳",
  Thailand: "🇹🇭",
  Philippines: "🇵🇭",
};

function flagFor(country) {
  if (!country) return "🌍";
  return COUNTRY_FLAGS[country] || "🌍";
}

const PREFIX_RE = /^(VCS|GS|ACR|CAR)[-_]?\d*/i;
function isValidPrefix(serial) {
  return PREFIX_RE.test(serial.trim());
}

function mapboxImage(lat, lon, approximate) {
  const token = import.meta.env.VITE_MAPBOX_TOKEN;
  if (!token || lat == null || lon == null) return null;
  // Wider zoom when we're showing a country centroid — at z=8 the
  // satellite tile would just be dark ocean half the time.
  const zoom = approximate ? 5 : 8;
  return `https://api.mapbox.com/styles/v1/mapbox/satellite-v9/static/${lon},${lat},${zoom},0/600x400@2x?access_token=${token}`;
}

const RECOMMENDATION_BADGE = {
  ISSUE: { label: "FULL ISSUANCE", color: "var(--color-risk-low)" },
  FULL_ISSUANCE: { label: "FULL ISSUANCE", color: "var(--color-risk-low)" },
  FULL_ISSUE: { label: "FULL ISSUANCE", color: "var(--color-risk-low)" },
  ISSUE_FULL: { label: "FULL ISSUANCE", color: "var(--color-risk-low)" },
  FLAG: { label: "PARTIAL FLAG", color: "var(--color-risk-medium)" },
  PARTIAL_FLAG: { label: "PARTIAL FLAG", color: "var(--color-risk-medium)" },
  PARTIAL: { label: "PARTIAL FLAG", color: "var(--color-risk-medium)" },
  HOLD: { label: "HOLD", color: "var(--color-risk-medium)" },
  REVIEW: { label: "MANUAL REVIEW", color: "var(--color-risk-medium)" },
  REJECT: { label: "REJECT", color: "var(--color-risk-high)" },
  DENY: { label: "REJECT", color: "var(--color-risk-high)" },
};

function pickBadge(rec) {
  if (!rec) return { label: "—", color: "var(--color-text-muted)" };
  const key = String(rec).toUpperCase().trim().replace(/\s+/g, "_");
  return (
    RECOMMENDATION_BADGE[key] ||
    { label: String(rec).toUpperCase(), color: "var(--color-gold-bright)" }
  );
}

// ────────────────────────────────────────────────────────────────────────
// Page
// ────────────────────────────────────────────────────────────────────────

export default function AuditFlow() {
  const audit = useAudit();
  const {
    serial,
    setSerial,
    status,
    currentStep,
    progressPct,
    logs,
    projectData,
    coords,
    fireData,
    vegetationData,
    forestData,
    riskScore,
    narrative,
    errorMsg,
    startAudit,
    resetAudit,
    downloadReport,
  } = audit;

  const isRunning = status === "starting" || status === "running";
  const validPrefix = serial.trim() === "" || isValidPrefix(serial);

  const fireMapUrl = useMemo(
    () => (coords ? mapboxImage(coords.lat, coords.lon, coords.approximate) : null),
    [coords]
  );

  const ndviSeries = useMemo(() => {
    if (!vegetationData) return null;
    const { ndvi_3yr_ago, ndvi_1yr_ago, current_ndvi } = vegetationData;
    if (ndvi_3yr_ago == null && ndvi_1yr_ago == null && current_ndvi == null) {
      return null;
    }
    return [
      { period: "3 Years Ago", ndvi: ndvi_3yr_ago ?? null },
      { period: "1 Year Ago", ndvi: ndvi_1yr_ago ?? null },
      { period: "Current", ndvi: current_ndvi ?? null },
    ];
  }, [vegetationData]);

  const ndviStroke = useMemo(() => {
    const s = String(vegetationData?.health_status || "").toUpperCase();
    if (s === "HEALTHY" || s === "GOOD") return "#4caf7d";
    if (s === "DEGRADED" || s === "MODERATE") return "#e8a44a";
    if (s === "CRITICAL" || s === "POOR") return "#e85a4a";
    return "#4caf7d";
  }, [vegetationData]);

  const forestSeries = useMemo(() => {
    const raw = forestData?.annual_loss_ha;
    if (!Array.isArray(raw) || raw.length === 0) return null;
    return raw.map((row, i) => {
      if (typeof row === "number") {
        return { year: `Y-${raw.length - i}`, loss_ha: row };
      }
      return {
        year: row.year ?? `Y-${raw.length - i}`,
        loss_ha: row.loss_ha ?? row.ha ?? row.value ?? 0,
      };
    });
  }, [forestData]);

  const componentBars = useMemo(() => {
    const cs = riskScore?.component_scores;
    if (!cs) return null;
    return [
      { key: "fire_risk", label: "Fire risk", value: cs.fire_risk },
      { key: "vegetation_integrity", label: "Vegetation integrity", value: cs.vegetation_integrity },
      { key: "deforestation_risk", label: "Deforestation risk", value: cs.deforestation_risk },
      { key: "climate_stress", label: "Climate stress", value: cs.climate_stress },
      { key: "registry_compliance", label: "Registry compliance", value: cs.registry_compliance },
    ];
  }, [riskScore]);

  const recBadge = pickBadge(riskScore?.recommendation);

  return (
    <main
      style={{
        minHeight: "100vh",
        background: "var(--color-bg)",
        color: "var(--color-text-primary)",
        display: "grid",
        gridTemplateColumns: "minmax(380px, 40%) 1fr",
      }}
    >
      {/* ───────────────────── LEFT PANEL ───────────────────── */}
      <aside
        style={{
          padding: "32px 36px",
          borderRight: "1px solid var(--color-border)",
          display: "flex",
          flexDirection: "column",
          gap: "20px",
          height: "100vh",
          position: "sticky",
          top: 0,
          overflow: "hidden",
        }}
      >
        <Link
          to="/"
          style={{
            fontFamily: "var(--font-display)",
            color: "var(--color-gold-bright)",
            fontSize: "1.15rem",
            letterSpacing: "0.05em",
            textDecoration: "none",
          }}
        >
          VERITAS
        </Link>

        <div>
          <h1
            style={{
              fontFamily: "var(--font-display)",
              fontSize: "2.1rem",
              margin: 0,
              lineHeight: 1.1,
              fontWeight: 500,
            }}
          >
            Carbon Credit Audit
          </h1>
          <p
            style={{
              color: "var(--color-text-secondary)",
              fontSize: "0.92rem",
              marginTop: "10px",
              marginBottom: 0,
            }}
          >
            Enter any serial number from Verra, Gold Standard, ACR, or CAR.
          </p>
        </div>

        {/* Input form */}
        <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
          <input
            type="text"
            value={serial}
            onChange={(e) => setSerial(e.target.value.toUpperCase())}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !isRunning && validPrefix && serial.trim()) {
                startAudit();
              }
            }}
            placeholder="VCS-1234 or GS-5678 or ACR-9012"
            disabled={isRunning}
            style={{
              width: "100%",
              padding: "12px 14px",
              background: "var(--color-bg-elevated)",
              border: `1px solid ${
                !validPrefix
                  ? "var(--color-risk-high)"
                  : "var(--color-border)"
              }`,
              borderRadius: "3px",
              color: "var(--color-text-primary)",
              fontFamily: "var(--font-mono)",
              fontSize: "0.95rem",
              letterSpacing: "0.05em",
              outline: "none",
              transition: "border-color 0.18s",
            }}
            onFocus={(e) => {
              if (validPrefix) e.target.style.borderColor = "var(--color-gold)";
            }}
            onBlur={(e) => {
              e.target.style.borderColor = !validPrefix
                ? "var(--color-risk-high)"
                : "var(--color-border)";
            }}
          />
          {!validPrefix && (
            <span
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: "0.72rem",
                color: "var(--color-risk-high)",
                letterSpacing: "0.05em",
              }}
            >
              Expected prefix: VCS-, GS-, ACR-, or CAR-
            </span>
          )}

          <button
            type="button"
            onClick={startAudit}
            disabled={isRunning || !serial.trim() || !validPrefix}
            style={{
              width: "100%",
              padding: "13px 14px",
              background:
                isRunning || !serial.trim() || !validPrefix
                  ? "var(--color-gold-dim)"
                  : "var(--color-gold)",
              color: "#0a0a0a",
              border: "none",
              borderRadius: "3px",
              fontFamily: "var(--font-mono)",
              fontWeight: 600,
              letterSpacing: "0.2em",
              fontSize: "0.82rem",
              cursor:
                isRunning || !serial.trim() || !validPrefix
                  ? "not-allowed"
                  : "pointer",
              transition: "background 0.15s",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              gap: "10px",
            }}
          >
            {isRunning ? (
              <>
                <Spinner /> AUDIT RUNNING…
              </>
            ) : (
              "RUN AUDIT"
            )}
          </button>

          <div style={{ display: "flex", gap: "6px", flexWrap: "wrap" }}>
            {REGISTRY_PILLS.map((pill) => (
              <button
                key={pill.label}
                type="button"
                onClick={() => setSerial(pill.prefix)}
                disabled={isRunning}
                style={{
                  flex: 1,
                  background: "transparent",
                  border: "1px solid var(--color-border)",
                  color: "var(--color-text-secondary)",
                  fontFamily: "var(--font-mono)",
                  fontSize: "0.62rem",
                  letterSpacing: "0.18em",
                  padding: "6px 4px",
                  borderRadius: "20px",
                  cursor: isRunning ? "not-allowed" : "pointer",
                  whiteSpace: "nowrap",
                }}
              >
                {pill.label}
              </button>
            ))}
          </div>
        </div>

        {/* Terminal */}
        <div style={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0 }}>
          <div
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: "0.68rem",
              letterSpacing: "0.22em",
              color: "var(--color-text-muted)",
              marginBottom: "6px",
            }}
          >
            // EXECUTION TERMINAL
          </div>
          <div style={{ flex: 1, minHeight: 0 }}>
            <TerminalLog logs={logs} active={isRunning} />
          </div>
        </div>

        {/* Progress bar */}
        <div>
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              fontFamily: "var(--font-mono)",
              fontSize: "0.68rem",
              color: "var(--color-text-muted)",
              marginBottom: "5px",
              letterSpacing: "0.18em",
            }}
          >
            <span>STEP {currentStep || 0} / 8</span>
            <span>{progressPct}%</span>
          </div>
          <div
            style={{
              height: "2px",
              background: "rgba(245,240,232,0.08)",
              borderRadius: "1px",
              overflow: "hidden",
            }}
          >
            <div
              style={{
                height: "100%",
                width: `${progressPct}%`,
                background:
                  status === "error"
                    ? "var(--color-risk-high)"
                    : "var(--color-gold)",
                transition: "width 0.45s cubic-bezier(0.2, 0.8, 0.2, 1)",
              }}
            />
          </div>
        </div>

        {(status === "complete" || status === "error") && (
          <button
            type="button"
            onClick={resetAudit}
            style={{
              alignSelf: "flex-start",
              background: "transparent",
              border: "1px solid var(--color-border)",
              color: "var(--color-text-secondary)",
              fontFamily: "var(--font-mono)",
              fontSize: "0.7rem",
              padding: "6px 14px",
              letterSpacing: "0.18em",
              cursor: "pointer",
              borderRadius: "2px",
            }}
          >
            ↺ NEW AUDIT
          </button>
        )}
      </aside>

      {/* ───────────────────── RIGHT PANEL ───────────────────── */}
      <section
        style={{
          padding: "32px 40px",
          overflowY: "auto",
          height: "100vh",
        }}
      >
        {status === "idle" && <IdlePane />}

        <DataCard
          title="Project Identity"
          step={1}
          visible={!!projectData}
        >
          <ProjectTable
            project={projectData}
            flag={flagFor(projectData?.country)}
            coords={coords}
          />
        </DataCard>

        <DataCard
          title="Fire Detection — NASA FIRMS"
          step={3}
          visible={!!fireData}
          approximate={!!fireData?.approximate}
        >
          <FireMapCard fireData={fireData} mapUrl={fireMapUrl} coords={coords} />
        </DataCard>

        <DataCard
          title="Vegetation Index — Sentinel-2 NDVI"
          step={5}
          visible={!!vegetationData}
          approximate={!!vegetationData?.approximate}
        >
          <NdviCard
            data={ndviSeries}
            vegetation={vegetationData}
            stroke={ndviStroke}
          />
        </DataCard>

        <DataCard
          title="Forest Cover — Global Forest Watch"
          step={6}
          visible={!!forestData}
          approximate={!!forestData?.approximate}
        >
          <ForestCard data={forestSeries} forest={forestData} />
        </DataCard>

        <DataCard
          title="Risk Score — Llama 3.3 70B"
          step={7}
          visible={!!riskScore}
        >
          <RiskScoreCard
            score={riskScore?.overall_score ?? 0}
            level={riskScore?.risk_level}
            recommendation={recBadge}
            components={componentBars}
            confidence={riskScore?.confidence}
            keyFlags={riskScore?.key_flags}
          />
        </DataCard>

        <DataCard
          title="Audit Narrative — Claude Opus"
          step={8}
          visible={!!narrative}
        >
          <NarrativeCard narrative={narrative} onDownload={downloadReport} />
        </DataCard>

        {status === "error" && (
          <div
            style={{
              background: "rgba(232,90,74,0.08)",
              border: "1px solid rgba(232,90,74,0.4)",
              padding: "16px 20px",
              color: "var(--color-risk-high)",
              fontFamily: "var(--font-mono)",
              fontSize: "0.85rem",
              borderRadius: "3px",
              marginTop: "16px",
            }}
          >
            ✗ {errorMsg || "Audit failed. See terminal for details."}
          </div>
        )}
      </section>
    </main>
  );
}

// ────────────────────────────────────────────────────────────────────────
// Sub-components (kept in this file so the audit page is self-contained)
// ────────────────────────────────────────────────────────────────────────

function Spinner() {
  return (
    <span
      aria-hidden
      style={{
        display: "inline-block",
        width: "11px",
        height: "11px",
        border: "1.5px solid rgba(10,10,10,0.3)",
        borderTopColor: "#0a0a0a",
        borderRadius: "50%",
        animation: "veritas-spin 0.7s linear infinite",
      }}
    >
      <style>{`
        @keyframes veritas-spin { to { transform: rotate(360deg); } }
      `}</style>
    </span>
  );
}

function IdlePane() {
  return (
    <div
      style={{
        height: "100%",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        color: "var(--color-text-muted)",
      }}
    >
      <div
        style={{
          fontSize: "0.68rem",
          fontFamily: "var(--font-mono)",
          letterSpacing: "0.4em",
          color: "var(--color-gold)",
          marginBottom: "18px",
        }}
      >
        STANDBY
      </div>
      <h2
        style={{
          fontFamily: "var(--font-display)",
          fontSize: "1.6rem",
          fontWeight: 400,
          fontStyle: "italic",
          maxWidth: "440px",
          textAlign: "center",
          color: "var(--color-text-secondary)",
          margin: 0,
        }}
      >
        “Trust, but verify.”
      </h2>
      <p
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: "0.7rem",
          color: "var(--color-text-muted)",
          letterSpacing: "0.12em",
          marginTop: "20px",
          textAlign: "center",
          maxWidth: "440px",
        }}
      >
        Live audit data populates this pane as the pipeline runs.
      </p>
    </div>
  );
}

function ProjectTable({ project, flag, coords }) {
  const rows = [
    ["Serial", project?.serial || "—"],
    ["Project name", project?.project_name || "—"],
    ["Registry", project?.registry || "—"],
    ["Country", `${flag} ${project?.country || "Unknown"}`],
    [
      "Coordinates",
      coords?.lat != null && coords?.lon != null
        ? `${coords.lat.toFixed(4)}, ${coords.lon.toFixed(4)}${
            coords.approximate ? " (centroid)" : ""
          }`
        : "—",
    ],
    ["Methodology", project?.methodology || "—"],
    ["Credits issued", project?.credits_issued ?? "—"],
    ["Last verification", project?.last_verification_date || "—"],
    ["Data source", project?.data_source || "—"],
  ];
  return (
    <table
      style={{
        width: "100%",
        fontFamily: "var(--font-mono)",
        fontSize: "0.85rem",
        borderCollapse: "collapse",
      }}
    >
      <tbody>
        {rows.map(([k, v]) => (
          <tr key={k}>
            <td
              style={{
                color: "var(--color-text-muted)",
                padding: "5px 0",
                width: "40%",
              }}
            >
              {k}
            </td>
            <td style={{ color: "var(--color-text-primary)", padding: "5px 0" }}>
              {v}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function FireMapCard({ fireData, mapUrl, coords }) {
  const hotspots = fireData?.hotspot_count ?? 0;
  const highConf = fireData?.high_confidence_count ?? 0;
  const fireRiskFlag =
    fireData?.fire_risk_flag === true ||
    (typeof fireData?.fire_risk_flag === "undefined" && highConf > 0);

  return (
    <div>
      <div
        style={{
          position: "relative",
          borderRadius: "3px",
          overflow: "hidden",
          border: "1px solid var(--color-border)",
          aspectRatio: "3 / 2",
          background: "#0a0a0a",
        }}
      >
        {mapUrl ? (
          <img
            src={mapUrl}
            alt={`Satellite tile near ${coords?.lat}, ${coords?.lon}`}
            style={{ width: "100%", height: "100%", display: "block", objectFit: "cover" }}
          />
        ) : (
          <div
            style={{
              width: "100%",
              height: "100%",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              color: "var(--color-text-muted)",
              fontFamily: "var(--font-mono)",
              fontSize: "0.78rem",
              padding: "1rem",
              textAlign: "center",
            }}
          >
            {coords
              ? `${coords.lat?.toFixed(4)}, ${coords.lon?.toFixed(4)} — set VITE_MAPBOX_TOKEN to render satellite tile`
              : "no coordinates available"}
          </div>
        )}
        <div
          style={{
            position: "absolute",
            top: 12,
            left: 12,
            background: "rgba(0,0,0,0.65)",
            color: "var(--color-gold-bright)",
            padding: "5px 10px",
            fontFamily: "var(--font-mono)",
            fontSize: "0.72rem",
            letterSpacing: "0.12em",
            borderRadius: "2px",
          }}
        >
          {hotspots} HOTSPOTS · {highConf} HIGH-CONF
        </div>
        {fireRiskFlag && (
          <div
            style={{
              position: "absolute",
              top: 12,
              right: 12,
              display: "flex",
              alignItems: "center",
              gap: 6,
              background: "rgba(232,90,74,0.85)",
              color: "#fff",
              padding: "4px 10px",
              fontFamily: "var(--font-mono)",
              fontSize: "0.7rem",
              letterSpacing: "0.16em",
              borderRadius: "2px",
            }}
          >
            <span
              style={{
                width: 8,
                height: 8,
                borderRadius: "50%",
                background: "#fff",
                animation: "veritas-pulse 1.4s infinite",
              }}
            />
            FIRE RISK
          </div>
        )}
      </div>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(2, 1fr)",
          gap: "10px 24px",
          marginTop: "14px",
          fontFamily: "var(--font-mono)",
          fontSize: "0.85rem",
        }}
      >
        <Stat label="Most recent" value={fireData?.most_recent_fire_date || "—"} />
        <Stat
          label="Nearest fire"
          value={
            fireData?.nearest_fire_km != null
              ? `${fireData.nearest_fire_km} km`
              : "—"
          }
        />
        <Stat label="Source" value={fireData?.source || "VIIRS_SNPP_NRT"} />
        <Stat label="Days scanned" value={fireData?.days_scanned ?? 90} />
      </div>

      <style>{`
        @keyframes veritas-pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.4; } }
      `}</style>
    </div>
  );
}

function Stat({ label, value }) {
  return (
    <div>
      <div
        style={{
          color: "var(--color-text-muted)",
          fontSize: "0.68rem",
          textTransform: "uppercase",
          letterSpacing: "0.12em",
        }}
      >
        {label}
      </div>
      <div style={{ color: "var(--color-text-primary)" }}>{value}</div>
    </div>
  );
}

function NdviCard({ data, vegetation, stroke }) {
  const change = vegetation?.vegetation_change_pct;
  const arrow =
    typeof change === "number" ? (change >= 0 ? "↑" : "↓") : "";
  const changeColor =
    typeof change === "number"
      ? change >= 0
        ? "var(--color-risk-low)"
        : "var(--color-risk-high)"
      : "var(--color-text-muted)";

  return (
    <div>
      <div
        style={{
          display: "flex",
          alignItems: "baseline",
          gap: 16,
          marginBottom: 12,
          flexWrap: "wrap",
        }}
      >
        <span
          style={{
            fontFamily: "var(--font-display)",
            fontSize: "2rem",
            color: changeColor,
            fontWeight: 500,
          }}
        >
          {arrow} {typeof change === "number" ? `${change.toFixed(1)}%` : "—"}
        </span>
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: "0.72rem",
            color: "var(--color-text-muted)",
            letterSpacing: "0.12em",
            textTransform: "uppercase",
          }}
        >
          NDVI {(vegetation?.health_status || "unknown").toLowerCase()}
        </span>
      </div>
      {data ? (
        <ResponsiveContainer width="100%" height={180}>
          <AreaChart data={data} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
            <defs>
              <linearGradient id="ndvi-fill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={stroke} stopOpacity={0.45} />
                <stop offset="100%" stopColor={stroke} stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="2 4" stroke="rgba(245,240,232,0.06)" />
            <XAxis
              dataKey="period"
              stroke="rgba(245,240,232,0.4)"
              fontSize={11}
              tickLine={false}
              axisLine={false}
            />
            <YAxis
              stroke="rgba(245,240,232,0.4)"
              fontSize={11}
              tickLine={false}
              axisLine={false}
              domain={[0, 1]}
            />
            <Tooltip
              contentStyle={{
                background: "#0A0A0A",
                border: "1px solid var(--color-border)",
                borderRadius: 3,
                fontFamily: "var(--font-mono)",
                fontSize: 11,
              }}
              cursor={{ stroke: "var(--color-gold-dim)" }}
            />
            <Area
              type="monotone"
              dataKey="ndvi"
              stroke={stroke}
              strokeWidth={2}
              fill="url(#ndvi-fill)"
            />
          </AreaChart>
        </ResponsiveContainer>
      ) : (
        <div
          style={{
            color: "var(--color-text-muted)",
            fontFamily: "var(--font-mono)",
            fontSize: "0.8rem",
          }}
        >
          NDVI series unavailable.
        </div>
      )}
    </div>
  );
}

function ForestCard({ data, forest }) {
  return (
    <div>
      <div
        style={{
          display: "flex",
          alignItems: "baseline",
          gap: 16,
          marginBottom: 12,
          flexWrap: "wrap",
        }}
      >
        <span
          style={{
            fontFamily: "var(--font-display)",
            fontSize: "1.8rem",
            fontWeight: 500,
          }}
        >
          {forest?.total_loss_5yr_ha ?? "—"}{" "}
          <span style={{ fontSize: "0.85rem", color: "var(--color-text-muted)" }}>
            ha lost (5yr)
          </span>
        </span>
        {forest?.trend && (
          <span
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: "0.7rem",
              padding: "3px 8px",
              background: "rgba(201,168,76,0.08)",
              color: "var(--color-gold-bright)",
              letterSpacing: "0.18em",
              borderRadius: 2,
            }}
          >
            {forest.trend}
          </span>
        )}
      </div>
      {data ? (
        <ResponsiveContainer width="100%" height={180}>
          <ComposedChart data={data} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="2 4" stroke="rgba(245,240,232,0.06)" />
            <XAxis
              dataKey="year"
              stroke="rgba(245,240,232,0.4)"
              fontSize={11}
              tickLine={false}
              axisLine={false}
            />
            <YAxis
              stroke="rgba(245,240,232,0.4)"
              fontSize={11}
              tickLine={false}
              axisLine={false}
            />
            <Tooltip
              contentStyle={{
                background: "#0A0A0A",
                border: "1px solid var(--color-border)",
                borderRadius: 3,
                fontFamily: "var(--font-mono)",
                fontSize: 11,
              }}
              cursor={{ fill: "rgba(201,168,76,0.05)" }}
            />
            <Bar dataKey="loss_ha" fill="var(--color-gold-dim)" />
            <Line
              type="monotone"
              dataKey="loss_ha"
              stroke="var(--color-gold-bright)"
              strokeWidth={2}
              dot={false}
            />
          </ComposedChart>
        </ResponsiveContainer>
      ) : (
        <div
          style={{
            color: "var(--color-text-muted)",
            fontFamily: "var(--font-mono)",
            fontSize: "0.8rem",
          }}
        >
          Annual loss series unavailable.
          {forest?.annual_loss_rate_pct != null &&
            ` Avg annual loss: ${forest.annual_loss_rate_pct}%.`}
        </div>
      )}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 1fr",
          gap: "10px 24px",
          marginTop: 12,
          fontFamily: "var(--font-mono)",
          fontSize: "0.85rem",
        }}
      >
        <Stat
          label="Annual loss rate"
          value={
            forest?.annual_loss_rate_pct != null
              ? `${forest.annual_loss_rate_pct}%`
              : "—"
          }
        />
        <Stat
          label="Intact forest"
          value={
            forest?.intact_forest_pct != null
              ? `${forest.intact_forest_pct}%`
              : "—"
          }
        />
      </div>
    </div>
  );
}

function RiskScoreCard({ score, level, recommendation, components, confidence, keyFlags }) {
  return (
    <div>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "auto 1fr",
          gap: 32,
          alignItems: "center",
        }}
      >
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            gap: 12,
          }}
        >
          <ScoreRing score={score} animating size={180} />
          <div
            style={{
              padding: "5px 12px",
              background: recommendation.color,
              color: "#0a0a0a",
              fontFamily: "var(--font-mono)",
              fontSize: "0.74rem",
              letterSpacing: "0.2em",
              fontWeight: 600,
              borderRadius: 2,
            }}
          >
            {recommendation.label}
          </div>
          <div
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: "0.66rem",
              color: "var(--color-text-muted)",
              letterSpacing: "0.12em",
            }}
          >
            {(level || "—").toString().toUpperCase()} · CONFIDENCE{" "}
            {typeof confidence === "number"
              ? `${Math.round(confidence * 100)}%`
              : "—"}
          </div>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {(components || []).map((c) => (
            <ComponentBar key={c.key} label={c.label} value={c.value} />
          ))}
        </div>
      </div>
      {Array.isArray(keyFlags) && keyFlags.length > 0 && (
        <div style={{ marginTop: 18 }}>
          <div
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: "0.65rem",
              letterSpacing: "0.18em",
              color: "var(--color-text-muted)",
              marginBottom: 6,
            }}
          >
            KEY FLAGS
          </div>
          <ul
            style={{
              margin: 0,
              padding: 0,
              listStyle: "none",
              fontFamily: "var(--font-mono)",
              fontSize: "0.78rem",
              color: "var(--color-text-secondary)",
              display: "flex",
              flexDirection: "column",
              gap: 4,
            }}
          >
            {keyFlags.map((flag, i) => (
              <li
                key={i}
                style={{
                  background: "rgba(232,164,74,0.06)",
                  borderLeft: "2px solid var(--color-risk-medium)",
                  padding: "5px 10px",
                }}
              >
                {flag}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function ComponentBar({ label, value }) {
  const numeric = typeof value === "number" ? value : 0;
  const v = Math.max(0, Math.min(100, numeric));
  const color =
    v >= 70
      ? "var(--color-risk-low)"
      : v >= 40
      ? "var(--color-risk-medium)"
      : "var(--color-risk-high)";
  return (
    <div>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          fontFamily: "var(--font-mono)",
          fontSize: "0.78rem",
          marginBottom: 3,
        }}
      >
        <span style={{ color: "var(--color-text-secondary)" }}>{label}</span>
        <span style={{ color: "var(--color-text-primary)" }}>
          {typeof value === "number" ? value : "—"}
        </span>
      </div>
      <div
        style={{
          height: 4,
          background: "rgba(245,240,232,0.06)",
          borderRadius: 1,
          overflow: "hidden",
        }}
      >
        <motion.div
          initial={{ width: 0 }}
          animate={{ width: `${v}%` }}
          transition={{ duration: 0.8, ease: [0.2, 0.8, 0.2, 1] }}
          style={{ height: "100%", background: color, borderRadius: 1 }}
        />
      </div>
    </div>
  );
}

function NarrativeCard({ narrative, onDownload }) {
  const summary =
    narrative?.executive_summary ||
    "Narrative unavailable. The audit completed but the AI summary couldn't be generated.";
  return (
    <div>
      <blockquote
        style={{
          margin: 0,
          padding: "10px 0 6px 24px",
          fontFamily: "var(--font-display)",
          fontStyle: "italic",
          fontSize: "1.18rem",
          lineHeight: 1.55,
          color: "var(--color-text-primary)",
          position: "relative",
        }}
      >
        <span
          aria-hidden
          style={{
            position: "absolute",
            left: -4,
            top: -10,
            fontSize: "3rem",
            color: "var(--color-gold)",
            fontFamily: "var(--font-display)",
            lineHeight: 1,
          }}
        >
          “
        </span>
        {summary}
      </blockquote>
      <button
        type="button"
        onClick={onDownload}
        style={{
          marginTop: 18,
          background: "var(--color-gold)",
          color: "#0a0a0a",
          border: "none",
          borderRadius: 3,
          fontFamily: "var(--font-mono)",
          fontSize: "0.78rem",
          letterSpacing: "0.2em",
          fontWeight: 600,
          padding: "11px 20px",
          cursor: "pointer",
        }}
      >
        ↓ DOWNLOAD FULL REPORT
      </button>
    </div>
  );
}
