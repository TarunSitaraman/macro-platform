"""Pillar 5 — Sustainability: Cost tracking for LLM, crawler, and storage operations.
Satisfies INTERNAL platform cost governance."""

import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, Query
from sqlalchemy import Column, DateTime, Float, Index, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Session

from src.database import Base, get_db

logger = structlog.get_logger().bind(pillar="sustainability")


# ── Enums & Dataclasses ───────────────────────────────────────────────────────

class CostCategory(str, Enum):
    LLM = "LLM"
    CRAWLER = "CRAWLER"
    STORAGE = "STORAGE"


@dataclass
class CostSummary:
    source_id: str
    period_days: int
    total_cost_usd: float
    event_count: int
    breakdown: dict[str, float] = field(default_factory=dict)


# ── SQLAlchemy Model ──────────────────────────────────────────────────────────

class CostEvent(Base):
    __tablename__ = "cost_tracking"

    event_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_id = Column(String(100), nullable=False)
    category = Column(String(50), nullable=False)
    provider = Column(String(100), nullable=True)
    tokens_used = Column(Integer, nullable=True)
    duration_seconds = Column(Float, nullable=True)
    bytes_written = Column(Integer, nullable=True)
    cost_usd = Column(Float, nullable=False)
    occurred_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    compliance_context = Column(
        String, default="INTERNAL - Platform Cost Governance", nullable=False
    )

    __table_args__ = (
        Index("ix_cost_source_occurred", "source_id", "occurred_at"),
        Index("ix_cost_category_occurred", "category", "occurred_at"),
    )


# ── CostTracker ───────────────────────────────────────────────────────────────

class CostTracker:
    def __init__(self, db: Session) -> None:
        self._db = db

    def _get_llm_cost_per_1k(self, provider: str) -> float:
        env_key = f"LLM_COST_PER_1K_TOKENS_{provider.upper()}"
        defaults = {
            "GROQ": 0.00,
            "GEMINI": 0.00015,
            "OPENROUTER": 0.001,
            "CEREBRAS": 0.00,
        }
        raw = os.getenv(env_key)
        if raw is not None:
            return float(raw)
        return defaults.get(provider.upper(), 0.001)

    def _get_playwright_cost_per_second(self) -> float:
        return float(os.getenv("PLAYWRIGHT_COST_PER_SECOND", "0.000001"))

    def _get_storage_cost_per_gb(self) -> float:
        return float(os.getenv("STORAGE_COST_PER_GB", "0.023"))

    def track_llm_call(self, source_id: str, provider: str, tokens: int) -> CostEvent:
        cost = (tokens / 1000) * self._get_llm_cost_per_1k(provider)
        event = CostEvent(
            source_id=source_id,
            category=CostCategory.LLM,
            provider=provider,
            tokens_used=tokens,
            cost_usd=cost,
        )
        self._db.add(event)
        self._db.commit()
        self._db.refresh(event)
        logger.info(
            "llm_cost_tracked",
            source_id=source_id,
            provider=provider,
            tokens=tokens,
            cost_usd=cost,
        )
        return event

    def track_crawler_session(self, source_id: str, duration_seconds: float) -> CostEvent:
        cost = duration_seconds * self._get_playwright_cost_per_second()
        event = CostEvent(
            source_id=source_id,
            category=CostCategory.CRAWLER,
            duration_seconds=duration_seconds,
            cost_usd=cost,
        )
        self._db.add(event)
        self._db.commit()
        self._db.refresh(event)
        logger.info(
            "crawler_cost_tracked",
            source_id=source_id,
            duration_seconds=duration_seconds,
            cost_usd=cost,
        )
        return event

    def track_storage_write(
        self, source_id: str, bytes_written: int, layer: str = "bronze"
    ) -> CostEvent:
        cost = (bytes_written / (1024 ** 3)) * self._get_storage_cost_per_gb()
        event = CostEvent(
            source_id=source_id,
            category=CostCategory.STORAGE,
            provider=layer,
            bytes_written=bytes_written,
            cost_usd=cost,
        )
        self._db.add(event)
        self._db.commit()
        self._db.refresh(event)
        logger.info(
            "storage_cost_tracked",
            source_id=source_id,
            layer=layer,
            bytes_written=bytes_written,
            cost_usd=cost,
        )
        return event

    def get_cost_per_source(self, source_id: str, days: int = 30) -> CostSummary:
        since = datetime.utcnow() - timedelta(days=days)
        events = (
            self._db.query(CostEvent)
            .filter(CostEvent.source_id == source_id, CostEvent.occurred_at >= since)
            .all()
        )
        total = sum(e.cost_usd for e in events)
        breakdown: dict[str, float] = {}
        for e in events:
            breakdown[e.category] = breakdown.get(e.category, 0.0) + e.cost_usd
        return CostSummary(
            source_id=source_id,
            period_days=days,
            total_cost_usd=total,
            event_count=len(events),
            breakdown=breakdown,
        )


# ── FastAPI Router ────────────────────────────────────────────────────────────

router = APIRouter(prefix="/api/sustainability", tags=["sustainability"])


@router.get("/costs")
def get_costs(
    source_id: str,
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_db),
) -> dict:
    tracker = CostTracker(db)
    summary = tracker.get_cost_per_source(source_id=source_id, days=days)
    return {
        "source_id": summary.source_id,
        "period_days": summary.period_days,
        "total_cost_usd": summary.total_cost_usd,
        "event_count": summary.event_count,
        "breakdown": summary.breakdown,
    }


@router.get("/costs/all")
def get_all_costs(
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_db),
) -> list[dict]:
    since = datetime.utcnow() - timedelta(days=days)
    events = (
        db.query(CostEvent)
        .filter(CostEvent.occurred_at >= since)
        .all()
    )

    # Aggregate by source_id
    aggregated: dict[str, float] = {}
    for e in events:
        aggregated[e.source_id] = aggregated.get(e.source_id, 0.0) + e.cost_usd

    top20 = sorted(aggregated.items(), key=lambda x: x[1], reverse=True)[:20]
    return [
        {"source_id": source_id, "total_cost_usd": cost}
        for source_id, cost in top20
    ]
