"""Pillar 4 — Privacy: User consent management per GDPR Article 7 - Conditions for Consent."""

import hashlib
import uuid
from datetime import date, datetime
from enum import Enum

import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import Column, DateTime, Index, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Session

from src.database import Base, get_db

logger = structlog.get_logger().bind(pillar="privacy")

router = APIRouter(prefix="/api/privacy/consent", tags=["privacy", "consent"])


# ── Enum ───────────────────────────────────────────────────────────────────────

class ConsentType(str, Enum):
    SESSION_HISTORY = "SESSION_HISTORY"
    ANALYTICS = "ANALYTICS"
    PREFERENCES = "PREFERENCES"


# ── SQLAlchemy model ───────────────────────────────────────────────────────────

class ConsentRecord(Base):
    __tablename__ = "user_consents"

    consent_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # sha256 of user_id — raw user_id never stored
    user_id_hash = Column(String(64), nullable=False)
    consent_type = Column(String(50), nullable=False)
    granted_at = Column(DateTime, nullable=True)
    revoked_at = Column(DateTime, nullable=True)
    # sha256(ip + daily_salt) — raw IP never stored
    ip_hash = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    compliance_context = Column(String, default="GDPR Article 7 - Consent")

    __table_args__ = (
        Index("ix_user_consents_user_type", "user_id_hash", "consent_type"),
    )


# ── Helpers ────────────────────────────────────────────────────────────────────

def _hash_user_id(user_id: str) -> str:
    return hashlib.sha256(user_id.encode()).hexdigest()


def _hash_ip(ip: str) -> str:
    daily_salt = date.today().isoformat()
    return hashlib.sha256(f"{ip}{daily_salt}".encode()).hexdigest()


# ── Manager ────────────────────────────────────────────────────────────────────

class ConsentManager:
    def __init__(self, db: Session) -> None:
        self._db = db

    def grant(
        self,
        user_id: str,
        consent_type: ConsentType,
        ip: str = "",
    ) -> ConsentRecord:
        user_id_hash = _hash_user_id(user_id)
        record = (
            self._db.query(ConsentRecord)
            .filter_by(user_id_hash=user_id_hash, consent_type=consent_type.value)
            .first()
        )
        if record is None:
            record = ConsentRecord(
                user_id_hash=user_id_hash,
                consent_type=consent_type.value,
                ip_hash=_hash_ip(ip) if ip else None,
            )
            self._db.add(record)

        record.granted_at = datetime.utcnow()
        record.revoked_at = None
        if ip:
            record.ip_hash = _hash_ip(ip)

        self._db.commit()
        self._db.refresh(record)

        logger.info(
            "consent_granted",
            consent_type=consent_type.value,
            user_hash=user_id_hash[:8] + "***",
        )
        return record

    def revoke(self, user_id: str, consent_type: ConsentType) -> ConsentRecord:
        user_id_hash = _hash_user_id(user_id)
        record = (
            self._db.query(ConsentRecord)
            .filter_by(user_id_hash=user_id_hash, consent_type=consent_type.value)
            .first()
        )
        if record is None:
            record = ConsentRecord(
                user_id_hash=user_id_hash,
                consent_type=consent_type.value,
            )
            self._db.add(record)

        record.revoked_at = datetime.utcnow()
        self._db.commit()
        self._db.refresh(record)

        logger.info(
            "consent_revoked",
            consent_type=consent_type.value,
            user_hash=user_id_hash[:8] + "***",
        )
        return record

    def check(self, user_id: str, consent_type: ConsentType) -> bool:
        user_id_hash = _hash_user_id(user_id)
        record = (
            self._db.query(ConsentRecord)
            .filter_by(user_id_hash=user_id_hash, consent_type=consent_type.value)
            .first()
        )
        if record is None:
            return False
        return record.granted_at is not None and record.revoked_at is None


# ── Pydantic schemas ───────────────────────────────────────────────────────────

class ConsentRequest(BaseModel):
    user_id: str
    consent_type: ConsentType


# ── FastAPI routes ─────────────────────────────────────────────────────────────

@router.post("/grant")
def grant_consent(
    body: ConsentRequest,
    db: Session = Depends(get_db),
) -> dict:
    manager = ConsentManager(db)
    manager.grant(body.user_id, body.consent_type)
    return {"granted": True, "consent_type": body.consent_type.value}


@router.post("/revoke")
def revoke_consent(
    body: ConsentRequest,
    db: Session = Depends(get_db),
) -> dict:
    manager = ConsentManager(db)
    manager.revoke(body.user_id, body.consent_type)
    return {"revoked": True, "consent_type": body.consent_type.value}


@router.get("/status")
def consent_status(
    user_id: str,
    consent_type: ConsentType,
    db: Session = Depends(get_db),
) -> dict:
    manager = ConsentManager(db)
    granted = manager.check(user_id, consent_type)
    return {"granted": granted, "consent_type": consent_type.value}
