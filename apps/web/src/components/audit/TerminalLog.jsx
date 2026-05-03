/**
 * TerminalLog — monospace event log with auto-scroll + animated lines.
 *
 * Each entry is { id, timestamp, message, type }. Type drives the colour:
 *   info    — secondary text
 *   step    — gold (header line for "► STEP n/8")
 *   success — risk-low green
 *   error   — risk-high red
 *
 * `active` toggles a blinking caret at the end of the last line.
 */
import { useEffect, useRef } from "react";
import { AnimatePresence, motion } from "framer-motion";

const TYPE_COLOR = {
  info: "var(--color-text-secondary)",
  step: "var(--color-gold-bright)",
  success: "var(--color-risk-low)",
  error: "var(--color-risk-high)",
};

export default function TerminalLog({ logs = [], active = false, height = "100%" }) {
  const scrollRef = useRef(null);

  useEffect(() => {
    const node = scrollRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [logs.length]);

  return (
    <div
      ref={scrollRef}
      style={{
        background: "#0A0A0A",
        border: "1px solid var(--color-border)",
        borderRadius: "3px",
        padding: "12px 14px",
        fontFamily: "var(--font-mono)",
        fontSize: "11.5px",
        lineHeight: 1.65,
        height,
        overflowY: "auto",
        color: "var(--color-text-secondary)",
      }}
    >
      {logs.length === 0 && (
        <div style={{ color: "var(--color-text-muted)" }}>
          // Awaiting audit. Enter a serial above and press RUN AUDIT.
        </div>
      )}

      <AnimatePresence initial={false}>
        {logs.map((log) => (
          <motion.div
            key={log.id}
            initial={{ opacity: 0, x: -6 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ duration: 0.18, ease: [0.2, 0.8, 0.2, 1] }}
            style={{
              color: TYPE_COLOR[log.type] || TYPE_COLOR.info,
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
            }}
          >
            <span style={{ color: "var(--color-text-muted)" }}>{log.timestamp}</span>{" "}
            {log.message}
          </motion.div>
        ))}
      </AnimatePresence>

      {active && (
        <span
          aria-hidden
          style={{
            display: "inline-block",
            width: "8px",
            height: "1em",
            background: "var(--color-gold)",
            verticalAlign: "text-bottom",
            marginLeft: "2px",
            animation: "veritas-cursor 1s steps(2) infinite",
          }}
        />
      )}

      <style>{`
        @keyframes veritas-cursor {
          0%, 49% { opacity: 1; }
          50%, 100% { opacity: 0; }
        }
      `}</style>
    </div>
  );
}
