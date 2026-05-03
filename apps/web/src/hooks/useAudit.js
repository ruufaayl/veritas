/**
 * useAudit — single hook driving the entire audit experience.
 *
 * Lifecycle:
 *   1. caller sets `serial` then calls `startAudit()`
 *   2. hook POSTs /audit/start, opens an EventSource on /audit/{id}/stream
 *   3. each SSE event is appended as a log line and routed into the
 *      matching data slice (projectData, fireData, ...)
 *   4. on the `final` event we keep the connection open just long enough
 *      to read the assembled result, then close it
 *   5. status transitions: idle → starting → running → complete | error
 *
 * EventSource URL: built from VITE_API_URL via getApiBaseUrl(). Never
 * hardcoded — the deployed Vercel build relies on the env var.
 */
import { useCallback, useRef, useState } from "react";

import { getApiBaseUrl, startAudit as startAuditRequest, reportPdfUrl } from "../lib/api.js";

const TOTAL_STEPS = 8;

function nowStamp() {
  const d = new Date();
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  const ss = String(d.getSeconds()).padStart(2, "0");
  return `[${hh}:${mm}:${ss}]`;
}

let _logCounter = 0;
function logId() {
  _logCounter += 1;
  return `${Date.now()}-${_logCounter}`;
}

function makeLog(message, type = "info") {
  return { id: logId(), timestamp: nowStamp(), message, type };
}

export function useAudit() {
  const [serial, setSerial] = useState("");
  const [status, setStatus] = useState("idle"); // idle|starting|running|complete|error
  const [currentStep, setCurrentStep] = useState(0);
  const [progressPct, setProgressPct] = useState(0);
  const [logs, setLogs] = useState([]);
  const [auditId, setAuditId] = useState(null);

  const [projectData, setProjectData] = useState(null);
  const [coords, setCoords] = useState(null); // { lat, lon, approximate, country }
  const [fireData, setFireData] = useState(null);
  const [satelliteImage, setSatelliteImage] = useState(null);
  const [vegetationData, setVegetationData] = useState(null);
  const [forestData, setForestData] = useState(null);
  const [climateData, setClimateData] = useState(null);
  const [riskScore, setRiskScore] = useState(null);
  const [narrative, setNarrative] = useState(null);
  const [errorMsg, setErrorMsg] = useState(null);

  const eventSourceRef = useRef(null);

  const appendLog = useCallback((message, type = "info") => {
    setLogs((prev) => [...prev, makeLog(message, type)]);
  }, []);

  const closeStream = useCallback(() => {
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }
  }, []);

  const resetAudit = useCallback(() => {
    closeStream();
    setStatus("idle");
    setCurrentStep(0);
    setProgressPct(0);
    setLogs([]);
    setAuditId(null);
    setProjectData(null);
    setCoords(null);
    setFireData(null);
    setSatelliteImage(null);
    setVegetationData(null);
    setForestData(null);
    setClimateData(null);
    setRiskScore(null);
    setNarrative(null);
    setErrorMsg(null);
  }, [closeStream]);

  /** Process one SSE event payload (already JSON-parsed). */
  const processEvent = useCallback((event) => {
    const {
      step,
      status: eventStatus,
      step_name: stepName,
      message,
      data,
      progress_pct: pPct,
      final,
      result,
      error,
    } = event;

    // Logs ────────────────────────────────────────────────────────────
    if (eventStatus === "running" && step >= 1) {
      setLogs((prev) => [
        ...prev,
        makeLog(`► STEP ${step}/${TOTAL_STEPS} — ${stepName}`, "step"),
        ...(message ? [makeLog(`  ${message}`, "info")] : []),
      ]);
    } else if (eventStatus === "complete") {
      setLogs((prev) => [...prev, makeLog(`✓ ${message || stepName}`, "success")]);
    } else if (eventStatus === "error") {
      setLogs((prev) => [...prev, makeLog(`✗ ${message || error || "error"}`, "error")]);
    }

    if (typeof step === "number") setCurrentStep(step);
    if (typeof pPct === "number") setProgressPct(pPct);

    // Per-step data captures (on `complete`) ──────────────────────────
    if (eventStatus === "complete" && data) {
      switch (step) {
        case 1:
          setProjectData((prev) => ({ ...(prev || {}), ...data }));
          break;
        case 2:
          if (typeof data.lat === "number" && typeof data.lon === "number") {
            setCoords({
              lat: data.lat,
              lon: data.lon,
              approximate: !!data.approximate,
              country: data.country,
            });
          }
          break;
        case 3:
          setFireData(data);
          break;
        case 5:
          setVegetationData(data);
          break;
        case 6:
          setForestData(data);
          break;
        case 7:
          setRiskScore(data);
          break;
        default:
          break;
      }
    }

    // Final event includes the full assembled result ──────────────────
    if (final && result) {
      if (result.project) {
        setProjectData((prev) => ({ ...(prev || {}), ...result.project }));
        const { lat, lon, country, coordinates_approximate } = result.project;
        if (typeof lat === "number" && typeof lon === "number") {
          setCoords({
            lat, lon, country,
            approximate: !!coordinates_approximate,
          });
        }
      }
      if (result.fire_data) setFireData(result.fire_data);
      if (result.vegetation_data) setVegetationData(result.vegetation_data);
      if (result.forest_data) setForestData(result.forest_data);
      if (result.climate_data) setClimateData(result.climate_data);
      if (result.risk_score) setRiskScore(result.risk_score);
      if (result.narrative) setNarrative(result.narrative);
      if (result.satellite_image_b64) setSatelliteImage(result.satellite_image_b64);
    }

    // Terminal state
    if (final) {
      if (eventStatus === "error") {
        setErrorMsg(error || message || "Audit failed");
        setStatus("error");
      } else {
        setStatus("complete");
        setProgressPct(100);
      }
      // Close after a microtask so React commits the final state first.
      Promise.resolve().then(closeStream);
    }
  }, [closeStream]);

  const startAudit = useCallback(async () => {
    const trimmed = serial.trim();
    if (!trimmed) return;
    resetAudit();
    setStatus("starting");
    setLogs([
      makeLog("VERITAS ORACLE v1.0 initialized", "info"),
      makeLog("Connecting to registry network...", "info"),
    ]);

    let id;
    try {
      const resp = await startAuditRequest(trimmed);
      id = resp.audit_id;
      setAuditId(id);
      appendLog(`Audit ${id.slice(0, 8)} accepted by orchestrator`, "info");
    } catch (err) {
      const detail =
        err?.response?.data?.detail || err?.message || "Unknown error";
      appendLog(`✗ Failed to start audit: ${detail}`, "error");
      setStatus("error");
      setErrorMsg(detail);
      return;
    }

    setStatus("running");
    const url = `${getApiBaseUrl()}/audit/${id}/stream`;
    const es = new EventSource(url);
    eventSourceRef.current = es;

    es.onmessage = (msgEvent) => {
      try {
        const data = JSON.parse(msgEvent.data);
        processEvent(data);
      } catch (parseErr) {
        appendLog(`Stream parse error: ${parseErr.message}`, "error");
      }
    };

    // Server emits a named `end` event before closing — clean shutdown.
    es.addEventListener("end", () => {
      closeStream();
      setStatus((prev) => (prev === "running" ? "complete" : prev));
    });

    es.onerror = () => {
      // EventSource fires `error` on natural disconnect too. If we already
      // hit a `final` event, the status will be complete/error — leave it.
      // Otherwise, the connection actually dropped.
      setStatus((prev) => {
        if (prev === "complete" || prev === "error") return prev;
        appendLog("✗ Stream connection lost", "error");
        return "error";
      });
      closeStream();
    };
  }, [serial, resetAudit, appendLog, closeStream, processEvent]);

  const downloadReport = useCallback(() => {
    if (!auditId) return;
    // Open in a new tab — backend returns Content-Disposition: attachment,
    // so the browser will trigger a save dialog.
    window.open(reportPdfUrl(auditId), "_blank", "noopener,noreferrer");
  }, [auditId]);

  return {
    // input
    serial,
    setSerial,

    // status
    status,
    currentStep,
    progressPct,
    logs,
    auditId,
    errorMsg,

    // data slices (each may be null until its step completes)
    projectData,
    coords,
    fireData,
    satelliteImage,
    vegetationData,
    forestData,
    climateData,
    riskScore,
    narrative,

    // actions
    startAudit,
    resetAudit,
    downloadReport,
  };
}

export default useAudit;
