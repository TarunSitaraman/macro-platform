"""Pillar 8 — Transparency: Three-level citation lineage for every indicator value.
Satisfies MiFID II research audit trail requirements.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import Column, DateTime, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Session

from src.database import Base, get_db

logger = structlog.get_logger().bind(pillar="transparency")


@dataclass
class Level1:
    """Source Attribution."""
    source_name:      str
    source_url:       str
    access_timestamp: datetime
    crawl_method:     str  # "API" | "HTML" | "PDF" | "SCREENSHOT"
    api_endpoint:     Optional[str] = None
    dataset_version:  Optional[str] = None


@dataclass
class Level2:
    """Processing History."""
    extraction_method:     str
    quality_score:         float
    processed_by:          str
    transformations_applied: list[str] = field(default_factory=list)
    quality_checks_run:    list[str]   = field(default_factory=list)
    llm_model_used:        Optional[str] = None
    prompt_version:        Optional[str] = None


@dataclass
class Level3:
    """Serving Lineage."""
    data_products:         list[str] = field(default_factory=list)
    api_calls_count:       int       = 0
    chatbot_sessions_count: int      = 0


class CitationTrail(Base):
    __tablename__ = "citation_trails"

    trail_id           = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    indicator_code     = Column(String(100), nullable=False)
    country_code       = Column(String(3),   nullable=False)
    period             = Column(String(20),  nullable=False)
    level1             = Column(JSONB,        nullable=False)
    level2             = Column(JSONB,        nullable=False)
    level3             = Column(JSONB,        nullable=False)
    created_at         = Column(DateTime,    default=datetime.utcnow)
    updated_at         = Column(DateTime,    default=datetime.utcnow, onupdate=datetime.utcnow)
    compliance_context = Column(String,      default="MiFID II - Research Audit Trail")

    __table_args__ = (
        UniqueConstraint("indicator_code", "country_code", "period", name="uq_citation_trail_key"),
    )


def _level1_to_dict(l1: Level1) -> dict:
    return {
        "source_name":      l1.source_name,
        "source_url":       l1.source_url,
        "access_timestamp": l1.access_timestamp.isoformat(),
        "crawl_method":     l1.crawl_method,
        "api_endpoint":     l1.api_endpoint,
        "dataset_version":  l1.dataset_version,
    }


def _level2_to_dict(l2: Level2) -> dict:
    return {
        "extraction_method":      l2.extraction_method,
        "quality_score":          l2.quality_score,
        "processed_by":           l2.processed_by,
        "transformations_applied": l2.transformations_applied,
        "quality_checks_run":     l2.quality_checks_run,
        "llm_model_used":         l2.llm_model_used,
        "prompt_version":         l2.prompt_version,
    }


def _default_level3_dict() -> dict:
    return {
        "data_products":          [],
        "api_calls_count":        0,
        "chatbot_sessions_count": 0,
    }


class CitationTrailManager:
    def __init__(self, db: Session) -> None:
        self._db = db

    def create_trail(
        self,
        indicator_code: str,
        country_code: str,
        period: str,
        level1: Level1,
        level2: Level2,
    ) -> CitationTrail:
        existing = self.get_trail(indicator_code, country_code, period)
        if existing is not None:
            existing.level1     = _level1_to_dict(level1)
            existing.level2     = _level2_to_dict(level2)
            existing.updated_at = datetime.utcnow()
            self._db.commit()
            self._db.refresh(existing)
            logger.info(
                "citation_trail_updated",
                indicator_code=indicator_code,
                country_code=country_code,
                period=period,
            )
            return existing

        trail = CitationTrail(
            trail_id=uuid.uuid4(),
            indicator_code=indicator_code,
            country_code=country_code,
            period=period,
            level1=_level1_to_dict(level1),
            level2=_level2_to_dict(level2),
            level3=_default_level3_dict(),
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        self._db.add(trail)
        self._db.commit()
        self._db.refresh(trail)
        logger.info(
            "citation_trail_created",
            indicator_code=indicator_code,
            country_code=country_code,
            period=period,
        )
        return trail

    def record_access(
        self,
        indicator_code: str,
        country_code: str,
        period: str,
        access_type: str = "api",
    ) -> None:
        trail = self.get_trail(indicator_code, country_code, period)
        if trail is None:
            return

        level3 = dict(trail.level3) if trail.level3 else _default_level3_dict()
        if access_type == "chatbot":
            level3["chatbot_sessions_count"] = level3.get("chatbot_sessions_count", 0) + 1
        else:
            level3["api_calls_count"] = level3.get("api_calls_count", 0) + 1

        trail.level3     = level3
        trail.updated_at = datetime.utcnow()
        self._db.commit()

    def get_trail(
        self,
        indicator_code: str,
        country_code: str,
        period: str,
    ) -> Optional[CitationTrail]:
        return (
            self._db.query(CitationTrail)
            .filter(
                CitationTrail.indicator_code == indicator_code,
                CitationTrail.country_code   == country_code,
                CitationTrail.period         == period,
            )
            .first()
        )


# ── FastAPI router ─────────────────────────────────────────────────────────────

router = APIRouter(prefix="/api/citation", tags=["citation"])


@router.get("/{indicator_id}/{period}")
def get_citation_trail(
    indicator_id: str,
    period: str,
    country: Optional[str] = None,
    db: Session = Depends(get_db),
):
    if not country:
        raise HTTPException(status_code=400, detail="'country' query parameter is required.")

    manager = CitationTrailManager(db)
    trail = manager.get_trail(indicator_id, country, period)
    if trail is None:
        raise HTTPException(
            status_code=404,
            detail=f"No citation trail found for {indicator_id}/{country}/{period}.",
        )
    return {
        "trail_id":        str(trail.trail_id),
        "indicator_code":  trail.indicator_code,
        "country_code":    trail.country_code,
        "period":          trail.period,
        "level1":          trail.level1,
        "level2":          trail.level2,
        "level3":          trail.level3,
        "created_at":      trail.created_at.isoformat(),
        "updated_at":      trail.updated_at.isoformat(),
        "compliance_context": trail.compliance_context,
    }
