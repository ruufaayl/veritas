import axios from "axios";

const baseURL = import.meta.env.VITE_API_URL || "http://localhost:8000";

export const api = axios.create({
  baseURL,
  headers: { "Content-Type": "application/json" },
});

export async function runAudit(serial) {
  const { data } = await api.post("/audit", { serial });
  return data;
}

export async function getAuditStatus(auditId) {
  const { data } = await api.get(`/audit/${auditId}`);
  return data;
}

export async function getDashboardData() {
  const { data } = await api.get("/dashboard");
  return data;
}

export async function downloadReport(auditId) {
  const response = await api.get(`/report/${auditId}`, { responseType: "blob" });
  return response.data;
}
