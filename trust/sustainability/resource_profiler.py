"""Pillar 5 — Sustainability: Database resource profiling job.
Satisfies INTERNAL infrastructure governance."""

import uuid
from datetime import datetime, timedelta

import structlog
from fastapi import APIRouter, Depends
from sqlalchemy import BigInteger, Column, DateTime, Integer, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Session

from src.database import Base, SessionLocal, get_db

logger = structlog.get_logger().bind(pillar="sustainability")


# ── SQLAlchemy Model ──────────────────────────────────────────────────────────

class ResourceMetric(Base):
    __tablename__ = "resource_metrics"

    metric_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    db_size_bytes = Column(BigInteger, nullable=False)
    pgvector_rows = Column(Integer, nullable=False)
    bronze_rows = Column(Integer, nullable=False)
    silver_rows = Column(Integer, nullable=False)
    gold_rows = Column(Integer, nullable=False)
    review_queue_rows = Column(Integer, nullable=False)
    measured_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    compliance_context = Column(
        String, default="INTERNAL - Infrastructure Governance", nullable=False
    )


# ── ResourceProfiler ──────────────────────────────────────────────────────────

_TABLES_TO_SIZE = [
    "bronze_records",
    "silver_records",
    "gold_records",
    "review_queue",
    "source_config",
    "cost_tracking",
    "crawl_opt_log",
    "explainability_log",
    "llm_extraction_traces",
    "resource_metrics",
]


class ResourceProfiler:
    def __init__(self, db: Session) -> None:
        self._db = db

    def measure(self) -> ResourceMetric:
        # Total DB size across key tables
        tables_sql = ", ".join(f"'public.{t}'" for t in _TABLES_TO_SIZE)
        size_result = self._db.execute(
            text(
                f"SELECT COALESCE(SUM(pg_total_relation_size(t)), 0) AS total_size "
                f"FROM unnest(ARRAY[{tables_sql}]::regclass[]) AS t"
            )
        ).fetchone()
        db_size_bytes = int(size_result[0]) if size_result else 0

        bronze_rows = self._count("SELECT COUNT(*) FROM bronze_records")
        silver_rows = self._count("SELECT COUNT(*) FROM silver_records")
        gold_rows = self._count("SELECT COUNT(*) FROM gold_records")
        review_queue_rows = self._count("SELECT COUNT(*) FROM review_queue")
        pgvector_rows = self._count(
            "SELECT COUNT(*) FROM gold_records WHERE embedding IS NOT NULL"
        )

        metric = ResourceMetric(
            db_size_bytes=db_size_bytes,
            pgvector_rows=pgvector_rows,
            bronze_rows=bronze_rows,
            silver_rows=silver_rows,
            gold_rows=gold_rows,
            review_queue_rows=review_queue_rows,
        )
        self._db.add(metric)
        self._db.commit()
        self._db.refresh(metric)

        logger.info(
            "resource_metric_recorded",
            db_size_bytes=db_size_bytes,
            gold_rows=gold_rows,
            pgvector_rows=pgvector_rows,
        )
        return metric

    def _count(self, sql: str) -> int:
        try:
            result = self._db.execute(text(sql)).fetchone()
            return int(result[0]) if result else 0
        except Exception:
            return 0

    def get_trend(self, days: int = 30) -> list[dict]:
        since = datetime.utcnow() - timedelta(days=days)
        rows = (
            self._db.query(ResourceMetric)
            .filter(ResourceMetric.measured_at >= since)
            .order_by(ResourceMetric.measured_at.asc())
            .all()
        )
        return [
            {
                "date": r.measured_at.date().isoformat(),
                "db_size_bytes": r.db_size_bytes,
                "gold_rows": r.gold_rows,
                "silver_rows": r.silver_rows,
                "bronze_rows": r.bronze_rows,
                "pgvector_rows": r.pgvector_rows,
                "review_queue_rows": r.review_queue_rows,
            }
            for r in rows
        ]


# ── APScheduler Job ───────────────────────────────────────────────────────────

def profile_resources() -> None:
    """APScheduler job: profiles database resources every 6 hours."""
    db = SessionLocal()
    try:
        profiler = ResourceProfiler(db)
        metric = profiler.measure()
        logger.info(
            "scheduled_resource_profile_complete",
            metric_id=str(metric.metric_id),
            db_size_bytes=metric.db_size_bytes,
        )
    except Exception as exc:
        logger.error("scheduled_resource_profile_failed", error=str(exc))
    finally:
        db.close()


# ── FastAPI Router ────────────────────────────────────────────────────────────

router = APIRouter(prefix="/api/sustainability/resources", tags=["sustainability"])


@router.get("/metrics")
def get_resource_metrics(
    days: int = 30,
    db: Session = Depends(get_db),
) -> dict:
    profiler = ResourceProfiler(db)
    latest = (
        db.query(ResourceMetric)
        .order_by(ResourceMetric.measured_at.desc())
        .first()
    )
    trend = profiler.get_trend(days=days)
    latest_data = None
    if latest:
        latest_data = {
            "metric_id": str(latest.metric_id),
            "db_size_bytes": latest.db_size_bytes,
            "pgvector_rows": latest.pgvector_rows,
            "bronze_rows": latest.bronze_rows,
            "silver_rows": latest.silver_rows,
            "gold_rows": latest.gold_rows,
            "review_queue_rows": latest.review_queue_rows,
            "measured_at": latest.measured_at.isoformat(),
        }
    return {"latest": latest_data, "trend": trend}
