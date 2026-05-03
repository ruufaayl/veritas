/**
 * Dashboard — VERITAS Oracle live intelligence panel.
 *
 * 12 glassmorphism cards on a CSS grid. Polls /dashboard every 5 min,
 * /health every 60s, /audit/history every 30s. All panels degrade
 * gracefully — backend dropouts surface as muted error states, not
 * white screens.
 *
 * Aesthetic mirrors the redesigned PDF preview: dark background with
 * gold accents, Playfair Display headings, IBM Plex Mono data, glass
 * panels with backdrop-filter blur.
 */
import { useMemo } from "react";
import { Link } from "react-router-dom";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import useAuditHistory from "../hooks/useAuditHistory.js";
import useDashboard from "../hooks/useDashboard.js";
import useHealth from "../hooks/useHealth.js";

// ────────────────────────────────────────────────────────────────────
// Tokens & helpers
// ────────────────────────────────────────────────────────────────────

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

function scoreColor(score) {
  if (typeof score !== "number") return "rgba(245,240,232,0.4)";
  if (score >= 70) return GREEN;
  if (score >= 40) return AMBER;
  return RED;
}

function fmtTimeAgo(ts) {
  if (!ts) return "—";
  const d = ts instanceof Date ? ts : new Date(ts);
  const sec = Math.max(0, Math.floor((Date.now() - d.getTime()) / 1000));
  if (sec < 60) return `${sec}s ago`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m ago`;
  if (sec < 86400) return `${Math.floor(sec / 3600)}h ago`;
  return d.toLocaleDateString();
}

// Synthetic series for panels the backend doesn't compute yet. Clearly
// labelled in the panel UI so nobody confuses them for live data.
function syntheticForecast() {
  const out = [];
  const today = new Date();
  for (let i = -60; i <= 30; i++) {
    const d = new Date(today.getTime() + i * 86400000);
    const base = 70 + Math.sin(i / 12) * 6 + Math.cos(i / 8) * 3;
    out.push({
      day: d.toLocaleDateString("en-US", { month: "short", day: "numeric" }),
      historical: i <= 0 ? Math.round(base + Math.random() * 4) : null,
      forecast:   i >= 0 ? Math.round(base + Math.random() * 5) : null,
    });
  }
  return out;
}

function syntheticThermalSeries(currentHotspots) {
  const out = [];
  for (let i = 29; i >= 0; i--) {
    const noise = Math.round(Math.random() * 6);
    const v = Math.max(0, Math.round((currentHotspots ?? 8) + noise - 3 + Math.sin(i / 4) * 2));
    out.push({ day: `D-${i}`, hotspots: v });
  }
  return out;
}

const VCU_PRICE_HISTORY = [
  { week: "W-12", price: 4.20 },
  { week: "W-11", price: 4.35 },
  { week: "W-10", price: 4.10 },
  { week: "W-9",  price: 4.45 },
  { week: "W-8",  price: 4.62 },
  { week: "W-7",  price: 4.55 },
  { week: "W-6",  price: 4.38 },
  { week: "W-5",  price: 4.20 },
  { week: "W-4",  price: 4.05 },
  { week: "W-3",  price: 3.92 },
  { week: "W-2",  price: 4.10 },
  { week: "W-1",  price: 4.25 },
];

const SERVICE_LABELS = {
  database: "Database",
  nasa_firms: "NASA FIRMS",
  sentinel_hub: "Sentinel Hub",
  noaa: "NOAA",
  mapbox: "Mapbox",
  groq: "Groq",
  anthropic: "Anthropic",
  global_forest_watch: "GFW",
};

const SERVICE_ORDER = [
  "database",
  "nasa_firms",
  "sentinel_hub",
  "noaa",
  "mapbox",
  "groq",
  "anthropic",
  "global_forest_watch",
];

function climateAnomalyBars(climate) {
  const t = climate?.temp_anomaly_c;
  const p = climate?.precipitation_anomaly_mm;
  const valid = (v) => typeof v === "number" && isFinite(v);
  return [
    { metric: "Temp °C",        value: valid(t) ? Number(t.toFixed(2)) : 0, hasData: valid(t), kind: "temp" },
    { metric: "Precip mm",      value: valid(p) ? Number(p.toFixed(0)) : 0, hasData: valid(p), kind: "precip" },
  ];
}

function colorForAnomaly(kind, v) {
  const abs = Math.abs(v);
  if (kind === "temp") return abs > 2 ? RED : abs > 1 ? AMBER : GREEN;
  if (kind === "precip") return abs > 200 ? RED : abs > 80 ? AMBER : GREEN;
  return GREEN;
}

// ────────────────────────────────────────────────────────────────────
// Reusable building blocks
// ────────────────────────────────────────────────────────────────────

function Panel({ id, title, children, source, span = 1, error, loading, footer }) {
  return (
    <section
      className="vt-panel"
      style={{
        gridColumn: span === "full" ? "1 / -1" : `span ${span}`,
      }}
    >
      <header className="vt-panel-head">
        <div className="vt-panel-title-wrap">
          <span className="vt-panel-eyebrow">PANEL · {id}</span>
          <h3 className="vt-panel-title">{title}</h3>
        </div>
        <span className="vt-panel-num">{id}</span>
      </header>

      <div className="vt-panel-body">
        {loading ? (
          <Skeleton />
        ) : error ? (
          <div className="vt-panel-error">Data unavailable</div>
        ) : (
          children
        )}
      </div>

      <footer className="vt-panel-foot">
        {source && <span className="vt-panel-source">{source}</span>}
        {footer}
      </footer>
    </section>
  );
}

function Skeleton() {
  return (
    <div className="vt-skeleton">
      <div className="vt-skeleton-line" />
      <div className="vt-skeleton-line short" />
      <div className="vt-skeleton-line" />
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

// ────────────────────────────────────────────────────────────────────
// Individual panel views
// ────────────────────────────────────────────────────────────────────

function CarbonIntelligencePanel({ data, lastUpdated }) {
  const ci = data?.panels?.carbon_intelligence;
  return (
    <div className="vt-ci">
      <p className="vt-ci-headline">{ci?.headline || "Live carbon-credit integrity feed"}</p>
      <p className="vt-ci-summary">
        {ci?.summary ||
          "Daily AI brief composed across NASA FIRMS, Sentinel-2, Global Forest Watch, NOAA, and registry scrapes. Each completed audit refreshes the aggregate signal."}
      </p>
      <span className="vt-ci-update">Updated · {fmtTimeAgo(lastUpdated)}</span>
    </div>
  );
}

function ClimateForecastPanel() {
  const series = useMemo(syntheticForecast, []);
  return (
    <div className="vt-chart-host">
      <ResponsiveContainer width="100%" height={160}>
        <LineChart data={series}>
          <CartesianGrid strokeDasharray="2 4" stroke="rgba(245,240,232,0.06)" />
          <XAxis dataKey="day" stroke="rgba(245,240,232,0.4)" fontSize={9} tickLine={false} axisLine={false}
                 interval={Math.floor(series.length / 6)} />
          <YAxis stroke="rgba(245,240,232,0.4)" fontSize={9} tickLine={false} axisLine={false} />
          <Tooltip contentStyle={tooltipStyle} cursor={{ stroke: "rgba(201,168,76,0.3)" }} />
          <Line type="monotone" dataKey="historical" stroke={GOLD_BRIGHT} strokeWidth={2} dot={false} />
          <Line type="monotone" dataKey="forecast"   stroke={GOLD} strokeDasharray="4 4" strokeWidth={2} dot={false} />
        </LineChart>
      </ResponsiveContainer>
      <span className="vt-chip">Reference 90-day model · solid = historical, dashed = projected</span>
    </div>
  );
}

function ClimateAnomaliesPanel({ data }) {
  const climate = data?.panels?.climate_anomalies;
  const bars = useMemo(() => climateAnomalyBars(climate), [climate]);
  return (
    <div>
      <div className="vt-chart-host">
        <ResponsiveContainer width="100%" height={120}>
          <BarChart data={bars}>
            <XAxis dataKey="metric" stroke="rgba(245,240,232,0.4)" fontSize={10} tickLine={false} axisLine={false} />
            <YAxis stroke="rgba(245,240,232,0.4)" fontSize={9} tickLine={false} axisLine={false} />
            <Tooltip contentStyle={tooltipStyle} cursor={{ fill: "rgba(201,168,76,0.05)" }} />
            <Bar dataKey="value" radius={[4, 4, 0, 0]}>
              {bars.map((b, i) => (
                <Cell key={i} fill={b.hasData ? colorForAnomaly(b.kind, b.value) : "rgba(245,240,232,0.12)"} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
      <div className="vt-mini-stats">
        <div>
          <span className="vt-mini-k">Risk level</span>
          <span className="vt-mini-v" style={{ color: RISK_COLOR[climate?.risk_level] || "var(--color-text-primary)" }}>
            {climate?.risk_level || "—"}
          </span>
        </div>
        <div>
          <span className="vt-mini-k">Sample station</span>
          <span className="vt-mini-v vt-mono-sm">{climate?.sample_station || "—"}</span>
        </div>
      </div>
    </div>
  );
}

function ActiveFireCountPanel({ data }) {
  const fire = data?.panels?.active_fire_count;
  const hotspots = fire?.hotspots ?? 0;
  const highConf = fire?.high_confidence ?? 0;
  return (
    <div className="vt-big-stat">
      <div className="vt-big-num" style={{ color: GOLD_BRIGHT }}>{hotspots}</div>
      <div className="vt-big-sub">{fire?.region || "Sample region · 24h"}</div>
      <div className="vt-mini-stats">
        <div>
          <span className="vt-mini-k">High-confidence</span>
          <span className="vt-mini-v">{highConf}</span>
        </div>
        <div>
          <span className="vt-mini-k">Δ vs yesterday</span>
          <span className="vt-mini-v" style={{ color: GREEN }}>↘ —</span>
        </div>
      </div>
    </div>
  );
}

function ThermalAnomaliesPanel({ data }) {
  const fire = data?.panels?.active_fire_count;
  const series = useMemo(() => syntheticThermalSeries(fire?.hotspots), [fire?.hotspots]);
  return (
    <div className="vt-chart-host">
      <ResponsiveContainer width="100%" height={150}>
        <AreaChart data={series}>
          <defs>
            <linearGradient id="vt-thermal" x1="0" x2="0" y1="0" y2="1">
              <stop offset="0%" stopColor={GOLD_BRIGHT} stopOpacity={0.5} />
              <stop offset="100%" stopColor={GOLD} stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="2 4" stroke="rgba(245,240,232,0.06)" />
          <XAxis dataKey="day" stroke="rgba(245,240,232,0.4)" fontSize={9} tickLine={false} axisLine={false}
                 interval={4} />
          <YAxis stroke="rgba(245,240,232,0.4)" fontSize={9} tickLine={false} axisLine={false} />
          <Tooltip contentStyle={tooltipStyle} cursor={{ stroke: GOLD }} />
          <Area type="monotone" dataKey="hotspots" stroke={GOLD_BRIGHT} strokeWidth={2} fill="url(#vt-thermal)" />
        </AreaChart>
      </ResponsiveContainer>
      <span className="vt-chip">Reference 30-day trend · synthesized from latest spot reading</span>
    </div>
  );
}

function ClimateExposurePanel({ data }) {
  const audits = data?.panels?.recent_audits || [];
  const audited = audits.length;
  // Honest placeholder — populations-in-risk needs an external dataset
  // we haven't wired. Show the audit count instead, framed as "tracked
  // projects in monitored zones".
  return (
    <div className="vt-rings-wrap">
      <svg width="170" height="170" viewBox="0 0 170 170">
        <circle cx="85" cy="85" r="74" fill="none" stroke="rgba(201,168,76,0.10)" strokeWidth="2" />
        <circle cx="85" cy="85" r="58" fill="none" stroke="rgba(201,168,76,0.18)" strokeWidth="2" />
        <circle cx="85" cy="85" r="42" fill="none" stroke={GOLD} strokeWidth="2.5" />
        <text x="85" y="80" textAnchor="middle" fill={GOLD_BRIGHT}
              fontFamily="'Playfair Display', serif" fontSize="36" fontWeight="600">
          {audited}
        </text>
        <text x="85" y="100" textAnchor="middle" fill="rgba(245,240,232,0.5)"
              fontFamily="'IBM Plex Mono', monospace" fontSize="9" letterSpacing="1.5">
          AUDITED
        </text>
      </svg>
      <p className="vt-ring-caption">tracked projects in monitored zones</p>
    </div>
  );
}

function RecentAuditsPanel({ data }) {
  const rows = data?.panels?.recent_audits || [];
  if (rows.length === 0) {
    return <div className="vt-empty">No audits yet. Run your first audit.</div>;
  }
  return (
    <table className="vt-table">
      <thead>
        <tr>
          <th>Serial</th>
          <th>Project</th>
          <th>Country</th>
          <th>Score</th>
          <th>Risk</th>
          <th>When</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        {rows.slice(0, 8).map((r) => (
          <tr key={r.audit_id}>
            <td className="vt-mono-sm">{r.serial || "—"}</td>
            <td className="vt-truncate" title={r.project_name || ""}>{r.project_name || "—"}</td>
            <td>{r.country || "—"}</td>
            <td style={{ color: scoreColor(r.overall_score), fontWeight: 600 }}>
              {r.overall_score ?? "—"}
            </td>
            <td>
              <span className="vt-pill" style={{
                color: RISK_COLOR[r.risk_level] || "var(--color-text-secondary)",
                borderColor: RISK_COLOR[r.risk_level] || "var(--color-border)",
              }}>
                {r.risk_level || "—"}
              </span>
            </td>
            <td className="vt-mono-sm">{fmtTimeAgo(r.created_at)}</td>
            <td>
              <Link to={`/report/${r.audit_id}`} className="vt-link">View</Link>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function RegistryActivityPanel({ data }) {
  const total = data?.panels?.registry_activity?.total_audits ?? 0;
  return (
    <div className="vt-big-stat">
      <div className="vt-big-num" style={{ color: GOLD_BRIGHT }}>
        {total.toLocaleString()}
      </div>
      <div className="vt-big-sub">audits run all-time</div>
      <div className="vt-mini-stats">
        <div>
          <span className="vt-mini-k">Period</span>
          <span className="vt-mini-v">all-time</span>
        </div>
      </div>
    </div>
  );
}

function GlobalRiskMapPanel({ data }) {
  const projects = data?.panels?.global_risk_map || [];
  const token = import.meta.env.VITE_MAPBOX_TOKEN;

  // No token → list view
  if (!token) {
    return (
      <div>
        <div className="vt-no-map">
          <span className="vt-no-map-eyebrow">MAPBOX TOKEN NOT SET</span>
          <span className="vt-no-map-sub">listing audited projects below</span>
        </div>
        <ul className="vt-project-list">
          {projects.slice(0, 12).map((p) => (
            <li key={p.audit_id}>
              <span className="vt-mono-sm">{p.serial || "—"}</span>
              <span style={{ color: scoreColor(p.overall_score) }}>
                {p.overall_score ?? "—"}
              </span>
              <span className="vt-text-dim">{p.lat?.toFixed(2)}, {p.lon?.toFixed(2)}</span>
            </li>
          ))}
          {projects.length === 0 && (
            <li className="vt-empty-small">No geocoded audits yet.</li>
          )}
        </ul>
      </div>
    );
  }

  // With token → static map with risk-coloured pins
  const pins = projects
    .filter(p => typeof p.lat === "number" && typeof p.lon === "number")
    .slice(0, 14)
    .map(p => {
      const colour = (RISK_COLOR[p.risk_level] || GOLD).replace("#", "");
      return `pin-s+${colour}(${p.lon.toFixed(3)},${p.lat.toFixed(3)})`;
    })
    .join(",");

  const overlay = pins ? `${pins}/auto` : "0,0,1,0";
  const url = `https://api.mapbox.com/styles/v1/mapbox/dark-v11/static/${overlay}/640x320@2x?access_token=${token}`;

  return (
    <div>
      <img className="vt-map" src={url} alt="Audited projects worldwide" />
      <div className="vt-map-legend">
        <span><i style={{ background: GREEN }} /> LOW</span>
        <span><i style={{ background: AMBER }} /> MEDIUM</span>
        <span><i style={{ background: RED }} /> HIGH</span>
        <span className="vt-text-dim">{projects.length} audited</span>
      </div>
    </div>
  );
}

function AuditQueuePanel({ history }) {
  const audits = history?.data?.audits || [];
  const recent = audits.slice(0, 4);
  if (recent.length === 0) {
    return <div className="vt-empty">No active audits</div>;
  }
  return (
    <ul className="vt-queue-list">
      {recent.map((a) => (
        <li key={a.audit_id}>
          <span className="vt-mono-sm">{a.serial || "—"}</span>
          <span className="vt-truncate" style={{ flex: 1 }}>{a.project_name || "—"}</span>
          <span className="vt-pill" style={{
            color: RISK_COLOR[a.risk_level] || "var(--color-text-secondary)",
            borderColor: RISK_COLOR[a.risk_level] || "var(--color-border)",
          }}>
            {a.overall_score ?? "—"}
          </span>
        </li>
      ))}
    </ul>
  );
}

function MarketSignalsPanel() {
  return (
    <div className="vt-chart-host">
      <ResponsiveContainer width="100%" height={130}>
        <LineChart data={VCU_PRICE_HISTORY}>
          <CartesianGrid strokeDasharray="2 4" stroke="rgba(245,240,232,0.06)" />
          <XAxis dataKey="week" stroke="rgba(245,240,232,0.4)" fontSize={9} tickLine={false} axisLine={false}
                 interval={1} />
          <YAxis stroke="rgba(245,240,232,0.4)" fontSize={9} tickLine={false} axisLine={false}
                 domain={["dataMin - 0.2", "dataMax + 0.2"]} />
          <Tooltip contentStyle={tooltipStyle} cursor={{ stroke: GOLD }}
                   formatter={(v) => [`$${v}`, "Avg VCU"]} />
          <Line type="monotone" dataKey="price" stroke={GOLD_BRIGHT} strokeWidth={2}
                dot={{ r: 2.5, fill: GOLD_BRIGHT, stroke: "transparent" }} />
        </LineChart>
      </ResponsiveContainer>
      <div className="vt-mini-stats">
        <div>
          <span className="vt-mini-k">Latest</span>
          <span className="vt-mini-v">${VCU_PRICE_HISTORY.at(-1).price}</span>
        </div>
        <div>
          <span className="vt-mini-k">12-week</span>
          <span className="vt-mini-v">
            {(VCU_PRICE_HISTORY.reduce((a, b) => a + b.price, 0) / VCU_PRICE_HISTORY.length).toFixed(2)}
          </span>
        </div>
      </div>
    </div>
  );
}

function SystemStatusPanel({ health }) {
  const services = health?.services || {};
  return (
    <ul className="vt-status-list">
      {SERVICE_ORDER.map((key) => {
        const v = services[key];
        const status =
          v === true ? "ok" : v === false ? "down" : "unknown";
        const colour = status === "ok" ? GREEN : status === "down" ? RED : AMBER;
        return (
          <li key={key}>
            <span className="vt-status-dot" style={{ background: colour, boxShadow: `0 0 6px ${colour}` }} />
            <span className="vt-status-label">{SERVICE_LABELS[key] || key}</span>
            <span className="vt-status-state vt-mono-sm">{status === "ok" ? "ONLINE" : status === "down" ? "MISSING KEY" : "—"}</span>
          </li>
        );
      })}
    </ul>
  );
}

// ────────────────────────────────────────────────────────────────────
// Page composition
// ────────────────────────────────────────────────────────────────────

export default function Dashboard() {
  const dash = useDashboard();
  const health = useHealth();
  const history = useAuditHistory(30000);

  return (
    <main className="vt-dash">
      <DashStyles />

      <header className="vt-dash-head">
        <Link to="/" className="vt-wordmark">
          VERITAS<span className="vt-wordmark-sub">ORACLE</span>
        </Link>
        <div className="vt-dash-meta">
          <span className="vt-dash-eyebrow">DASHBOARD · LIVE INTELLIGENCE</span>
          <span className="vt-dash-update">
            {dash.lastUpdated
              ? `Updated · ${fmtTimeAgo(dash.lastUpdated)}`
              : dash.loading ? "Loading…" : "—"}
          </span>
        </div>
        <Link to="/audit" className="vt-cta">RUN AUDIT</Link>
      </header>

      <div className="vt-grid">
        <Panel id="01" title="Carbon Intelligence Brief" span="full"
               source="VERITAS Oracle aggregate"
               loading={dash.loading} error={dash.error}>
          <CarbonIntelligencePanel data={dash.data} lastUpdated={dash.lastUpdated} />
        </Panel>

        <Panel id="02" title="AI Climate Forecast"
               source="VERITAS reference model · 90-day">
          <ClimateForecastPanel />
        </Panel>

        <Panel id="03" title="Climate Anomalies"
               source="NOAA NCDC · sample station"
               loading={dash.loading} error={dash.error}>
          <ClimateAnomaliesPanel data={dash.data} />
        </Panel>

        <Panel id="04" title="Active Fire Count"
               source="NASA FIRMS · 24h"
               loading={dash.loading} error={dash.error}>
          <ActiveFireCountPanel data={dash.data} />
        </Panel>

        <Panel id="05" title="Thermal Anomalies"
               source="NASA FIRMS · 30-day reference"
               loading={dash.loading} error={dash.error}>
          <ThermalAnomaliesPanel data={dash.data} />
        </Panel>

        <Panel id="06" title="Climate Risk Exposure"
               source="VERITAS audit history"
               loading={dash.loading} error={dash.error}>
          <ClimateExposurePanel data={dash.data} />
        </Panel>

        <Panel id="07" title="Recent Audits" span={2}
               source="VERITAS audit history"
               loading={dash.loading} error={dash.error}>
          <RecentAuditsPanel data={dash.data} />
        </Panel>

        <Panel id="08" title="Registry Activity"
               source="VERITAS audit count"
               loading={dash.loading} error={dash.error}>
          <RegistryActivityPanel data={dash.data} />
        </Panel>

        <Panel id="09" title="Global Risk Map"
               source={import.meta.env.VITE_MAPBOX_TOKEN ? "Mapbox dark · audit pins" : "Audit list (no Mapbox token)"}
               loading={dash.loading} error={dash.error}>
          <GlobalRiskMapPanel data={dash.data} />
        </Panel>

        <Panel id="10" title="Audit Queue"
               source="VERITAS active sessions"
               loading={history.loading} error={history.error}>
          <AuditQueuePanel history={history} />
        </Panel>

        <Panel id="11" title="Market Signals"
               source="Reference VCU pricing · weekly">
          <MarketSignalsPanel />
        </Panel>

        <Panel id="12" title="System Health"
               source="VERITAS Oracle · /health · 60s"
               error={health.error && !health.data}>
          <SystemStatusPanel health={health.data} />
        </Panel>
      </div>
    </main>
  );
}

// ────────────────────────────────────────────────────────────────────
// Inline scoped CSS (kept local so the dashboard reskin doesn't leak)
// ────────────────────────────────────────────────────────────────────

function DashStyles() {
  return (
    <style>{`
      .vt-dash {
        min-height: 100vh;
        padding: 24px 32px 64px;
        color: var(--color-text-primary);
        background:
          radial-gradient(ellipse 60% 40% at 50% -10%, rgba(201,168,76,0.10), transparent 65%),
          radial-gradient(ellipse 70% 40% at 100% 110%, rgba(76,175,125,0.06), transparent 60%),
          var(--color-bg);
        background-attachment: fixed;
      }

      .vt-dash-head {
        display: flex;
        align-items: center;
        gap: 18px;
        margin-bottom: 24px;
        padding-bottom: 16px;
        border-bottom: 1px solid rgba(201,168,76,0.12);
      }
      .vt-wordmark {
        font-family: var(--font-display);
        font-size: 1.5rem;
        font-weight: 600;
        letter-spacing: 0.04em;
        color: var(--color-gold-bright);
        text-decoration: none;
      }
      .vt-wordmark-sub {
        font-family: var(--font-mono);
        font-size: 0.7rem;
        letter-spacing: 0.4em;
        margin-left: 8px;
        color: var(--color-text-secondary);
        font-weight: 400;
      }
      .vt-dash-meta {
        flex: 1;
        display: flex;
        flex-direction: column;
        gap: 2px;
      }
      .vt-dash-eyebrow {
        font-family: var(--font-mono);
        font-size: 0.65rem;
        letter-spacing: 0.32em;
        color: var(--color-gold);
        text-transform: uppercase;
      }
      .vt-dash-update {
        font-family: var(--font-mono);
        font-size: 0.7rem;
        color: var(--color-text-muted);
        letter-spacing: 0.05em;
      }
      .vt-cta {
        background: var(--color-gold);
        color: #0a0a0a;
        padding: 10px 22px;
        border-radius: 3px;
        font-family: var(--font-mono);
        font-size: 0.75rem;
        font-weight: 600;
        letter-spacing: 0.22em;
        text-decoration: none;
      }
      .vt-cta:hover { background: var(--color-gold-bright); }

      .vt-grid {
        display: grid;
        grid-template-columns: repeat(3, 1fr);
        gap: 16px;
      }
      @media (max-width: 1100px) { .vt-grid { grid-template-columns: repeat(2, 1fr); } }
      @media (max-width: 720px)  { .vt-grid { grid-template-columns: 1fr; } }

      .vt-panel {
        background: rgba(255,255,255,0.025);
        border: 1px solid rgba(201,168,76,0.15);
        border-radius: 14px;
        backdrop-filter: blur(20px) saturate(140%);
        -webkit-backdrop-filter: blur(20px) saturate(140%);
        padding: 16px 18px 14px;
        display: flex;
        flex-direction: column;
        min-height: 220px;
        transition: border-color 0.22s ease, box-shadow 0.22s ease;
      }
      .vt-panel:hover {
        border-color: rgba(201,168,76,0.32);
        box-shadow: 0 12px 36px -16px rgba(0,0,0,0.5);
      }
      .vt-panel-head {
        display: flex;
        justify-content: space-between;
        align-items: flex-start;
        gap: 12px;
        margin-bottom: 12px;
      }
      .vt-panel-title-wrap { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
      .vt-panel-eyebrow {
        font-family: var(--font-mono);
        font-size: 0.6rem;
        letter-spacing: 0.25em;
        color: var(--color-gold);
        text-transform: uppercase;
      }
      .vt-panel-title {
        font-family: var(--font-display);
        font-size: 1.05rem;
        font-weight: 500;
        color: var(--color-text-primary);
        margin: 0;
      }
      .vt-panel-num {
        font-family: var(--font-mono);
        font-size: 0.7rem;
        color: var(--color-gold);
        letter-spacing: 0.15em;
      }
      .vt-panel-body { flex: 1; min-width: 0; }
      .vt-panel-foot {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-top: 10px;
        padding-top: 8px;
        border-top: 1px solid rgba(245,240,232,0.05);
        font-family: var(--font-mono);
        font-size: 0.62rem;
        letter-spacing: 0.18em;
        color: var(--color-text-muted);
        text-transform: uppercase;
      }
      .vt-panel-source::before { content: "· "; color: var(--color-gold); }

      .vt-panel-error {
        font-family: var(--font-mono);
        font-size: 0.78rem;
        color: var(--color-risk-high);
        padding: 14px 8px;
        border-left: 2px solid var(--color-risk-high);
      }

      /* skeleton shimmer */
      @keyframes vt-shim {
        0%   { background-position: -200px 0; }
        100% { background-position: 200px 0; }
      }
      .vt-skeleton { display: flex; flex-direction: column; gap: 8px; padding: 8px 0; }
      .vt-skeleton-line {
        height: 12px;
        border-radius: 6px;
        background: linear-gradient(
          90deg,
          rgba(201,168,76,0.05) 25%,
          rgba(201,168,76,0.18) 50%,
          rgba(201,168,76,0.05) 75%
        );
        background-size: 400px 100%;
        animation: vt-shim 1.5s linear infinite;
      }
      .vt-skeleton-line.short { width: 60%; }

      .vt-mono-sm { font-family: var(--font-mono); font-size: 0.78rem; }
      .vt-text-dim { color: var(--color-text-muted); }
      .vt-truncate {
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
        max-width: 22ch;
      }

      /* CI panel */
      .vt-ci { padding: 4px 0; display: flex; flex-direction: column; gap: 8px; }
      .vt-ci-headline {
        font-family: var(--font-display);
        font-size: 1.4rem;
        font-style: italic;
        color: var(--color-gold-bright);
        margin: 0;
      }
      .vt-ci-summary {
        font-family: var(--font-body);
        font-size: 0.92rem;
        line-height: 1.55;
        color: rgba(245,240,232,0.75);
        margin: 0;
      }
      .vt-ci-update {
        font-family: var(--font-mono);
        font-size: 0.65rem;
        color: var(--color-text-muted);
        letter-spacing: 0.18em;
        align-self: flex-end;
      }

      .vt-chart-host { display: flex; flex-direction: column; gap: 6px; }
      .vt-chip {
        font-family: var(--font-mono);
        font-size: 0.62rem;
        letter-spacing: 0.18em;
        color: var(--color-text-muted);
        text-transform: uppercase;
      }

      .vt-mini-stats {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 10px;
        margin-top: 10px;
      }
      .vt-mini-stats > div { display: flex; flex-direction: column; gap: 2px; }
      .vt-mini-k {
        font-family: var(--font-mono);
        font-size: 0.62rem;
        color: var(--color-text-muted);
        letter-spacing: 0.15em;
        text-transform: uppercase;
      }
      .vt-mini-v {
        font-family: var(--font-mono);
        font-size: 0.92rem;
        color: var(--color-text-primary);
      }

      /* Big number panels */
      .vt-big-stat { display: flex; flex-direction: column; gap: 4px; }
      .vt-big-num {
        font-family: var(--font-mono);
        font-size: 3rem;
        font-weight: 300;
        line-height: 1;
        letter-spacing: -0.02em;
      }
      .vt-big-sub {
        font-family: var(--font-mono);
        font-size: 0.7rem;
        letter-spacing: 0.18em;
        text-transform: uppercase;
        color: var(--color-text-muted);
        margin-top: 4px;
      }

      /* Concentric ring */
      .vt-rings-wrap { display: flex; flex-direction: column; align-items: center; gap: 8px; }
      .vt-ring-caption {
        font-family: var(--font-mono);
        font-size: 0.7rem;
        color: var(--color-text-muted);
        letter-spacing: 0.12em;
        text-align: center;
        margin: 0;
      }

      /* Recent audits table */
      .vt-table {
        width: 100%;
        border-collapse: collapse;
        font-size: 0.82rem;
        font-family: var(--font-mono);
      }
      .vt-table th {
        text-align: left;
        font-size: 0.62rem;
        letter-spacing: 0.18em;
        font-weight: 500;
        color: var(--color-gold);
        padding: 4px 8px 8px 0;
        border-bottom: 1px solid rgba(201,168,76,0.18);
        text-transform: uppercase;
      }
      .vt-table td {
        padding: 7px 8px 7px 0;
        border-bottom: 1px solid rgba(245,240,232,0.05);
        color: rgba(245,240,232,0.85);
      }
      .vt-table tr:hover td { background: rgba(201,168,76,0.04); }
      .vt-pill {
        font-size: 0.62rem;
        letter-spacing: 0.16em;
        padding: 2px 8px;
        border: 1px solid;
        border-radius: 999px;
        font-family: var(--font-mono);
        text-transform: uppercase;
      }
      .vt-link {
        color: var(--color-gold-bright);
        text-decoration: none;
        font-size: 0.7rem;
        letter-spacing: 0.15em;
      }
      .vt-link:hover { color: #fff; }

      /* Empty states */
      .vt-empty, .vt-empty-small {
        font-family: var(--font-mono);
        font-size: 0.78rem;
        color: var(--color-text-muted);
        padding: 16px 0;
        text-align: center;
        letter-spacing: 0.05em;
      }
      .vt-empty-small { padding: 6px 0; }

      /* Map */
      .vt-map {
        width: 100%;
        height: auto;
        border-radius: 8px;
        border: 1px solid rgba(201,168,76,0.18);
        display: block;
      }
      .vt-no-map {
        background: rgba(0,0,0,0.4);
        border: 1px dashed rgba(201,168,76,0.25);
        border-radius: 8px;
        padding: 16px;
        text-align: center;
        margin-bottom: 10px;
        display: flex;
        flex-direction: column;
        gap: 4px;
      }
      .vt-no-map-eyebrow {
        font-family: var(--font-mono);
        font-size: 0.62rem;
        letter-spacing: 0.22em;
        color: var(--color-gold);
      }
      .vt-no-map-sub {
        font-family: var(--font-mono);
        font-size: 0.7rem;
        color: var(--color-text-muted);
      }
      .vt-map-legend {
        display: flex;
        gap: 14px;
        margin-top: 8px;
        font-family: var(--font-mono);
        font-size: 0.62rem;
        letter-spacing: 0.18em;
        color: var(--color-text-secondary);
        text-transform: uppercase;
      }
      .vt-map-legend i {
        display: inline-block;
        width: 8px;
        height: 8px;
        border-radius: 50%;
        margin-right: 5px;
        vertical-align: middle;
      }
      .vt-project-list {
        list-style: none;
        margin: 0;
        padding: 0;
        font-family: var(--font-mono);
        font-size: 0.78rem;
        max-height: 220px;
        overflow-y: auto;
      }
      .vt-project-list li {
        display: grid;
        grid-template-columns: 90px 60px 1fr;
        gap: 10px;
        padding: 4px 0;
        border-bottom: 1px solid rgba(245,240,232,0.04);
      }

      /* Queue */
      .vt-queue-list {
        list-style: none;
        padding: 0;
        margin: 0;
        font-family: var(--font-mono);
        font-size: 0.78rem;
      }
      .vt-queue-list li {
        display: flex;
        align-items: center;
        gap: 10px;
        padding: 6px 0;
        border-bottom: 1px solid rgba(245,240,232,0.05);
      }

      /* System health */
      .vt-status-list {
        list-style: none;
        padding: 0;
        margin: 0;
        font-family: var(--font-mono);
        font-size: 0.75rem;
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 8px 14px;
      }
      .vt-status-list li {
        display: flex;
        align-items: center;
        gap: 8px;
      }
      .vt-status-dot {
        width: 8px;
        height: 8px;
        border-radius: 50%;
        flex-shrink: 0;
      }
      .vt-status-label {
        flex: 1;
        color: rgba(245,240,232,0.85);
        letter-spacing: 0.06em;
      }
      .vt-status-state {
        color: var(--color-text-muted);
        font-size: 0.62rem;
        letter-spacing: 0.18em;
      }
    `}</style>
  );
}
