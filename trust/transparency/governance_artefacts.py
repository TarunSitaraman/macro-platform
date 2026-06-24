"""Pillar 8 — Transparency: Versioned governance policies for extraction thresholds and data governance.
Satisfies INTERNAL governance framework.
"""

import uuid
from datetime import datetime
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import Boolean, Column, DateTime, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Session

from src.database import Base, get_db

logger = structlog.get_logger().bind(pillar="transparency")

SEED_POLICY: dict = {
    "policy_name": "Extraction Threshold Policy",
    "version":     "1.0.0",
    "approved_by": "Data Governance Lead",
    "content": {
        "auto_accept_threshold":  0.85,
        "review_threshold_low":   0.70,
        "reject_below":           0.70,
        "description": (
            "Records with confidence >= 0.85 are auto-promoted to Silver. "
            "Records 0.70-0.84 are queued for manual review. "
            "Records below 0.70 are rejected."
        ),
        "applicable_regulations": ["SOX", "MiFID II", "INTERNAL"],
    },
}

_DEFAULT_THRESHOLDS = {
    "auto_accept_threshold": 0.85,
    "review_threshold_low":  0.70,
    "reject_below":          0.70,
}


class GovernancePolicy(Base):
    __tablename__ = "governance_policies"

    policy_id          = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    policy_name        = Column(String(200), nullable=False)
    version            = Column(String(20),  nullable=False)
    effective_date     = Column(DateTime,    nullable=False)
    approved_by        = Column(String(100), nullable=False)
    content            = Column(JSONB,        nullable=False)
    is_active          = Column(Boolean,     default=True)
    created_at         = Column(DateTime,    default=datetime.utcnow)
    compliance_context = Column(String,      default="INTERNAL - Governance Framework")

    __table_args__ = (
        Index("ix_governance_policies_name_version", "policy_name", "version"),
        UniqueConstraint("policy_name", "version", name="uq_policy_name_version"),
    )


class PolicyManager:
    def __init__(self, db: Session) -> None:
        self._db = db

    def seed_default_policy(self) -> GovernancePolicy:
        existing = (
            self._db.query(GovernancePolicy)
            .filter(
                GovernancePolicy.policy_name == SEED_POLICY["policy_name"],
                GovernancePolicy.version     == SEED_POLICY["version"],
            )
            .first()
        )
        if existing is not None:
            return existing

        policy = GovernancePolicy(
            policy_id=uuid.uuid4(),
            policy_name=SEED_POLICY["policy_name"],
            version=SEED_POLICY["version"],
            effective_date=datetime.utcnow(),
            approved_by=SEED_POLICY["approved_by"],
            content=SEED_POLICY["content"],
            is_active=True,
            created_at=datetime.utcnow(),
        )
        self._db.add(policy)
        self._db.commit()
        self._db.refresh(policy)
        logger.info("governance_policy_seeded", policy_name=policy.policy_name, version=policy.version)
        return policy

    def get_active_policy(self, policy_name: str) -> Optional[GovernancePolicy]:
        return (
            self._db.query(GovernancePolicy)
            .filter(
                GovernancePolicy.policy_name == policy_name,
                GovernancePolicy.is_active   == True,  # noqa: E712
            )
            .order_by(GovernancePolicy.effective_date.desc())
            .first()
        )

    def get_thresholds(self) -> dict:
        policy = self.get_active_policy("Extraction Threshold Policy")
        if policy is None:
            return _DEFAULT_THRESHOLDS
        content = policy.content or {}
        return {
            "auto_accept_threshold": content.get("auto_accept_threshold", _DEFAULT_THRESHOLDS["auto_accept_threshold"]),
            "review_threshold_low":  content.get("review_threshold_low",  _DEFAULT_THRESHOLDS["review_threshold_low"]),
            "reject_below":          content.get("reject_below",          _DEFAULT_THRESHOLDS["reject_below"]),
        }

    def list_policies(self) -> list[GovernancePolicy]:
        return (
            self._db.query(GovernancePolicy)
            .filter(GovernancePolicy.is_active == True)  # noqa: E712
            .order_by(GovernancePolicy.policy_name, GovernancePolicy.effective_date.desc())
            .all()
        )

    def publish_policy(
        self,
        policy_name: str,
        version: str,
        content: dict,
        approved_by: str,
    ) -> GovernancePolicy:
        # Deactivate all existing versions of the same policy
        self._db.query(GovernancePolicy).filter(
            GovernancePolicy.policy_name == policy_name,
            GovernancePolicy.is_active   == True,  # noqa: E712
        ).update({"is_active": False})

        new_policy = GovernancePolicy(
            policy_id=uuid.uuid4(),
            policy_name=policy_name,
            version=version,
            effective_date=datetime.utcnow(),
            approved_by=approved_by,
            content=content,
            is_active=True,
            created_at=datetime.utcnow(),
        )
        self._db.add(new_policy)
        self._db.commit()
        self._db.refresh(new_policy)
        logger.info(
            "governance_policy_published",
            policy_name=policy_name,
            version=version,
            approved_by=approved_by,
        )
        return new_policy


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class PublishPolicyRequest(BaseModel):
    policy_name: str
    version:     str
    content:     dict
    approved_by: str


# ── FastAPI router ─────────────────────────────────────────────────────────────

router = APIRouter(prefix="/api/governance", tags=["governance"])


@router.get("/policies")
def list_policies(db: Session = Depends(get_db)):
    manager = PolicyManager(db)
    policies = manager.list_policies()
    return [
        {
            "policy_id":    str(p.policy_id),
            "policy_name":  p.policy_name,
            "version":      p.version,
            "effective_date": p.effective_date.isoformat(),
            "approved_by":  p.approved_by,
            "content":      p.content,
            "is_active":    p.is_active,
            "created_at":   p.created_at.isoformat(),
        }
        for p in policies
    ]


@router.post("/policies")
def publish_policy(
    body: PublishPolicyRequest,
    db: Session = Depends(get_db),
):
    manager = PolicyManager(db)
    try:
        policy = manager.publish_policy(
            policy_name=body.policy_name,
            version=body.version,
            content=body.content,
            approved_by=body.approved_by,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {
        "policy_id":    str(policy.policy_id),
        "policy_name":  policy.policy_name,
        "version":      policy.version,
        "effective_date": policy.effective_date.isoformat(),
        "approved_by":  policy.approved_by,
        "is_active":    policy.is_active,
    }
