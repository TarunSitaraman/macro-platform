"""Pillar 9 — Fairness: Personalization bias detection for cross-session diversity.
Satisfies INTERNAL fairness policy.
"""

import hashlib
import uuid
from datetime import datetime
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import Column, DateTime, Integer, String
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Session

from src.database import Base, ChatMessage, ChatSession, GoldRecord, get_db

logger = structlog.get_logger().bind(pillar="fairness")

REGIONS: dict[str, str] = {
    "USA": "Americas",
    "CAN": "Americas",
    "MEX": "Americas",
    "BRA": "Americas",
    "ARG": "Americas",
    "GBR": "Europe",
    "DEU": "Europe",
    "FRA": "Europe",
    "ITA": "Europe",
    "ESP": "Europe",
    "NLD": "Europe",
    "JPN": "Asia-Pacific",
    "CHN": "Asia-Pacific",
    "IND": "Asia-Pacific",
    "KOR": "Asia-Pacific",
    "AUS": "Asia-Pacific",
    "IDN": "Asia-Pacific",
    "SAU": "Middle East & Africa",
    "ZAF": "Middle East & Africa",
    "TUR": "Middle East & Africa",
}

_ALL_REGIONS = sorted(set(REGIONS.values()))


class BiasAlert(Base):
    __tablename__ = "bias_alerts"

    alert_id               = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id_hash           = Column(String(64),  nullable=False)
    session_count          = Column(Integer,      nullable=False)
    dominant_region        = Column(String(100),  nullable=False)
    other_regions_missing  = Column(ARRAY(String), nullable=False)
    alerted_at             = Column(DateTime,     default=datetime.utcnow)
    compliance_context     = Column(String,       default="INTERNAL - Fairness Policy")


class PersonalizationBiasMonitor:
    def __init__(self, db: Session) -> None:
        self._db = db

    def _hash_user(self, user_id: str) -> str:
        return hashlib.sha256(user_id.encode()).hexdigest()

    def analyze_user_sessions(
        self,
        user_id: str,
        session_limit: int = 30,
    ) -> Optional[BiasAlert]:
        user_id_hash = self._hash_user(user_id)

        # Fetch last N sessions for this user
        sessions = (
            self._db.query(ChatSession)
            .filter(ChatSession.user_id == uuid.UUID(user_id))
            .order_by(ChatSession.last_active.desc())
            .limit(session_limit)
            .all()
        )
        if not sessions:
            return None

        session_ids = [s.session_id for s in sessions]

        # Find country_codes accessed via context_records_used in chat messages
        messages = (
            self._db.query(ChatMessage)
            .filter(ChatMessage.session_id.in_(session_ids))
            .filter(ChatMessage.context_records_used != None)  # noqa: E711
            .all()
        )

        # Collect context record IDs (stored as UUID array)
        record_ids: list[str] = []
        for msg in messages:
            if msg.context_records_used:
                record_ids.extend([str(r) for r in msg.context_records_used])

        regions_seen: set[str] = set()
        if record_ids:
            gold_rows = (
                self._db.query(GoldRecord.country_code)
                .filter(GoldRecord.record_id.in_([uuid.UUID(r) for r in record_ids]))
                .distinct()
                .all()
            )
            for row in gold_rows:
                region = REGIONS.get(row.country_code)
                if region:
                    regions_seen.add(region)

        if len(regions_seen) != 1:
            # Either no data or diverse access — no bias alert
            return None

        dominant_region = next(iter(regions_seen))
        missing_regions = [r for r in _ALL_REGIONS if r != dominant_region]

        alert = BiasAlert(
            alert_id=uuid.uuid4(),
            user_id_hash=user_id_hash,
            session_count=len(sessions),
            dominant_region=dominant_region,
            other_regions_missing=missing_regions,
            alerted_at=datetime.utcnow(),
        )
        self._db.add(alert)
        self._db.commit()
        self._db.refresh(alert)

        logger.warning(
            "personalization_bias_detected",
            user_id_hash=user_id_hash,
            dominant_region=dominant_region,
            session_count=len(sessions),
            missing_regions=missing_regions,
        )
        return alert

    def generate_diversity_nudge(self, dominant_region: str) -> str:
        other_regions = [r for r in _ALL_REGIONS if r != dominant_region]
        sample = other_regions[:2]
        return (
            f"You've been exploring {dominant_region} data. Consider also looking at "
            f"{', '.join(sample)} indicators for a broader perspective."
        )


# ── FastAPI router ─────────────────────────────────────────────────────────────

router = APIRouter(prefix="/api/fairness", tags=["fairness"])


@router.get("/bias-check")
def bias_check(user_id: str, db: Session = Depends(get_db)):
    monitor = PersonalizationBiasMonitor(db)
    try:
        alert = monitor.analyze_user_sessions(user_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if alert is None:
        return {
            "bias_detected":    False,
            "user_id_hash":     monitor._hash_user(user_id),
            "dominant_region":  None,
            "missing_regions":  [],
            "diversity_nudge":  None,
            "alerted_at":       None,
        }
    return {
        "bias_detected":   True,
        "user_id_hash":    alert.user_id_hash,
        "session_count":   alert.session_count,
        "dominant_region": alert.dominant_region,
        "missing_regions": alert.other_regions_missing,
        "diversity_nudge": monitor.generate_diversity_nudge(alert.dominant_region),
        "alerted_at":      alert.alerted_at.isoformat(),
    }
