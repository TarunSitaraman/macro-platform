"""Pillar 7 — Data Quality: Data revision tracking with time-travel queries.
Satisfies SOX financial data accuracy requirements.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import Column, DateTime, Boolean, Float, Index, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Session

from src.database import Base, GoldRecord, get_db

logger = structlog.get_logger().bind(pillar="data_quality")


class IndicatorRevision(Base):
    __tablename__ = "indicator_revisions"

    revision_id        = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    indicator_code     = Column(String(100), nullable=False)
    country_code       = Column(String(3),   nullable=False)
    period             = Column(String(20),  nullable=False)
    source_id          = Column(String(50),  nullable=False)
    old_value          = Column(Float,        nullable=False)
    new_value          = Column(Float,        nullable=False)
    revision_pct       = Column(Float,        nullable=False)
    is_significant     = Column(Boolean,      nullable=False)
    revised_at         = Column(DateTime,     default=datetime.utcnow)
    gold_record_id     = Column(UUID(as_uuid=True), nullable=True)
    compliance_context = Column(String,       default="SOX - Financial Data Accuracy")

    __table_args__ = (
        Index("ix_indicator_revisions_key", "indicator_code", "country_code", "period"),
        Index("ix_indicator_revisions_revised_at", "revised_at"),
    )


@dataclass
class RevisionEvent:
    indicator_code: str
    old_value: float
    new_value: float
    revision_pct: float
    is_significant: bool


class RevisionTracker:
    def __init__(self, db: Session) -> None:
        self._db = db

    def check_and_record(
        self,
        indicator_code: str,
        country_code: str,
        period: str,
        source_id: str,
        new_value: float,
    ) -> Optional[RevisionEvent]:
        existing = (
            self._db.query(GoldRecord)
            .filter(
                GoldRecord.indicator_code == indicator_code,
                GoldRecord.country_code   == country_code,
                GoldRecord.period         == period,
            )
            .first()
        )
        if existing is None or existing.value is None:
            return None
        if existing.value == new_value:
            return None

        old_value = existing.value
        revision_pct = (
            (new_value - old_value) / abs(old_value) * 100
            if old_value != 0
            else 0.0
        )
        is_significant = abs(revision_pct) > 10.0

        rev = IndicatorRevision(
            revision_id=uuid.uuid4(),
            indicator_code=indicator_code,
            country_code=country_code,
            period=period,
            source_id=source_id,
            old_value=old_value,
            new_value=new_value,
            revision_pct=revision_pct,
            is_significant=is_significant,
            revised_at=datetime.utcnow(),
            gold_record_id=existing.record_id,
        )
        self._db.add(rev)
        self._db.commit()

        if is_significant:
            logger.warning(
                "significant_revision_detected",
                indicator_code=indicator_code,
                country_code=country_code,
                period=period,
                old_value=old_value,
                new_value=new_value,
                revision_pct=revision_pct,
                alert=True,
            )
        else:
            logger.info(
                "revision_recorded",
                indicator_code=indicator_code,
                country_code=country_code,
                period=period,
                revision_pct=revision_pct,
            )

        return RevisionEvent(
            indicator_code=indicator_code,
            old_value=old_value,
            new_value=new_value,
            revision_pct=revision_pct,
            is_significant=is_significant,
        )

    def get_revision_history(
        self,
        indicator_code: str,
        country_code: Optional[str] = None,
    ) -> list[IndicatorRevision]:
        query = self._db.query(IndicatorRevision).filter(
            IndicatorRevision.indicator_code == indicator_code
        )
        if country_code:
            query = query.filter(IndicatorRevision.country_code == country_code)
        return query.order_by(IndicatorRevision.revised_at.desc()).limit(100).all()

    def get_value_as_of(
        self,
        indicator_code: str,
        country_code: str,
        period: str,
        as_of: datetime,
    ) -> Optional[float]:
        current = (
            self._db.query(GoldRecord)
            .filter(
                GoldRecord.indicator_code == indicator_code,
                GoldRecord.country_code   == country_code,
                GoldRecord.period         == period,
            )
            .first()
        )
        if current is None:
            return None

        # Find revisions that occurred *after* the as_of date for this key
        later_revisions = (
            self._db.query(IndicatorRevision)
            .filter(
                IndicatorRevision.indicator_code == indicator_code,
                IndicatorRevision.country_code   == country_code,
                IndicatorRevision.period         == period,
                IndicatorRevision.revised_at     > as_of,
            )
            .order_by(IndicatorRevision.revised_at.asc())
            .all()
        )
        if not later_revisions:
            return current.value

        # The value as-of `as_of` is the old_value of the earliest revision after as_of
        return later_revisions[0].old_value


# ── FastAPI router ─────────────────────────────────────────────────────────────

router = APIRouter(prefix="/api/data-quality", tags=["data-quality"])


@router.get("/indicators/{indicator_id}/revision-history")
def get_revision_history(
    indicator_id: str,
    country: Optional[str] = None,
    db: Session = Depends(get_db),
):
    tracker = RevisionTracker(db)
    revisions = tracker.get_revision_history(indicator_id, country)
    return [
        {
            "revision_id":    str(r.revision_id),
            "indicator_code": r.indicator_code,
            "country_code":   r.country_code,
            "period":         r.period,
            "old_value":      r.old_value,
            "new_value":      r.new_value,
            "revision_pct":   r.revision_pct,
            "is_significant": r.is_significant,
            "revised_at":     r.revised_at.isoformat(),
        }
        for r in revisions
    ]


@router.get("/indicators/{indicator_id}/value")
def get_value_as_of(
    indicator_id: str,
    country: str,
    period: str,
    as_of: str,
    db: Session = Depends(get_db),
):
    try:
        as_of_dt = datetime.fromisoformat(as_of)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid as_of date: '{as_of}'. Use ISO 8601.")

    tracker = RevisionTracker(db)
    value = tracker.get_value_as_of(indicator_id, country, period, as_of_dt)
    if value is None:
        raise HTTPException(
            status_code=404,
            detail=f"No Gold record found for {indicator_id}/{country}/{period}.",
        )
    return {
        "indicator_code": indicator_id,
        "country_code":   country,
        "period":         period,
        "as_of":          as_of,
        "value":          value,
    }
