"""Pillar 9 — Fairness: Human accountability chain for data quality decisions.
Satisfies SOX internal controls requirements.
"""

import uuid
from datetime import datetime, timedelta
from enum import IntEnum
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import Column, DateTime, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Session

from src.database import Base, SessionLocal, get_db

logger = structlog.get_logger().bind(pillar="fairness")


class AccountabilityLevel(IntEnum):
    INGESTION          = 1
    DATA_QUALITY_ANALYST = 2
    DOMAIN_SME         = 3
    DATA_GOVERNANCE    = 4
    BUSINESS_OWNER     = 5


LEVEL_SLA_HOURS: dict[int, int] = {
    1: 0,
    2: 4,
    3: 8,
    4: 24,
    5: 48,
}

LEVEL_NAMES: dict[int, str] = {
    1: "Data Ingestion (Auto)",
    2: "Data Quality Analyst",
    3: "Domain SME",
    4: "Data Governance Lead",
    5: "Business Owner",
}


class ReviewTask(Base):
    __tablename__ = "accountability_tasks"

    task_id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    silver_record_id     = Column(UUID(as_uuid=True), nullable=True)
    indicator_code       = Column(String(100), nullable=False)
    country_code         = Column(String(3),   nullable=False)
    period               = Column(String(20),  nullable=False)
    current_level        = Column(Integer,     default=2)
    assigned_at          = Column(DateTime,    default=datetime.utcnow)
    sla_deadline         = Column(DateTime,    nullable=False)
    status               = Column(String(50),  default="PENDING")
    resolved_by          = Column(String(100), nullable=True)
    resolved_at          = Column(DateTime,    nullable=True)
    resolution_notes     = Column(Text,        nullable=True)
    escalated_from_level = Column(Integer,     nullable=True)
    compliance_context   = Column(String,      default="SOX - Internal Controls")

    __table_args__ = (
        Index("ix_accountability_tasks_status_sla", "status", "sla_deadline"),
    )


class AccountabilityChain:
    def __init__(self, db: Session) -> None:
        self._db = db

    def create_task(
        self,
        indicator_code: str,
        country_code: str,
        period: str,
        silver_record_id: Optional[str] = None,
        initial_level: int = 2,
    ) -> ReviewTask:
        sla_hours = LEVEL_SLA_HOURS.get(initial_level, 4)
        sla_deadline = (
            datetime.utcnow() + timedelta(hours=sla_hours)
            if sla_hours > 0
            else datetime.utcnow() + timedelta(hours=4)
        )
        silver_uuid = uuid.UUID(silver_record_id) if silver_record_id else None

        task = ReviewTask(
            task_id=uuid.uuid4(),
            silver_record_id=silver_uuid,
            indicator_code=indicator_code,
            country_code=country_code,
            period=period,
            current_level=initial_level,
            assigned_at=datetime.utcnow(),
            sla_deadline=sla_deadline,
            status="PENDING",
        )
        self._db.add(task)
        self._db.commit()
        self._db.refresh(task)
        logger.info(
            "accountability_task_created",
            task_id=str(task.task_id),
            indicator_code=indicator_code,
            country_code=country_code,
            period=period,
            level=LEVEL_NAMES.get(initial_level),
            sla_deadline=sla_deadline.isoformat(),
        )
        return task

    def check_and_escalate(self) -> int:
        now = datetime.utcnow()
        breached_tasks = (
            self._db.query(ReviewTask)
            .filter(
                ReviewTask.status       == "PENDING",
                ReviewTask.sla_deadline <  now,
            )
            .all()
        )
        escalated_count = 0
        for task in breached_tasks:
            if task.current_level >= 5:
                logger.critical(
                    "sla_breach_no_escalation_possible",
                    task_id=str(task.task_id),
                    indicator_code=task.indicator_code,
                    country_code=task.country_code,
                    current_level=LEVEL_NAMES.get(task.current_level),
                )
                continue

            new_level    = task.current_level + 1
            new_sla_hrs  = LEVEL_SLA_HOURS.get(new_level, 4)
            new_deadline = now + timedelta(hours=new_sla_hrs if new_sla_hrs > 0 else 4)

            task.escalated_from_level = task.current_level
            task.current_level        = new_level
            task.sla_deadline         = new_deadline
            escalated_count += 1

            logger.warning(
                "task_escalated",
                task_id=str(task.task_id),
                indicator_code=task.indicator_code,
                from_level=LEVEL_NAMES.get(task.escalated_from_level),
                to_level=LEVEL_NAMES.get(new_level),
                new_deadline=new_deadline.isoformat(),
            )

        if escalated_count > 0:
            self._db.commit()
        return escalated_count

    def get_open_tasks(self) -> list[ReviewTask]:
        return (
            self._db.query(ReviewTask)
            .filter(ReviewTask.status == "PENDING")
            .order_by(ReviewTask.sla_deadline.asc())
            .all()
        )


def check_sla_escalations() -> None:
    """APScheduler job: runs every 30 minutes."""
    db = SessionLocal()
    try:
        chain = AccountabilityChain(db)
        count = chain.check_and_escalate()
        logger.info("sla_escalation_run", escalated_count=count)
    except Exception as exc:
        logger.error("sla_escalation_failed", error=str(exc))
    finally:
        db.close()


# ── FastAPI router ─────────────────────────────────────────────────────────────

router = APIRouter(prefix="/api/review-queue", tags=["review-queue"])


@router.get("/")
def get_open_tasks(db: Session = Depends(get_db)):
    chain = AccountabilityChain(db)
    tasks = chain.get_open_tasks()
    now   = datetime.utcnow()
    return [
        {
            "task_id":              str(t.task_id),
            "silver_record_id":     str(t.silver_record_id) if t.silver_record_id else None,
            "indicator_code":       t.indicator_code,
            "country_code":         t.country_code,
            "period":               t.period,
            "current_level":        t.current_level,
            "current_level_name":   LEVEL_NAMES.get(t.current_level, "Unknown"),
            "assigned_at":          t.assigned_at.isoformat(),
            "sla_deadline":         t.sla_deadline.isoformat(),
            "sla_remaining_hours":  max(0.0, (t.sla_deadline - now).total_seconds() / 3600),
            "sla_breached":         t.sla_deadline < now,
            "status":               t.status,
            "escalated_from_level": t.escalated_from_level,
        }
        for t in tasks
    ]
