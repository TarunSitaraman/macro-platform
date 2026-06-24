"""Pillar 6 — Explainability: LLM extraction trace audit log.
Satisfies SOX audit trail requirements."""

import uuid
from datetime import datetime
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import Column, DateTime, Float, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Session

from src.database import Base, get_db
from trust.security.auth import UserRole, require_role

logger = structlog.get_logger().bind(pillar="explainability")


# ── SQLAlchemy Model ──────────────────────────────────────────────────────────

class LLMExtractionTrace(Base):
    __tablename__ = "llm_extraction_traces"

    trace_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_url = Column(Text, nullable=False)
    raw_excerpt = Column(Text, nullable=False)
    extraction_prompt = Column(Text, nullable=False)
    extracted_json = Column(JSONB, nullable=False)
    confidence = Column(Float, nullable=True)
    model_used = Column(String(100), nullable=False)
    tokens_consumed = Column(Integer, nullable=True)
    latency_ms = Column(Float, nullable=False)
    traced_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    compliance_context = Column(
        String, default="SOX - Audit Trail", nullable=False
    )

    __table_args__ = (Index("ix_llm_traces_traced_at", "traced_at"),)


# ── LLMTrace ──────────────────────────────────────────────────────────────────

class LLMTrace:
    def __init__(self, db: Session) -> None:
        self._db = db

    def record(
        self,
        source_url: str,
        raw_excerpt: str,
        extraction_prompt: str,
        extracted_json: dict,
        confidence: float,
        model_used: str,
        tokens_consumed: int,
        latency_ms: float,
    ) -> LLMExtractionTrace:
        trace = LLMExtractionTrace(
            source_url=source_url,
            raw_excerpt=raw_excerpt[:500],
            extraction_prompt=extraction_prompt,
            extracted_json=extracted_json,
            confidence=confidence,
            model_used=model_used,
            tokens_consumed=tokens_consumed,
            latency_ms=latency_ms,
        )
        self._db.add(trace)
        self._db.commit()
        self._db.refresh(trace)
        logger.info(
            "llm_trace_recorded",
            trace_id=str(trace.trace_id),
            source_url=source_url,
            model_used=model_used,
            tokens_consumed=tokens_consumed,
            latency_ms=latency_ms,
        )
        return trace

    def get_trace(self, trace_id: str) -> Optional[LLMExtractionTrace]:
        try:
            parsed_id = uuid.UUID(trace_id)
        except ValueError:
            return None
        return (
            self._db.query(LLMExtractionTrace)
            .filter(LLMExtractionTrace.trace_id == parsed_id)
            .first()
        )


# ── FastAPI Router ────────────────────────────────────────────────────────────

router = APIRouter(prefix="/api/audit", tags=["explainability"])


@router.get("/llm-trace/{trace_id}", dependencies=[Depends(require_role(UserRole.INTERNAL_ANALYST))])
def get_llm_trace(
    trace_id: str,
    db: Session = Depends(get_db),
) -> dict:
    tracer = LLMTrace(db)
    trace = tracer.get_trace(trace_id)
    if trace is None:
        raise HTTPException(status_code=404, detail="Trace not found.")
    return {
        "trace_id": str(trace.trace_id),
        "source_url": trace.source_url,
        "raw_excerpt": trace.raw_excerpt,
        "extraction_prompt": trace.extraction_prompt,
        "extracted_json": trace.extracted_json,
        "confidence": trace.confidence,
        "model_used": trace.model_used,
        "tokens_consumed": trace.tokens_consumed,
        "latency_ms": trace.latency_ms,
        "traced_at": trace.traced_at.isoformat(),
        "compliance_context": trace.compliance_context,
    }
