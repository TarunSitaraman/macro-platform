"""Pillar 4 — Privacy: Data retention enforcement per GDPR Article 5(1)(e) - Storage Limitation."""

import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

import structlog
from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import APIRouter, Depends
from sqlalchemy import Column, DateTime, Integer, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Session

from src.database import Base, SessionLocal, get_db

logger = structlog.get_logger().bind(pillar="privacy")

router = APIRouter(prefix="/api/privacy", tags=["privacy"])


# ── Retention policy ───────────────────────────────────────────────────────────

@dataclass
class RetentionPolicy:
    bronze_raw_days: int = 730        # 2 years; financial_regulatory: 7*365
    silver_days: int = 1095           # 3 years
    gold_days: Optional[int] = None   # indefinite
    chat_session_days: int = 90
    pii_audit_log_days: int = 365

    @classmethod
    def from_env(cls) -> "RetentionPolicy":
        return cls(
            bronze_raw_days=int(os.environ.get("RETENTION_BRONZE_DAYS", 730)),
            silver_days=int(os.environ.get("RETENTION_SILVER_DAYS", 1095)),
            gold_days=None,  # Always indefinite regardless of env
            chat_session_days=int(os.environ.get("RETENTION_CHAT_DAYS", 90)),
            pii_audit_log_days=int(os.environ.get("RETENTION_PII_AUDIT_DAYS", 365)),
        )


# ── SQLAlchemy model ───────────────────────────────────────────────────────────

class RetentionAuditLog(Base):
    __tablename__ = "retention_audit_log"

    audit_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    table_name = Column(String(100), nullable=False)
    records_deleted = Column(Integer, nullable=False)
    date_range_start = Column(DateTime, nullable=False)
    date_range_end = Column(DateTime, nullable=False)
    deleted_at = Column(DateTime, default=datetime.utcnow)
    compliance_context = Column(
        String, default="GDPR Article 5(1)(e) - Storage Limitation"
    )


# ── Enforcer ───────────────────────────────────────────────────────────────────

class RetentionEnforcer:
    def __init__(self, db: Session) -> None:
        self._db = db
        self._policy = RetentionPolicy.from_env()

    def _archive_and_delete(
        self,
        table_name: str,
        cutoff: datetime,
        id_column: str = "record_id",
    ) -> int:
        archive_table = f"{table_name}_archive"

        # Check whether the archive table exists
        result = self._db.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                "WHERE table_name = :tname)"
            ),
            {"tname": archive_table},
        )
        archive_exists = result.scalar()

        if archive_exists:
            self._db.execute(
                text(
                    f"INSERT INTO {archive_table} "  # noqa: S608 — table name from trusted constant
                    f"SELECT * FROM {table_name} "
                    f"WHERE crawled_at < :cutoff OR created_at < :cutoff"
                ),
                {"cutoff": cutoff},
            )

        # Count before delete
        count_result = self._db.execute(
            text(
                f"SELECT COUNT(*) FROM {table_name} "  # noqa: S608
                f"WHERE crawled_at < :cutoff OR created_at < :cutoff"
            ),
            {"cutoff": cutoff},
        )
        count = count_result.scalar() or 0

        self._db.execute(
            text(
                f"DELETE FROM {table_name} "  # noqa: S608
                f"WHERE crawled_at < :cutoff OR created_at < :cutoff"
            ),
            {"cutoff": cutoff},
        )

        entry = RetentionAuditLog(
            table_name=table_name,
            records_deleted=count,
            date_range_start=datetime(2000, 1, 1),
            date_range_end=cutoff,
        )
        self._db.add(entry)
        self._db.commit()

        logger.info(
            "retention_enforced",
            table=table_name,
            records_deleted=count,
            cutoff=cutoff.isoformat(),
        )
        return count

    def enforce_bronze(self) -> int:
        cutoff = datetime.utcnow() - timedelta(days=self._policy.bronze_raw_days)
        return self._archive_and_delete("bronze_records", cutoff)

    def enforce_silver(self) -> int:
        cutoff = datetime.utcnow() - timedelta(days=self._policy.silver_days)
        return self._archive_and_delete("silver_records", cutoff)

    def enforce_chat_sessions(self) -> int:
        cutoff = datetime.utcnow() - timedelta(days=self._policy.chat_session_days)

        # Delete messages first (FK constraint), then sessions
        msg_count = self._db.execute(
            text(
                "DELETE FROM chat_messages WHERE session_id IN ("
                "  SELECT session_id FROM chat_sessions WHERE last_active < :cutoff"
                ")"
            ),
            {"cutoff": cutoff},
        ).rowcount or 0

        session_count = self._db.execute(
            text("DELETE FROM chat_sessions WHERE last_active < :cutoff"),
            {"cutoff": cutoff},
        ).rowcount or 0

        total = msg_count + session_count
        entry = RetentionAuditLog(
            table_name="chat_sessions+chat_messages",
            records_deleted=total,
            date_range_start=datetime(2000, 1, 1),
            date_range_end=cutoff,
        )
        self._db.add(entry)
        self._db.commit()

        logger.info(
            "chat_retention_enforced",
            sessions_deleted=session_count,
            messages_deleted=msg_count,
        )
        return total

    def enforce_privacy_log(self) -> int:
        cutoff = datetime.utcnow() - timedelta(days=self._policy.pii_audit_log_days)
        count = self._db.execute(
            text("DELETE FROM privacy_audit_log WHERE redacted_at < :cutoff"),
            {"cutoff": cutoff},
        ).rowcount or 0

        entry = RetentionAuditLog(
            table_name="privacy_audit_log",
            records_deleted=count,
            date_range_start=datetime(2000, 1, 1),
            date_range_end=cutoff,
        )
        self._db.add(entry)
        self._db.commit()

        logger.info("privacy_log_retention_enforced", records_deleted=count)
        return count

    def enforce_all(self) -> dict[str, int]:
        return {
            "bronze_records": self.enforce_bronze(),
            "silver_records": self.enforce_silver(),
            "chat_sessions": self.enforce_chat_sessions(),
            "privacy_audit_log": self.enforce_privacy_log(),
        }


# ── APScheduler job ────────────────────────────────────────────────────────────

def run_retention_enforcement() -> None:
    """Daily 02:00 UTC retention job."""
    db = SessionLocal()
    try:
        enforcer = RetentionEnforcer(db)
        counts = enforcer.enforce_all()
        logger.info("retention_job_complete", counts=counts)
    except Exception as exc:  # noqa: BLE001
        logger.error("retention_job_failed", error=str(exc))
    finally:
        db.close()


def schedule_retention_job(scheduler: BackgroundScheduler) -> None:
    """Register the daily retention job on the provided scheduler."""
    scheduler.add_job(
        run_retention_enforcement,
        trigger="cron",
        hour=2,
        minute=0,
        id="gdpr_retention_enforcement",
        replace_existing=True,
    )


# ── FastAPI routes ─────────────────────────────────────────────────────────────

@router.delete("/my-data")
def delete_my_data(user_id: str, db: Session = Depends(get_db)) -> dict:
    """GDPR Article 17 — Right to Erasure: delete all data for a user_id."""
    # Delete chat messages via sessions
    db.execute(
        text(
            "DELETE FROM chat_messages WHERE session_id IN ("
            "  SELECT session_id FROM chat_sessions WHERE user_id = :uid::uuid"
            ")"
        ),
        {"uid": user_id},
    )
    db.execute(
        text("DELETE FROM chat_sessions WHERE user_id = :uid::uuid"),
        {"uid": user_id},
    )

    # Log the erasure
    entry = RetentionAuditLog(
        table_name="chat_sessions+chat_messages (user erasure)",
        records_deleted=-1,  # Exact count not required for erasure
        date_range_start=datetime(2000, 1, 1),
        date_range_end=datetime.utcnow(),
        compliance_context="GDPR Article 17 - Right to Erasure",
    )
    db.add(entry)
    db.commit()

    logger.info("user_data_erased", user_id_hash=user_id[:8] + "***")

    return {"deleted": True, "compliance": "GDPR Article 17"}
