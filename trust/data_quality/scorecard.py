"""Pillar 7 — Data Quality: Weekly source scorecards and data quality dashboard.
Satisfies INTERNAL data governance reporting.
"""

import uuid
from datetime import date, datetime, timedelta
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import Column, Date, DateTime, Float, Index, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Session

from src.database import Base, BronzeRecord, GoldRecord, SourceConfig, get_db
from trust.data_quality.conflict_resolver import ConflictLog
from trust.data_quality.revision_tracker import IndicatorRevision

logger = structlog.get_logger().bind(pillar="data_quality")


class SourceScorecard(Base):
    __tablename__ = "source_scorecards"

    scorecard_id           = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_code            = Column(String(50),  nullable=False)
    week_start             = Column(Date,         nullable=False)
    extraction_success_rate = Column(Float,       nullable=False, default=0.0)
    avg_quality_score      = Column(Float,        nullable=True)
    revision_frequency     = Column(Float,        nullable=False, default=0.0)
    conflict_rate          = Column(Float,        nullable=False, default=0.0)
    availability_uptime    = Column(Float,        nullable=False, default=95.0)
    source_reputation_score = Column(Float,       nullable=False, default=0.0)
    computed_at            = Column(DateTime,     default=datetime.utcnow)
    compliance_context     = Column(String,       default="INTERNAL - Data Governance")

    __table_args__ = (
        Index("ix_source_scorecards_source_week", "source_code", "week_start"),
        UniqueConstraint("source_code", "week_start", name="uq_source_week"),
    )


class ScorecardCalculator:
    def __init__(self, db: Session) -> None:
        self._db = db

    def compute_scorecard(self, source_code: str, week_start: date) -> SourceScorecard:
        week_end = week_start + timedelta(days=7)

        # extraction_success_rate: non-null raw_value / total bronze records this week
        total_bronze = (
            self._db.query(func.count(BronzeRecord.record_id))
            .filter(
                BronzeRecord.source_code  == source_code,
                BronzeRecord.crawled_at   >= datetime.combine(week_start, datetime.min.time()),
                BronzeRecord.crawled_at   <  datetime.combine(week_end,   datetime.min.time()),
            )
            .scalar()
        ) or 0

        success_bronze = (
            self._db.query(func.count(BronzeRecord.record_id))
            .filter(
                BronzeRecord.source_code  == source_code,
                BronzeRecord.crawled_at   >= datetime.combine(week_start, datetime.min.time()),
                BronzeRecord.crawled_at   <  datetime.combine(week_end,   datetime.min.time()),
                BronzeRecord.raw_value    != None,  # noqa: E711
            )
            .scalar()
        ) or 0

        extraction_success_rate = (success_bronze / total_bronze * 100) if total_bronze > 0 else 0.0

        # avg_quality_score from silver_records
        from src.database import SilverRecord
        avg_dq = (
            self._db.query(func.avg(SilverRecord.dq_score))
            .filter(
                SilverRecord.source_code  == source_code,
                SilverRecord.processed_at >= datetime.combine(week_start, datetime.min.time()),
                SilverRecord.processed_at <  datetime.combine(week_end,   datetime.min.time()),
            )
            .scalar()
        )
        avg_quality_score = float(avg_dq) if avg_dq is not None else None

        # revision_frequency: revisions this week / total gold records for source
        revision_count = (
            self._db.query(func.count(IndicatorRevision.revision_id))
            .filter(
                IndicatorRevision.source_id  == source_code,
                IndicatorRevision.revised_at >= datetime.combine(week_start, datetime.min.time()),
                IndicatorRevision.revised_at <  datetime.combine(week_end,   datetime.min.time()),
            )
            .scalar()
        ) or 0

        total_gold = (
            self._db.query(func.count(GoldRecord.record_id))
            .filter(GoldRecord.source_code == source_code)
            .scalar()
        ) or 0

        revision_frequency = (revision_count / total_gold) if total_gold > 0 else 0.0

        # conflict_rate: non-ROUTINE conflicts where source was selected / total conflicts
        total_conflicts = (
            self._db.query(func.count(ConflictLog.conflict_id))
            .filter(ConflictLog.selected_source == source_code)
            .scalar()
        ) or 0

        non_routine_conflicts = (
            self._db.query(func.count(ConflictLog.conflict_id))
            .filter(
                ConflictLog.selected_source == source_code,
                ConflictLog.severity        != "ROUTINE",
            )
            .scalar()
        ) or 0

        conflict_rate = (non_routine_conflicts / total_conflicts) if total_conflicts > 0 else 0.0

        # availability_uptime: simplified from source_config.last_error_at
        source_cfg = (
            self._db.query(SourceConfig)
            .filter(SourceConfig.source_code == source_code)
            .first()
        )
        if source_cfg is not None and source_cfg.last_error_at is not None:
            # Count error as 5% penalty per error in the last week
            availability_uptime = max(0.0, 95.0 - 5.0)
        else:
            availability_uptime = 95.0

        # Composite reputation score
        effective_quality = (avg_quality_score if avg_quality_score is not None else 80.0)
        source_reputation_score = (
            extraction_success_rate / 100 * 0.3
            + effective_quality / 100 * 0.3
            + (1.0 - revision_frequency) * 0.2
            + (1.0 - conflict_rate) * 0.1
            + availability_uptime / 100 * 0.1
        ) * 100

        # Upsert
        existing = (
            self._db.query(SourceScorecard)
            .filter(
                SourceScorecard.source_code == source_code,
                SourceScorecard.week_start  == week_start,
            )
            .first()
        )
        if existing is not None:
            existing.extraction_success_rate = extraction_success_rate
            existing.avg_quality_score       = avg_quality_score
            existing.revision_frequency      = revision_frequency
            existing.conflict_rate           = conflict_rate
            existing.availability_uptime     = availability_uptime
            existing.source_reputation_score = source_reputation_score
            existing.computed_at             = datetime.utcnow()
            self._db.commit()
            self._db.refresh(existing)
            return existing

        scorecard = SourceScorecard(
            scorecard_id=uuid.uuid4(),
            source_code=source_code,
            week_start=week_start,
            extraction_success_rate=extraction_success_rate,
            avg_quality_score=avg_quality_score,
            revision_frequency=revision_frequency,
            conflict_rate=conflict_rate,
            availability_uptime=availability_uptime,
            source_reputation_score=source_reputation_score,
            computed_at=datetime.utcnow(),
        )
        self._db.add(scorecard)
        self._db.commit()
        self._db.refresh(scorecard)
        return scorecard

    def compute_all_scorecards(self, week_start: Optional[date] = None) -> list[SourceScorecard]:
        if week_start is None:
            today = date.today()
            week_start = today - timedelta(days=today.weekday())  # Monday

        sources = (
            self._db.query(SourceConfig)
            .filter(SourceConfig.is_active == True)  # noqa: E712
            .all()
        )
        results = []
        for src in sources:
            try:
                sc = self.compute_scorecard(src.source_code, week_start)
                results.append(sc)
            except Exception as exc:
                logger.error(
                    "scorecard_compute_failed",
                    source_code=src.source_code,
                    error=str(exc),
                )
        return results


def compute_weekly_scorecards() -> None:
    """APScheduler job: runs every Monday at 06:00 UTC."""
    from src.database import SessionLocal

    db = SessionLocal()
    try:
        calculator = ScorecardCalculator(db)
        today = date.today()
        week_start = today - timedelta(days=today.weekday())
        scorecards = calculator.compute_all_scorecards(week_start)
        logger.info("weekly_scorecards_computed", count=len(scorecards), week_start=str(week_start))
    except Exception as exc:
        logger.error("weekly_scorecards_failed", error=str(exc))
    finally:
        db.close()


# ── FastAPI router ─────────────────────────────────────────────────────────────

router = APIRouter(prefix="/api/data-quality", tags=["data-quality"])


@router.get("/sources/{source_id}/scorecard")
def get_source_scorecard(source_id: str, db: Session = Depends(get_db)):
    scorecard = (
        db.query(SourceScorecard)
        .filter(SourceScorecard.source_code == source_id)
        .order_by(SourceScorecard.week_start.desc())
        .first()
    )
    if scorecard is None:
        raise HTTPException(status_code=404, detail=f"No scorecard found for source '{source_id}'.")
    return {
        "scorecard_id":            str(scorecard.scorecard_id),
        "source_code":             scorecard.source_code,
        "week_start":              str(scorecard.week_start),
        "extraction_success_rate": scorecard.extraction_success_rate,
        "avg_quality_score":       scorecard.avg_quality_score,
        "revision_frequency":      scorecard.revision_frequency,
        "conflict_rate":           scorecard.conflict_rate,
        "availability_uptime":     scorecard.availability_uptime,
        "source_reputation_score": scorecard.source_reputation_score,
        "computed_at":             scorecard.computed_at.isoformat(),
    }


@router.get("/sources/scorecards")
def get_all_scorecards(db: Session = Depends(get_db)):
    # Latest scorecard per source, sorted by reputation DESC
    from sqlalchemy import func as sqlfunc
    subq = (
        db.query(
            SourceScorecard.source_code,
            sqlfunc.max(SourceScorecard.week_start).label("latest_week"),
        )
        .group_by(SourceScorecard.source_code)
        .subquery()
    )
    scorecards = (
        db.query(SourceScorecard)
        .join(
            subq,
            (SourceScorecard.source_code == subq.c.source_code)
            & (SourceScorecard.week_start == subq.c.latest_week),
        )
        .order_by(SourceScorecard.source_reputation_score.desc())
        .all()
    )
    return [
        {
            "scorecard_id":            str(sc.scorecard_id),
            "source_code":             sc.source_code,
            "week_start":              str(sc.week_start),
            "extraction_success_rate": sc.extraction_success_rate,
            "avg_quality_score":       sc.avg_quality_score,
            "revision_frequency":      sc.revision_frequency,
            "conflict_rate":           sc.conflict_rate,
            "availability_uptime":     sc.availability_uptime,
            "source_reputation_score": sc.source_reputation_score,
            "computed_at":             sc.computed_at.isoformat(),
        }
        for sc in scorecards
    ]
