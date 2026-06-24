"""Pillar 1 — Reliability: Extraction confidence threshold decisions with audit trail.

Satisfies INTERNAL data governance policy.
"""

import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

import structlog
from fastapi import APIRouter, Depends
from sqlalchemy import Column, DateTime, Float, Integer, String, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Session

from src.database import Base, get_db

logger = structlog.get_logger().bind(pillar="reliability")

router = APIRouter(prefix="/api/thresholds", tags=["thresholds"])


# ── Enums ─────────────────────────────────────────────────────────────────────

class ThresholdDecision(str, Enum):
    AUTO_ACCEPT = "AUTO_ACCEPT"
    QUEUE_REVIEW = "QUEUE_REVIEW"
    REJECT = "REJECT"


# ── Threshold configuration ───────────────────────────────────────────────────

@dataclass
class ExtractionThreshold:
    auto_accept: float = 0.85
    review_low: float = 0.70
    reject_below: float = 0.70

    @classmethod
    def from_env(cls) -> "ExtractionThreshold":
        """Load threshold values from environment variables with defaults."""
        auto_accept = float(os.environ.get("AUTO_ACCEPT_THRESHOLD", "0.85"))
        review_low = float(os.environ.get("REVIEW_THRESHOLD_LOW", "0.70"))
        # reject_below mirrors review_low — anything below the review band is rejected
        return cls(
            auto_accept=auto_accept,
            review_low=review_low,
            reject_below=review_low,
        )


# ── SQLAlchemy model ──────────────────────────────────────────────────────────

class ExtractionDecision(Base):
    __tablename__ = "extraction_decisions"

    decision_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    indicator_code = Column(String(100), nullable=True, index=True)
    source_code = Column(String(50), nullable=True, index=True)
    confidence = Column(Float, nullable=False)
    decision = Column(String(20), nullable=False)  # ThresholdDecision.value
    decided_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    compliance_context = Column(
        String(200),
        default="INTERNAL - Data Quality Gate",
        nullable=False,
    )


# ── Core evaluation function ──────────────────────────────────────────────────

def evaluate_extraction(
    confidence: float,
    indicator_code: Optional[str] = None,
    source_code: Optional[str] = None,
    db: Optional[Session] = None,
) -> ThresholdDecision:
    """Classify an extraction confidence score and optionally write an audit row.

    Decision logic:
      confidence >= auto_accept  → AUTO_ACCEPT
      review_low <= confidence < auto_accept → QUEUE_REVIEW
      confidence < reject_below  → REJECT
    """
    thresholds = ExtractionThreshold.from_env()

    if confidence >= thresholds.auto_accept:
        decision = ThresholdDecision.AUTO_ACCEPT
    elif confidence >= thresholds.review_low:
        decision = ThresholdDecision.QUEUE_REVIEW
    else:
        decision = ThresholdDecision.REJECT

    logger.info(
        "extraction_decision",
        confidence=confidence,
        decision=decision.value,
        indicator_code=indicator_code,
        source_code=source_code,
        auto_accept_threshold=thresholds.auto_accept,
        review_low_threshold=thresholds.review_low,
    )

    if db is not None:
        record = ExtractionDecision(
            indicator_code=indicator_code,
            source_code=source_code,
            confidence=confidence,
            decision=decision.value,
            decided_at=datetime.utcnow(),
        )
        db.add(record)
        db.commit()

    return decision


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/config")
def get_threshold_config():
    """Return the current threshold configuration as loaded from environment."""
    thresholds = ExtractionThreshold.from_env()
    return {
        "auto_accept": thresholds.auto_accept,
        "review_low": thresholds.review_low,
        "reject_below": thresholds.reject_below,
        "env_vars": {
            "AUTO_ACCEPT_THRESHOLD": os.environ.get("AUTO_ACCEPT_THRESHOLD", "(default 0.85)"),
            "REVIEW_THRESHOLD_LOW": os.environ.get("REVIEW_THRESHOLD_LOW", "(default 0.70)"),
        },
        "compliance_context": "INTERNAL - Data Quality Gate",
    }


@router.get("/stats")
def get_threshold_stats(db: Session = Depends(get_db)):
    """Return distribution of extraction decisions over the last 7 days."""
    cutoff = text(
        "decided_at >= NOW() - INTERVAL '7 days'"
    )
    rows = (
        db.execute(
            text(
                "SELECT decision, COUNT(*) AS cnt, "
                "       AVG(confidence) AS avg_confidence, "
                "       MIN(confidence) AS min_confidence, "
                "       MAX(confidence) AS max_confidence "
                "FROM extraction_decisions "
                "WHERE decided_at >= NOW() - INTERVAL '7 days' "
                "GROUP BY decision"
            )
        ).fetchall()
    )

    total = sum(r[1] for r in rows)
    distribution = []
    for r in rows:
        decision_val, cnt, avg_conf, min_conf, max_conf = r
        distribution.append({
            "decision": decision_val,
            "count": cnt,
            "pct": round(cnt / total * 100, 1) if total > 0 else 0.0,
            "avg_confidence": round(float(avg_conf), 4) if avg_conf is not None else None,
            "min_confidence": round(float(min_conf), 4) if min_conf is not None else None,
            "max_confidence": round(float(max_conf), 4) if max_conf is not None else None,
        })

    thresholds = ExtractionThreshold.from_env()
    return {
        "period_days": 7,
        "total_decisions": total,
        "distribution": distribution,
        "current_thresholds": {
            "auto_accept": thresholds.auto_accept,
            "review_low": thresholds.review_low,
            "reject_below": thresholds.reject_below,
        },
        "compliance_context": "INTERNAL - Data Quality Gate",
    }
