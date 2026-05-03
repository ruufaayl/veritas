/**
 * DataCard — framer-motion wrapper for a right-panel result card.
 *
 * Slides in from the right with a spring when `visible` flips to true.
 * Shows a STEP n badge and an optional "⚠ APPROXIMATE REGIONAL DATA"
 * tag (set when the registry didn't supply project-level coordinates
 * and we fell back to the country centroid).
 */
import { AnimatePresence, motion } from "framer-motion";

export default function DataCard({
  title,
  step,
  children,
  visible = false,
  approximate = false,
  accent = "var(--color-gold)",
}) {
  return (
    <AnimatePresence initial={true}>
      {visible && (
        <motion.div
          initial={{ opacity: 0, x: 28, scale: 0.985 }}
          animate={{ opacity: 1, x: 0, scale: 1 }}
          exit={{ opacity: 0, x: 28 }}
          transition={{ type: "spring", stiffness: 220, damping: 26 }}
          style={{
            background: "var(--color-bg-panel)",
            border: "1px solid var(--color-border)",
            borderTop: `2px solid ${accent}`,
            borderRadius: "3px",
            padding: "20px 22px",
            marginBottom: "16px",
          }}
        >
          <header
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              gap: "12px",
              marginBottom: "14px",
              flexWrap: "wrap",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
              {step != null && (
                <span
                  style={{
                    fontFamily: "var(--font-mono)",
                    fontSize: "10px",
                    letterSpacing: "0.22em",
                    color: "var(--color-gold)",
                    border: "1px solid var(--color-gold-dim)",
                    padding: "2px 6px",
                    borderRadius: "2px",
                  }}
                >
                  STEP {step}
                </span>
              )}
              <h3
                style={{
                  fontFamily: "var(--font-display)",
                  fontSize: "1.15rem",
                  margin: 0,
                  color: "var(--color-text-primary)",
                  fontWeight: 500,
                }}
              >
                {title}
              </h3>
            </div>
            {approximate && (
              <span
                title="The registry did not provide project-level coordinates. Geo-services were run against the country centroid (Nominatim); figures here are regional, not project-specific."
                style={{
                  fontFamily: "var(--font-mono)",
                  fontSize: "9.5px",
                  letterSpacing: "0.18em",
                  color: "var(--color-risk-medium)",
                  background: "rgba(232,164,74,0.08)",
                  border: "1px solid rgba(232,164,74,0.35)",
                  padding: "3px 7px",
                  borderRadius: "2px",
                  textTransform: "uppercase",
                  whiteSpace: "nowrap",
                }}
              >
                ⚠ Approximate Regional Data
              </span>
            )}
          </header>
          <div>{children}</div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
