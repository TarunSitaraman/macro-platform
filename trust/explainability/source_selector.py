"""Pillar 6 — Explainability: Source selection audit trail for conflict resolution.
Satisfies MiFID II research transparency."""

import uuid
from dataclasses import dataclass
from datetime import datetime

import structlog
from fastapi import APIRouter, Depends
from sqlalchemy import Column, DateTime, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Session

from src.database import Base, get_db

logger = structlog.get_logger().bind(pillar="explainability")


# ── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass
class CandidateSource:
    source_code: str
    reliability_score: float
    value: float
    freshness_hours: float
    quality_score: float


# ── SQLAlchemy Model ──────────────────────────────────────────────────────────

class SourceSelectionEvent(Base):
    __tablename__ = "explainability_log"

    event_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    indicator_code = Column(String(100), nullable=False)
    country_code = Column(String(3), nullable=False)
    period = Column(String(20), nullable=False)
    candidate_sources = Column(JSONB, nullable=False)
    selected_source = Column(String(50), nullable=False)
    selection_rationale = Column(Text, nullable=False)
    decided_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    compliance_context = Column(
        String, default="MiFID II - Research Transparency", nullable=False
    )

    __table_args__ = (
        Index("ix_explainability_indicator_decided", "indicator_code", "decided_at"),
    )


# ── SourceSelectionExplainer ──────────────────────────────────────────────────

class SourceSelectionExplainer:
    def __init__(self, db: Session) -> None:
        self._db = db

    def record_selection(
        self,
        indicator_code: str,
        country_code: str,
        period: str,
        candidates: list[CandidateSource],
        selected: CandidateSource,
        rationale: str,
    ) -> SourceSelectionEvent:
        candidates_json = [
            {
                "source_code": c.source_code,
                "reliability_score": c.reliability_score,
                "value": c.value,
                "freshness_hours": c.freshness_hours,
                "quality_score": c.quality_score,
            }
            for c in candidates
        ]
        event = SourceSelectionEvent(
            indicator_code=indicator_code,
            country_code=country_code,
            period=period,
            candidate_sources=candidates_json,
            selected_source=selected.source_code,
            selection_rationale=rationale,
        )
        self._db.add(event)
        self._db.commit()
        self._db.refresh(event)
        logger.info(
            "source_selection_recorded",
            indicator_code=indicator_code,
            country_code=country_code,
            period=period,
            selected_source=selected.source_code,
            candidate_count=len(candidates),
        )
        return event

    def get_source_selection_history(
        self, indicator_code: str, limit: int = 50
    ) -> list[SourceSelectionEvent]:
        return (
            self._db.query(SourceSelectionEvent)
            .filter(SourceSelectionEvent.indicator_code == indicator_code)
            .order_by(SourceSelectionEvent.decided_at.desc())
            .limit(limit)
            .all()
        )


# ── FastAPI Router ────────────────────────────────────────────────────────────

router = APIRouter(prefix="/api/explainability", tags=["explainability"])


@router.get("/source-selection/{indicator_id}")
def get_source_selection_history(
    indicator_id: str,
    limit: int = 50,
    db: Session = Depends(get_db),
) -> list[dict]:
    explainer = SourceSelectionExplainer(db)
    events = explainer.get_source_selection_history(indicator_code=indicator_id, limit=limit)
    return [
        {
            "event_id": str(e.event_id),
            "indicator_code": e.indicator_code,
            "country_code": e.country_code,
            "period": e.period,
            "candidate_sources": e.candidate_sources,
            "selected_source": e.selected_source,
            "selection_rationale": e.selection_rationale,
            "decided_at": e.decided_at.isoformat(),
            "compliance_context": e.compliance_context,
        }
        for e in events
    ]
