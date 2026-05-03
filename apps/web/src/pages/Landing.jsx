import { Link } from "react-router-dom";

/**
 * Landing — preserved from the existing veritasoracle.vercel.app page.
 * The heading + subheading are exact; only the two CTA buttons are
 * wired up here, routing into the new SPA at /audit and /dashboard.
 */
export default function Landing() {
  return (
    <main style={{ padding: "4rem 2rem", maxWidth: "960px", margin: "0 auto" }}>
      <h1 style={{ fontSize: "3rem", color: "var(--color-gold-bright)" }}>
        VERITAS Oracle
      </h1>
      <p style={{ color: "var(--color-text-secondary)", fontSize: "1.125rem" }}>
        Carbon credit fraud risk scoring.
      </p>

      <div
        style={{
          display: "flex",
          gap: "12px",
          marginTop: "2.5rem",
          flexWrap: "wrap",
        }}
      >
        <Link
          to="/audit"
          style={{
            background: "var(--color-gold)",
            color: "#0a0a0a",
            padding: "12px 26px",
            fontFamily: "var(--font-mono)",
            letterSpacing: "0.22em",
            fontSize: "0.82rem",
            fontWeight: 600,
            borderRadius: "3px",
            textDecoration: "none",
          }}
        >
          RUN AN AUDIT
        </Link>
        <Link
          to="/dashboard"
          style={{
            background: "transparent",
            color: "var(--color-gold-bright)",
            border: "1px solid var(--color-gold-dim)",
            padding: "11px 26px",
            fontFamily: "var(--font-mono)",
            letterSpacing: "0.22em",
            fontSize: "0.82rem",
            fontWeight: 600,
            borderRadius: "3px",
            textDecoration: "none",
          }}
        >
          VIEW DASHBOARD
        </Link>
      </div>
    </main>
  );
}
