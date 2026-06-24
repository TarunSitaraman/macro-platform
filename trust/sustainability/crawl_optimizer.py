"""Pillar 5 — Sustainability: Crawl optimization via change detection and cost ceilings.
Satisfies INTERNAL resource governance."""

import hashlib
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, Query
from sqlalchemy import Column, DateTime, Index, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Session

from src.database import Base, SourceConfig, get_db

logger = structlog.get_logger().bind(pillar="sustainability")


# ── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass
class CrawlSkipEvent:
    source_url: str
    source_code: str
    reason: str
    skipped_at: datetime


@dataclass
class CrawlEfficiencyReport:
    total_crawls_scheduled: int
    crawls_skipped: int
    skip_rate_pct: float
    estimated_cost_saved_usd: float
    sources_deferred: list[str] = field(default_factory=list)


# ── SQLAlchemy Model ──────────────────────────────────────────────────────────

class CrawlOptLog(Base):
    __tablename__ = "crawl_opt_log"

    log_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_code = Column(String(50), nullable=False)
    action = Column(String(50), nullable=False)  # "SKIP" | "DEFER" | "PROCEED"
    reason = Column(String(500), nullable=False)
    content_hash = Column(String(64), nullable=True)
    logged_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    compliance_context = Column(
        String, default="INTERNAL - Resource Governance", nullable=False
    )

    __table_args__ = (
        Index("ix_crawl_opt_source_logged", "source_code", "logged_at"),
    )


# ── ContentHashStore ──────────────────────────────────────────────────────────

class ContentHashStore:
    def get_last_hash(self, source_code: str, db: Session) -> Optional[str]:
        row = (
            db.query(CrawlOptLog)
            .filter(
                CrawlOptLog.source_code == source_code,
                CrawlOptLog.content_hash.isnot(None),
            )
            .order_by(CrawlOptLog.logged_at.desc())
            .first()
        )
        return row.content_hash if row else None

    def store_hash(self, source_code: str, content_hash: str, db: Session) -> None:
        entry = CrawlOptLog(
            source_code=source_code,
            action="PROCEED",
            reason="content hash recorded",
            content_hash=content_hash,
        )
        db.add(entry)
        db.commit()


# ── CrawlOptimizer ────────────────────────────────────────────────────────────

class CrawlOptimizer:
    def __init__(self, db: Session) -> None:
        self._db = db
        self._hash_store = ContentHashStore()

    def compute_content_hash(self, content: str) -> str:
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    def should_crawl(
        self,
        source_code: str,
        etag: Optional[str] = None,
        last_modified: Optional[str] = None,
        content: Optional[str] = None,
    ) -> tuple[bool, str]:
        # Check ETag / Last-Modified stored in last PROCEED log's reason field
        last_log = (
            self._db.query(CrawlOptLog)
            .filter(
                CrawlOptLog.source_code == source_code,
                CrawlOptLog.action == "PROCEED",
            )
            .order_by(CrawlOptLog.logged_at.desc())
            .first()
        )

        if etag and last_log:
            if f"etag={etag}" in last_log.reason:
                self._log(source_code, "SKIP", "no change detected (etag match)")
                return False, "no change detected"

        if last_modified and last_log:
            if f"last_modified={last_modified}" in last_log.reason:
                self._log(source_code, "SKIP", "no change detected (last_modified match)")
                return False, "no change detected"

        if content is not None:
            new_hash = self.compute_content_hash(content)
            stored_hash = self._hash_store.get_last_hash(source_code, self._db)
            if stored_hash and stored_hash == new_hash:
                self._log(source_code, "SKIP", "no change detected (content hash match)")
                return False, "no change detected"
            # Store new hash
            self._hash_store.store_hash(source_code, new_hash, self._db)

        reason_parts = []
        if etag:
            reason_parts.append(f"etag={etag}")
        if last_modified:
            reason_parts.append(f"last_modified={last_modified}")
        reason = "change detected" + (f" ({', '.join(reason_parts)})" if reason_parts else "")
        self._log(source_code, "PROCEED", reason)
        return True, "change detected"

    def _log(self, source_code: str, action: str, reason: str) -> None:
        entry = CrawlOptLog(
            source_code=source_code,
            action=action,
            reason=reason,
        )
        self._db.add(entry)
        self._db.commit()
        logger.info("crawl_decision", source_code=source_code, action=action, reason=reason)

    def check_cost_ceiling(
        self,
        source_codes: list[str],
        estimated_cost_per_source: float,
    ) -> tuple[list[str], list[str]]:
        max_cost = float(os.getenv("MAX_CRAWL_COST_PER_WINDOW", "10.0"))

        # Sort by reputation_score DESC (higher reputation = higher priority)
        rows = (
            self._db.query(SourceConfig.source_code, SourceConfig.reputation_score)
            .filter(SourceConfig.source_code.in_(source_codes))
            .order_by(SourceConfig.reputation_score.desc())
            .all()
        )
        ranked = [r.source_code for r in rows]
        # Add any source_codes not found in source_config at the end
        known = {r.source_code for r in rows}
        ranked += [sc for sc in source_codes if sc not in known]

        to_crawl: list[str] = []
        deferred: list[str] = []
        running_cost = 0.0

        for sc in ranked:
            if running_cost + estimated_cost_per_source <= max_cost:
                to_crawl.append(sc)
                running_cost += estimated_cost_per_source
            else:
                deferred.append(sc)
                self._log(sc, "DEFER", f"cost ceiling ${max_cost:.2f} reached")

        return to_crawl, deferred

    def get_crawl_efficiency_report(self, days: int = 7) -> CrawlEfficiencyReport:
        since = datetime.utcnow() - timedelta(days=days)
        logs = (
            self._db.query(CrawlOptLog)
            .filter(CrawlOptLog.logged_at >= since)
            .all()
        )

        skips = [l for l in logs if l.action == "SKIP"]
        proceeds = [l for l in logs if l.action == "PROCEED"]
        deferred = [l for l in logs if l.action == "DEFER"]
        total = len(skips) + len(proceeds)
        skip_rate = (len(skips) / total * 100) if total > 0 else 0.0

        # Estimate cost saved: skips * avg playwright cost per crawl (assume 60s session)
        playwright_cost_per_second = float(os.getenv("PLAYWRIGHT_COST_PER_SECOND", "0.000001"))
        avg_crawl_cost = 60.0 * playwright_cost_per_second
        estimated_saved = len(skips) * avg_crawl_cost

        deferred_sources = list({l.source_code for l in deferred})

        return CrawlEfficiencyReport(
            total_crawls_scheduled=total,
            crawls_skipped=len(skips),
            skip_rate_pct=round(skip_rate, 2),
            estimated_cost_saved_usd=round(estimated_saved, 6),
            sources_deferred=deferred_sources,
        )


# ── FastAPI Router ────────────────────────────────────────────────────────────

router = APIRouter(prefix="/api/sustainability/crawler", tags=["sustainability"])


@router.get("/efficiency")
def get_efficiency(
    days: int = Query(default=7, ge=1, le=90),
    db: Session = Depends(get_db),
) -> dict:
    optimizer = CrawlOptimizer(db)
    report = optimizer.get_crawl_efficiency_report(days=days)
    return {
        "total_crawls_scheduled": report.total_crawls_scheduled,
        "crawls_skipped": report.crawls_skipped,
        "skip_rate_pct": report.skip_rate_pct,
        "estimated_cost_saved_usd": report.estimated_cost_saved_usd,
        "sources_deferred": report.sources_deferred,
    }


@router.get("/metrics")
def get_crawler_metrics(db: Session = Depends(get_db)) -> dict:
    from trust.sustainability.resource_profiler import ResourceProfiler
    profiler = ResourceProfiler(db)
    metric = profiler.measure()
    return {
        "db_size_bytes": metric.db_size_bytes,
        "pgvector_rows": metric.pgvector_rows,
        "bronze_rows": metric.bronze_rows,
        "silver_rows": metric.silver_rows,
        "gold_rows": metric.gold_rows,
        "review_queue_rows": metric.review_queue_rows,
        "measured_at": metric.measured_at.isoformat(),
    }
