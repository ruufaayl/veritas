/**
 * ScoreRing — animated SVG circular score (0-100).
 *
 * Score animates from 0 → score over 1.2s on mount (or on score change
 * if `animating`). Stroke colour swings across green/amber/red bands.
 */
import { useEffect, useState } from "react";

function colorForScore(score) {
  if (score == null) return "var(--color-text-muted)";
  if (score >= 70) return "var(--color-risk-low)";
  if (score >= 40) return "var(--color-risk-medium)";
  return "var(--color-risk-high)";
}

export default function ScoreRing({ score = 0, size = 200, animating = true }) {
  const [displayScore, setDisplayScore] = useState(animating ? 0 : score);

  useEffect(() => {
    if (!animating) {
      setDisplayScore(score);
      return undefined;
    }
    const start = performance.now();
    const duration = 1200;
    let frame;
    const step = (now) => {
      const t = Math.min(1, (now - start) / duration);
      const eased = 1 - Math.pow(1 - t, 3); // ease-out cubic
      setDisplayScore(Math.round(score * eased));
      if (t < 1) frame = requestAnimationFrame(step);
    };
    frame = requestAnimationFrame(step);
    return () => cancelAnimationFrame(frame);
  }, [score, animating]);

  const stroke = 6;
  const radius = size / 2 - stroke - 6;
  const circumference = 2 * Math.PI * radius;
  const dash = circumference * (Math.max(0, Math.min(100, displayScore)) / 100);
  const color = colorForScore(score);

  return (
    <svg width={size} height={size} role="img" aria-label={`Risk score ${score} of 100`}>
      <circle
        cx={size / 2}
        cy={size / 2}
        r={radius}
        fill="none"
        stroke="rgba(245,240,232,0.08)"
        strokeWidth={stroke}
      />
      <circle
        cx={size / 2}
        cy={size / 2}
        r={radius}
        fill="none"
        stroke={color}
        strokeWidth={stroke}
        strokeLinecap="round"
        strokeDasharray={`${dash} ${circumference - dash}`}
        // Start the arc at 12-o'clock instead of 3-o'clock
        transform={`rotate(-90 ${size / 2} ${size / 2})`}
        style={{ transition: "stroke 0.3s" }}
      />
      <text
        x="50%"
        y="50%"
        textAnchor="middle"
        dominantBaseline="central"
        fill="var(--color-text-primary)"
        style={{
          fontFamily: "var(--font-display)",
          fontSize: size * 0.32,
          fontWeight: 600,
        }}
      >
        {displayScore}
      </text>
      <text
        x="50%"
        y={size / 2 + size * 0.2}
        textAnchor="middle"
        fill="var(--color-text-muted)"
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: size * 0.07,
          letterSpacing: "0.18em",
        }}
      >
        / 100
      </text>
    </svg>
  );
}
