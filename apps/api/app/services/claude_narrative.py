"""Anthropic Claude narrative generation for audit reports.

Takes the assembled audit data + Groq risk score and produces seven
short, Bloomberg-Intelligence-style sections in strict JSON. Fails
gracefully — if Anthropic is unreachable or the response can't be
parsed, returns a structured "narrative unavailable" payload.

Uses prompt caching on the (large, stable) system prompt so repeat
audits hit the cache for ~90% input-cost savings.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

from anthropic import AsyncAnthropic
from anthropic import APIError, APIStatusError, AuthenticationError

from app.core.config import settings

logger = logging.getLogger(__name__)

# User pinned this model ID in the spec. Note: claude-opus-4-20250514
# is the dated alias for Opus 4.0; deprecates June 15, 2026. Migrating
# to claude-opus-4-7 is a one-line model swap when ready.
MODEL = "claude-opus-4-20250514"

SECTIONS = (
    "executive_summary",
    "fire_analysis",
    "vegetation_analysis",
    "climate_context",
    "registry_assessment",
    "recommendation_text",
    "methodology_note",
)

SYSTEM_PROMPT = """You are VERITAS Oracle, a fraud-detection analyst writing the narrative section of a carbon-credit integrity report.

Voice: Bloomberg Intelligence. Precise. Decisive. Direct verdicts. No hedging language ("may", "could", "appears to"). No filler.
Length: each section MUST be 120 words or fewer. Tighter is better.
Output: ONLY a single JSON object matching the schema below. No prose, no markdown, no code fences.

REQUIRED SCHEMA — every key present, every value a non-empty string:
{
  "executive_summary":   "<top-line verdict + the one or two signals that drove it>",
  "fire_analysis":       "<what the NASA FIRMS data shows for this site>",
  "vegetation_analysis": "<what the Sentinel-2 NDVI delta shows>",
  "climate_context":     "<what NOAA temp/precip anomaly implies for project additionality>",
  "registry_assessment": "<read on the registry record itself: status, recency, coverage gaps>",
  "recommendation_text": "<concrete action: full issuance / partial flag / hold / reject — with rationale>",
  "methodology_note":    "<one-sentence disclosure of which data sources were missing or degraded>"
}"""


def _ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def _default_narrative(reason: str) -> dict[str, Any]:
    """Returned when the Anthropic call fails or output can't be parsed.
    Every required key is present so downstream consumers (PDF, UI) don't
    need to special-case missing fields."""
    base = "Narrative unavailable: " + reason
    return {
        section: base if section == "methodology_note" else "Narrative generation unavailable for this section."
        for section in SECTIONS
    } | {"error": reason}


def _user_payload(audit_data: dict[str, Any], risk_score: dict[str, Any]) -> str:
    """Build the user message — only the variable parts go here so the
    cached system prompt prefix stays stable across audits."""
    return json.dumps(
        {
            "project": {
                "serial": audit_data.get("serial"),
                "name": audit_data.get("project_name"),
                "registry": audit_data.get("registry"),
                "country": audit_data.get("country"),
                "methodology": audit_data.get("methodology"),
                "lat": audit_data.get("lat"),
                "lon": audit_data.get("lon"),
                "credits_issued": audit_data.get("credits_issued"),
                "last_verification_date": audit_data.get("last_verification_date"),
            },
            "fire": audit_data.get("fire_data") or {},
            "vegetation": audit_data.get("vegetation_data") or {},
            "forest": audit_data.get("forest_data") or {},
            "climate": audit_data.get("climate_data") or {},
            "risk_score": {
                "overall_score": risk_score.get("overall_score"),
                "risk_level": risk_score.get("risk_level"),
                "recommendation": risk_score.get("recommendation"),
                "component_scores": risk_score.get("component_scores"),
                "key_flags": risk_score.get("key_flags"),
                "positive_indicators": risk_score.get("positive_indicators"),
                "confidence": risk_score.get("confidence"),
                "audit_summary": risk_score.get("audit_summary"),
            },
        },
        indent=2,
        default=str,
    )


def _strip_fences(text: str) -> str:
    """Defensive: strip ```json fences just in case the model adds them."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3]
    return text.strip()


async def generate_narrative(
    audit_data: dict[str, Any], risk_score: dict[str, Any]
) -> dict[str, Any]:
    started = time.perf_counter()
    if not settings.ANTHROPIC_API_KEY:
        return _default_narrative("ANTHROPIC_API_KEY not configured")

    client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    user_text = _user_payload(audit_data, risk_score)

    try:
        response = await client.messages.create(
            model=MODEL,
            max_tokens=2048,  # plenty for 7 short sections
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    # Stable prefix — every audit hits the same prompt, so
                    # cache it for ~90% input cost savings on repeat calls.
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user_text}],
        )
    except AuthenticationError as exc:
        logger.warning("claude narrative: auth error (%dms)", _ms(started))
        return _default_narrative(f"Anthropic auth failed: {exc}")
    except APIStatusError as exc:
        logger.warning("claude narrative: HTTP %s (%dms)", exc.status_code, _ms(started))
        return _default_narrative(f"Anthropic returned HTTP {exc.status_code}")
    except APIError as exc:
        logger.warning("claude narrative: API error: %s (%dms)", exc, _ms(started))
        return _default_narrative(f"Anthropic API error: {exc}")
    except Exception as exc:  # noqa: BLE001
        logger.warning("claude narrative: unexpected error: %s (%dms)", exc, _ms(started))
        return _default_narrative(f"Narrative request failed: {exc}")

    # Pull the first text block out of the response
    text_block = next(
        (block.text for block in response.content if getattr(block, "type", None) == "text"),
        "",
    )
    try:
        parsed = json.loads(_strip_fences(text_block))
    except json.JSONDecodeError as exc:
        logger.warning("claude narrative: JSON parse failed: %s (%dms)", exc, _ms(started))
        return _default_narrative(f"Model output was not valid JSON: {exc}")

    # Coerce to the required shape — guarantee every section is present
    out: dict[str, Any] = {
        section: (parsed.get(section) or "Section omitted by model.").strip()
        for section in SECTIONS
    }
    out["error"] = None

    cached = getattr(response.usage, "cache_read_input_tokens", 0) or 0
    written = getattr(response.usage, "cache_creation_input_tokens", 0) or 0
    logger.info(
        "claude narrative ok in=%d out=%d cache_read=%d cache_write=%d (%dms)",
        response.usage.input_tokens,
        response.usage.output_tokens,
        cached,
        written,
        _ms(started),
    )
    return out
