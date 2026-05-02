from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AuditRequest(BaseModel):
    serial: str = Field(..., min_length=3)


class ProjectData(BaseModel):
    serial: str
    project_name: str | None = None
    registry: str | None = None
    country: str | None = None
    lat: float | None = None
    lon: float | None = None
    methodology: str | None = None
    project_type: str | None = None
    issuance_date: datetime | None = None
    proponent: str | None = None


class RiskScore(BaseModel):
    overall_score: int
    risk_level: str
    recommendation: str
    component_scores: dict[str, float] = Field(default_factory=dict)
    key_flags: list[str] = Field(default_factory=list)
    confidence: float


class AuditResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    audit_id: UUID
    project: ProjectData
    risk_score: RiskScore
    fire_data: dict[str, Any] | None = None
    vegetation_data: dict[str, Any] | None = None
    forest_data: dict[str, Any] | None = None
    climate_data: dict[str, Any] | None = None
    narrative: dict[str, Any] | None = None
    pdf_ready: bool = False
    pdf_url: str | None = None
    audit_duration_ms: int
