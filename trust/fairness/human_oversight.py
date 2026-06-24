"""Pillar 9 — Fairness: Human-in-the-loop oversight gate for Silver→Gold promotion.
Satisfies SOX Section 404 internal controls.
"""

import uuid
from datetime import datetime
from enum import Enum
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import Column, Boolean, DateTime, Float, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Session

from src.database import Base, SilverRecord, get_db

logger = structlog.get_logger().bind(pillar="fairness")


class OversightDecision(str, Enum):
    AUTO_APPROVED  = "AUTO_APPROVED"
    PENDING_REVIEW = "PENDING_REVIEW"
    APPROVED       = "APPROVED"
    REJECTED       = "REJECTED"


class OversightApproval(Base):
    """
    Write-once immutable record — no UPDATE or DELETE should be performed.
    Each decision (auto, approve, reject) appends a new row.
    """
    __tablename__ = "oversight_approvals"

    approval_id        = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    silver_record_id   = Column(UUID(as_uuid=True), nullable=False)
    indicator_code     = Column(String(100), nullable=False)
    country_code       = Column(String(3),   nullable=False)
    period             = Column(String(20),  nullable=False)
    dq_score           = Column(Float,        nullable=False)
    decision           = Column(String(50),  nullable=False)
    decided_by         = Column(String(100), default="system")
    decided_at         = Column(DateTime,    default=datetime.utcnow)
    notes              = Column(Text,        nullable=True)
    is_immutable       = Column(Boolean,     default=True)
    compliance_context = Column(String,      default="SOX Section 404 - Internal Controls")

    __table_args__ = (
        Index("ix_oversight_approvals_silver", "silver_record_id"),
        Index("ix_oversight_approvals_decided_at", "decided_at"),
    )


class HumanOversightGate:
    def __init__(self, db: Session) -> None:
        self._db = db

    def evaluate(
        self,
        silver_record_id: str,
        dq_score: float,
        indicator_code: str,
        country_code: str,
        period: str,
    ) -> OversightDecision:
        if dq_score > 90.0:
            decision = OversightDecision.AUTO_APPROVED
        elif dq_score >= 70.0:
            decision = OversightDecision.PENDING_REVIEW
        else:
            decision = OversightDecision.REJECTED

        self._write_approval(
            silver_record_id=silver_record_id,
            indicator_code=indicator_code,
            country_code=country_code,
            period=period,
            dq_score=dq_score,
            decision=decision,
            decided_by="system",
            notes="",
        )
        logger.info(
            "oversight_evaluated",
            silver_record_id=silver_record_id,
            dq_score=dq_score,
            decision=decision.value,
        )
        return decision

    def approve(
        self,
        silver_record_id: str,
        approved_by: str,
        notes: str = "",
    ) -> OversightApproval:
        pending = self._find_pending(silver_record_id)
        if pending is None:
            raise ValueError(
                f"No PENDING_REVIEW oversight record found for silver_record_id={silver_record_id}."
            )
        approval = self._write_approval(
            silver_record_id=silver_record_id,
            indicator_code=pending.indicator_code,
            country_code=pending.country_code,
            period=pending.period,
            dq_score=pending.dq_score,
            decision=OversightDecision.APPROVED,
            decided_by=approved_by,
            notes=notes,
        )
        logger.info(
            "oversight_approved",
            silver_record_id=silver_record_id,
            approved_by=approved_by,
            action="approve",
        )
        return approval

    def reject(
        self,
        silver_record_id: str,
        rejected_by: str,
        notes: str = "",
    ) -> OversightApproval:
        pending = self._find_pending(silver_record_id)
        if pending is None:
            # Allow ADMIN override of already-evaluated records below 70
            auto_rejected = (
                self._db.query(OversightApproval)
                .filter(
                    OversightApproval.silver_record_id == uuid.UUID(silver_record_id),
                    OversightApproval.decision         == OversightDecision.REJECTED.value,
                )
                .order_by(OversightApproval.decided_at.desc())
                .first()
            )
            if auto_rejected is None:
                raise ValueError(
                    f"No reviewable oversight record found for silver_record_id={silver_record_id}."
                )
            source = auto_rejected
        else:
            source = pending

        approval = self._write_approval(
            silver_record_id=silver_record_id,
            indicator_code=source.indicator_code,
            country_code=source.country_code,
            period=source.period,
            dq_score=source.dq_score,
            decision=OversightDecision.REJECTED,
            decided_by=rejected_by,
            notes=notes,
        )
        logger.info(
            "oversight_rejected",
            silver_record_id=silver_record_id,
            rejected_by=rejected_by,
            action="reject",
        )
        return approval

    def get_pending_reviews(self) -> list[dict]:
        # Find all silver_record_ids whose latest approval decision is PENDING_REVIEW
        pending_approvals = (
            self._db.query(OversightApproval)
            .filter(OversightApproval.decision == OversightDecision.PENDING_REVIEW.value)
            .order_by(OversightApproval.decided_at.desc())
            .all()
        )
        # Deduplicate by silver_record_id (keep latest pending per record)
        seen: set[str] = set()
        results: list[dict] = []
        for ap in pending_approvals:
            sid = str(ap.silver_record_id)
            if sid in seen:
                continue
            # Check if a later APPROVED/REJECTED decision exists
            later = (
                self._db.query(OversightApproval)
                .filter(
                    OversightApproval.silver_record_id == ap.silver_record_id,
                    OversightApproval.decided_at       > ap.decided_at,
                    OversightApproval.decision.in_([
                        OversightDecision.APPROVED.value,
                        OversightDecision.REJECTED.value,
                    ]),
                )
                .first()
            )
            if later is not None:
                seen.add(sid)
                continue

            seen.add(sid)
            silver = (
                self._db.query(SilverRecord)
                .filter(SilverRecord.record_id == ap.silver_record_id)
                .first()
            )
            results.append({
                "approval_id":      str(ap.approval_id),
                "silver_record_id": sid,
                "indicator_code":   ap.indicator_code,
                "country_code":     ap.country_code,
                "period":           ap.period,
                "dq_score":         ap.dq_score,
                "decision":         ap.decision,
                "decided_at":       ap.decided_at.isoformat(),
                "silver_value":     silver.value if silver else None,
                "source_code":      silver.source_code if silver else None,
            })
        return results

    def _find_pending(self, silver_record_id: str) -> Optional[OversightApproval]:
        return (
            self._db.query(OversightApproval)
            .filter(
                OversightApproval.silver_record_id == uuid.UUID(silver_record_id),
                OversightApproval.decision         == OversightDecision.PENDING_REVIEW.value,
            )
            .order_by(OversightApproval.decided_at.desc())
            .first()
        )

    def _write_approval(
        self,
        silver_record_id: str,
        indicator_code: str,
        country_code: str,
        period: str,
        dq_score: float,
        decision: OversightDecision,
        decided_by: str,
        notes: str,
    ) -> OversightApproval:
        approval = OversightApproval(
            approval_id=uuid.uuid4(),
            silver_record_id=uuid.UUID(silver_record_id),
            indicator_code=indicator_code,
            country_code=country_code,
            period=period,
            dq_score=dq_score,
            decision=decision.value,
            decided_by=decided_by,
            decided_at=datetime.utcnow(),
            notes=notes if notes else None,
            is_immutable=True,
        )
        self._db.add(approval)
        self._db.commit()
        self._db.refresh(approval)
        return approval


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class OversightActionRequest(BaseModel):
    notes: str = ""


# ── FastAPI router ─────────────────────────────────────────────────────────────

router = APIRouter(prefix="/api/oversight", tags=["oversight"])


@router.post("/approve/{record_id}")
def approve_record(
    record_id: str,
    body: OversightActionRequest,
    db: Session = Depends(get_db),
):
    gate = HumanOversightGate(db)
    try:
        approval = gate.approve(record_id, approved_by="data_governance_user", notes=body.notes)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {
        "approval_id":      str(approval.approval_id),
        "silver_record_id": str(approval.silver_record_id),
        "decision":         approval.decision,
        "decided_by":       approval.decided_by,
        "decided_at":       approval.decided_at.isoformat(),
    }


@router.post("/reject/{record_id}")
def reject_record(
    record_id: str,
    body: OversightActionRequest,
    db: Session = Depends(get_db),
):
    gate = HumanOversightGate(db)
    try:
        approval = gate.reject(record_id, rejected_by="data_governance_user", notes=body.notes)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {
        "approval_id":      str(approval.approval_id),
        "silver_record_id": str(approval.silver_record_id),
        "decision":         approval.decision,
        "decided_by":       approval.decided_by,
        "decided_at":       approval.decided_at.isoformat(),
    }


@router.get("/pending")
def get_pending(db: Session = Depends(get_db)):
    gate = HumanOversightGate(db)
    return gate.get_pending_reviews()
