import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, Column, DateTime, Float, Integer, String
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


class Audit(Base):
    __tablename__ = "audits"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    serial = Column(String, nullable=False, index=True)
    project_name = Column(String, nullable=True)
    registry = Column(String, nullable=True)
    country = Column(String, nullable=True)
    lat = Column(Float, nullable=True)
    lon = Column(Float, nullable=True)

    overall_score = Column(Integer, nullable=True)
    risk_level = Column(String, nullable=True)
    recommendation = Column(String, nullable=True)

    fire_data = Column(JSON, nullable=True)
    vegetation_data = Column(JSON, nullable=True)
    forest_data = Column(JSON, nullable=True)
    climate_data = Column(JSON, nullable=True)
    risk_breakdown = Column(JSON, nullable=True)
    narrative = Column(JSON, nullable=True)

    pdf_url = Column(String, nullable=True)
    pdf_ready = Column(Boolean, nullable=False, default=False)

    audit_duration_ms = Column(Integer, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
