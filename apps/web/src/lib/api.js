import axios from "axios";

const baseURL = import.meta.env.VITE_API_URL || "http://localhost:8000";

export const api = axios.create({
  baseURL,
  headers: { "Content-Type": "application/json" },
});

/** Returns the configured API base URL.
 *  EventSource doesn't go through axios, so the AuditFlow hook needs the
 *  raw string to build `${base}/audit/{id}/stream`. Reading
 *  import.meta.env.VITE_API_URL twice (once here, once in the hook) is
 *  fine, but we centralize it so the hook can't accidentally hardcode
 *  localhost — that would break the Vercel build. */
export function getApiBaseUrl() {
  return baseURL.replace(/\/$/, "");
}

export async function startAudit(serial) {
  const { data } = await api.post("/audit/start", { serial });
  return data; // { audit_id, message }
}

export async function getAuditStatus(auditId) {
  const { data } = await api.get(`/audit/${auditId}`);
  return data;
}

export async function getAuditHistory() {
  const { data } = await api.get(`/audit/history`);
  return data;
}

export async function getDashboardData() {
  const { data } = await api.get("/dashboard");
  return data;
}

export async function getHealth() {
  const { data } = await api.get("/health");
  return data;
}

export async function getAuditById(auditId) {
  const { data } = await api.get(`/audit/${auditId}`);
  return data;
}

export function reportPdfUrl(auditId) {
  return `${getApiBaseUrl()}/report/${auditId}`;
}

export function reportHtmlUrl(auditId) {
  return `${getApiBaseUrl()}/report/${auditId}.html`;
}
