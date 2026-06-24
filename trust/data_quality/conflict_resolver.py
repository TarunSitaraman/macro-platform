"""Pillar 7 — Data Quality: Multi-source conflict resolution with audit trail.
Satisfies MiFID II data integrity requirements.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import Column, DateTime, Float, Boolean, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Session

from src.database import Base, GoldRecord, ReviewQueue, SessionLocal, get_db

logger = structlog.get_logger().bind(pillar="data_quality")


class ConflictSeverity(str, Enum):
    ROUTINE  = "ROUTINE"
    MODERATE = "MODERATE"
    MAJOR    = "MAJOR"


@dataclass
class ConflictResolution:
    selected_source: str
    selected_value: float
    variance_pct: float
    severity: ConflictSeverity
    rationale: str


@dataclass
class SourceValue:
    source_code: str
    value: float
    reliability_score: float
    crawled_at: datetime


class ConflictLog(Base):
    __tablename__ = "conflict_log"

    conflict_id        = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    indicator_code     = Column(String(100), nullable=False)
    country_code       = Column(String(3),   nullable=False)
    period             = Column(String(20),  nullable=False)
    candidate_values   = Column(JSONB,        nullable=False)
    selected_source    = Column(String(50),  nullable=False)
    selected_value     = Column(Float,        nullable=False)
    variance_pct       = Column(Float,        nullable=False)
    severity           = Column(String(20),  nullable=False)
    resolution_rationale = Column(Text,      nullable=False)
    resolved_at        = Column(DateTime,    default=datetime.utcnow)
    requires_review    = Column(Boolean,     default=False)
    compliance_context = Column(String,      default="MiFID II - Data Integrity")

    __table_args__ = (
        Index("ix_conflict_log_indicator_resolved", "indicator_code", "resolved_at"),
    )


class ConflictResolver:
    def __init__(self, db: Session) -> None:
        self._db = db

    def resolve(
        self,
        indicator_code: str,
        country_code: str,
        period: str,
        candidates: list[SourceValue],
    ) -> ConflictResolution:
        if len(candidates) <= 1:
            single = candidates[0] if candidates else SourceValue(
                source_code="unknown", value=0.0, reliability_score=0.0,
                crawled_at=datetime.utcnow()
            )
            resolution = ConflictResolution(
                selected_source=single.source_code,
                selected_value=single.value,
                variance_pct=0.0,
                severity=ConflictSeverity.ROUTINE,
                rationale="Single source — no conflict.",
            )
            self._write_log(indicator_code, country_code, period, candidates, resolution, False)
            return resolution

        values = [c.value for c in candidates]
        mean_value = sum(values) / len(values)
        variance_pct = (
            (max(values) - min(values)) / abs(mean_value) * 100
            if mean_value != 0
            else 0.0
        )

        requires_review = False

        if variance_pct < 1.0:
            best = max(candidates, key=lambda c: c.reliability_score)
            severity = ConflictSeverity.ROUTINE
            rationale = (
                f"Variance {variance_pct:.3f}% < 1% — selected highest reliability source "
                f"'{best.source_code}' (score={best.reliability_score})."
            )

        elif variance_pct <= 5.0:
            now = datetime.utcnow().replace(tzinfo=timezone.utc)

            def composite_score(c: SourceValue) -> float:
                crawled = c.crawled_at
                if crawled.tzinfo is None:
                    crawled = crawled.replace(tzinfo=timezone.utc)
                hours_old = (now - crawled).total_seconds() / 3600
                return c.reliability_score * 0.7 + (1.0 / (hours_old + 1)) * 0.3 * 100

            best = max(candidates, key=composite_score)
            severity = ConflictSeverity.MODERATE
            rationale = (
                f"Variance {variance_pct:.3f}% in [1%,5%] — selected source '{best.source_code}' "
                "by composite reliability+recency score."
            )

        else:
            requires_review = True
            severity = ConflictSeverity.MAJOR

            prev_gold = (
                self._db.query(GoldRecord)
                .filter(
                    GoldRecord.indicator_code == indicator_code,
                    GoldRecord.country_code == country_code,
                )
                .order_by(GoldRecord.period.desc())
                .first()
            )
            if prev_gold is not None:
                best_source = prev_gold.source_code or candidates[0].source_code
                best_value = prev_gold.value
                rationale = (
                    f"Variance {variance_pct:.3f}% > 5% — MAJOR conflict. "
                    f"Retaining previous Gold value {best_value} from '{best_source}'."
                )
            else:
                best = max(candidates, key=lambda c: c.reliability_score)
                best_source = best.source_code
                best_value = best.value
                rationale = (
                    f"Variance {variance_pct:.3f}% > 5% — MAJOR conflict, no prior Gold. "
                    f"Selected highest reliability source '{best_source}'."
                )

            # Write to review_queue for each candidate silver record
            silver_review = ReviewQueue(
                silver_id=uuid.uuid4(),  # placeholder — real integration would use actual silver_id
                indicator_code=indicator_code,
                country_code=country_code,
                period=period,
                extracted_value=str(best_value) if prev_gold is None else str(prev_gold.value),
                status="PENDING",
                created_at=datetime.utcnow(),
                sla_deadline=datetime.utcnow() + timedelta(hours=4),
            )
            self._db.add(silver_review)

            resolution = ConflictResolution(
                selected_source=best_source,
                selected_value=best_value if prev_gold is None else prev_gold.value,
                variance_pct=variance_pct,
                severity=severity,
                rationale=rationale,
            )
            self._write_log(indicator_code, country_code, period, candidates, resolution, requires_review)
            self._db.commit()
            logger.warning(
                "conflict_resolved",
                indicator_code=indicator_code,
                country_code=country_code,
                period=period,
                variance_pct=variance_pct,
                severity=severity.value,
                requires_review=requires_review,
            )
            return resolution

        resolution = ConflictResolution(
            selected_source=best.source_code,
            selected_value=best.value,
            variance_pct=variance_pct,
            severity=severity,
            rationale=rationale,
        )
        self._write_log(indicator_code, country_code, period, candidates, resolution, requires_review)
        self._db.commit()
        logger.info(
            "conflict_resolved",
            indicator_code=indicator_code,
            country_code=country_code,
            period=period,
            variance_pct=variance_pct,
            severity=severity.value,
        )
        return resolution

    def _write_log(
        self,
        indicator_code: str,
        country_code: str,
        period: str,
        candidates: list[SourceValue],
        resolution: ConflictResolution,
        requires_review: bool,
    ) -> None:
        candidate_json = [
            {
                "source_code": c.source_code,
                "value": c.value,
                "reliability_score": c.reliability_score,
            }
            for c in candidates
        ]
        log_entry = ConflictLog(
            conflict_id=uuid.uuid4(),
            indicator_code=indicator_code,
            country_code=country_code,
            period=period,
            candidate_values=candidate_json,
            selected_source=resolution.selected_source,
            selected_value=resolution.selected_value,
            variance_pct=resolution.variance_pct,
            severity=resolution.severity.value,
            resolution_rationale=resolution.rationale,
            resolved_at=datetime.utcnow(),
            requires_review=requires_review,
        )
        self._db.add(log_entry)


# ── FastAPI router ─────────────────────────────────────────────────────────────

router = APIRouter(prefix="/api/data-quality", tags=["data-quality"])


@router.get("/conflicts")
def get_recent_conflicts(
    indicator_id: Optional[str] = None,
    days: int = 7,
    db: Session = Depends(get_db),
):
    from datetime import timedelta
    from sqlalchemy import and_

    cutoff = datetime.utcnow() - timedelta(days=days)
    query = db.query(ConflictLog).filter(ConflictLog.resolved_at >= cutoff)
    if indicator_id:
        query = query.filter(ConflictLog.indicator_code == indicator_id)
    records = query.order_by(ConflictLog.resolved_at.desc()).limit(200).all()
    return [
        {
            "conflict_id":   str(r.conflict_id),
            "indicator_code": r.indicator_code,
            "country_code":  r.country_code,
            "period":        r.period,
            "variance_pct":  r.variance_pct,
            "severity":      r.severity,
            "selected_source": r.selected_source,
            "selected_value":  r.selected_value,
            "requires_review": r.requires_review,
            "resolved_at":   r.resolved_at.isoformat(),
        }
        for r in records
    ]
