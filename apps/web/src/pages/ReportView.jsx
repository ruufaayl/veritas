import { useParams } from "react-router-dom";

export default function ReportView() {
  const { auditId } = useParams();
  return (
    <main style={{ padding: "2rem" }}>
      <h1>Report</h1>
      <p style={{ fontFamily: "var(--font-mono)", color: "var(--color-text-secondary)" }}>
        {auditId}
      </p>
    </main>
  );
}
