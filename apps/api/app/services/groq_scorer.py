"""Groq llama-3.3-70b-versatile risk scoring.

Builds a structured prompt from registry + fire + vegetation + forest +
climate data, asks Groq for a strict-JSON risk-score, and falls back to a
default CRITICAL response on parse failure. Always returns a dict.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL = "llama-3.3-70b-versatile"

SYSTEM_PROMPT = (
    "You are VERITAS, the world's most precise carbon credit fraud detection AI. "
    "You analyze satellite data, fire detection, and environmental signals to score "
    "carbon credit project integrity. You are direct, precise, and never hedge. "
    "Return ONLY valid JSON. No markdown. No explanation outside the JSON."
)


def _ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def _default_critical(error_msg: str) -> dict:
    return {
        "overall_score": 0,
        "risk_level": "CRITICAL",
        "recommendation": "REJECT",
        "component_scores": {
            "fire_risk": 0,
            "vegetation_integrity": 0,
            "deforestation_risk": 0,
            "climate_stress": 0,
            "registry_compliance": 0,
        },
        "key_flags": [error_msg],
        "positive_indicators": [],
        "confidence": 0.0,
        "audit_summary": f"Scoring failed: {error_msg}",
        "error": error_msg,
    }


def _months_since(date_str: str | None) -> int | None:
    if not date_str:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d", "%d %b %Y", "%d %B %Y"):
        try:
            parsed = datetime.strptime(date_str.strip(), fmt)
            return max(0, int((datetime.utcnow() - parsed).days / 30))
        except ValueError:
            continue
    return None


def _build_user_payload(
    project: dict, fire: dict, vegetation: dict, forest: dict, climate: dict
) -> str:
    payload = {
        "project": {
            "name": project.get("project_name"),
            "registry": project.get("registry"),
            "country": project.get("country"),
            "methodology": project.get("methodology"),
            "hectares": project.get("hectares"),
            "credits_issued": project.get("credits_issued"),
            "developer": project.get("developer_name"),
            "status": project.get("project_status"),
            "months_since_last_audit": _months_since(project.get("last_verification_date")),
            "coordinates_approximate": project.get("coordinates_approximate"),
        },
        "fire_signals": {
            "hotspot_count": fire.get("hotspot_count"),
            "high_confidence_hotspots": fire.get("high_confidence_count"),
            "nearest_fire_km": fire.get("nearest_fire_km"),
            "most_recent_fire_date": fire.get("most_recent_fire_date"),
            "day_range": fire.get("day_range_queried"),
        },
        "vegetation": {
            "current_ndvi": vegetation.get("current_ndvi"),
            "ndvi_change_pct_3yr": vegetation.get("vegetation_change_pct"),
            "deforestation_flag": vegetation.get("deforestation_flag"),
            "health_status": vegetation.get("health_status"),
        },
        "forest": {
            "annual_loss_rate_pct": forest.get("annual_loss_rate_pct"),
            "intact_forest_pct": forest.get("intact_forest_pct"),
            "primary_forest_loss_ha": forest.get("primary_forest_loss_ha"),
            "trend": forest.get("trend"),
        },
        "climate": {
            "temp_anomaly_c": climate.get("temp_anomaly_c"),
            "precip_anomaly_mm": climate.get("precipitation_anomaly_mm"),
            "drought_stress": climate.get("drought_stress_flag"),
            "risk_level": climate.get("climate_risk_level"),
        },
        "schema_required": {
            "overall_score": "0-100, higher=more trustworthy",
            "risk_level": "LOW|MEDIUM|HIGH|CRITICAL",
            "recommendation": "FULL_ISSUANCE|PARTIAL_FLAG|HOLD|REJECT",
            "component_scores": {
                "fire_risk": "0-100",
                "vegetation_integrity": "0-100",
                "deforestation_risk": "0-100",
                "climate_stress": "0-100",
                "registry_compliance": "0-100",
            },
            "key_flags": "list of specific findings, max 6",
            "positive_indicators": "list of good signs, max 4",
            "confidence": "0.0-1.0",
            "audit_summary": "one sentence verdict",
        },
    }
    return json.dumps(payload, indent=2)


def _strip_json(content: str) -> str:
    text = (content or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3]
    return text.strip()


async def calculate_risk_score(
    project: dict,
    fire: dict,
    vegetation: dict,
    forest: dict,
    climate: dict,
) -> dict:
    started = time.perf_counter()
    if not settings.GROQ_API_KEY:
        return _default_critical("GROQ_API_KEY not configured")

    user_payload = _build_user_payload(project, fire, vegetation, forest, climate)

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                GROQ_URL,
                json={
                    "model": MODEL,
                    "temperature": 0.1,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_payload},
                    ],
                },
                headers={
                    "Authorization": f"Bearer {settings.GROQ_API_KEY}",
                    "Content-Type": "application/json",
                },
            )
            if response.status_code != 200:
                return _default_critical(
                    f"Groq returned HTTP {response.status_code}: {response.text[:200]}"
                )
            payload = response.json()
    except Exception as exc:  # noqa: BLE001
        return _default_critical(f"Groq request failed: {exc}")

    try:
        content = payload["choices"][0]["message"]["content"]
        score = json.loads(_strip_json(content))
    except (KeyError, IndexError, json.JSONDecodeError) as exc:
        return _default_critical(f"Failed to parse model output: {exc}")

    score.setdefault("error", None)
    logger.info(
        "groq score: overall=%s level=%s rec=%s (%dms)",
        score.get("overall_score"),
        score.get("risk_level"),
        score.get("recommendation"),
        _ms(started),
    )
    return score
