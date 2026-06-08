"""SQLAlchemy ORM models and database session management."""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean, Column, DateTime, Enum, Float, ForeignKey,
    Integer, String, Text, ARRAY, UniqueConstraint, Index,
    create_engine, text,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker, relationship
from pgvector.sqlalchemy import Vector

from src.config import get_settings

settings = get_settings()

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    echo=False,
)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class Base(DeclarativeBase):
    pass


# ── Enums ──────────────────────────────────────────────────────────────────────

SourceTypeEnum = Enum("API", "HTML", "PDF", "CSV", name="source_type_enum")
FrequencyEnum = Enum(
    "HOURLY", "DAILY", "WEEKLY", "MONTHLY", "QUARTERLY", "ANNUAL",
    name="frequency_enum"
)
ExtractionMethodEnum = Enum(
    "API", "HTML_LLM", "PDF_OCR", "MANUAL", name="extraction_method_enum"
)
DQStatusEnum = Enum(
    "AUTO_PROMOTED", "REVIEW", "REJECTED", name="dq_status_enum"
)
ReviewStatusEnum = Enum(
    "PENDING", "APPROVED", "ADJUSTED", "REJECTED", name="review_status_enum"
)
SummaryTypeEnum = Enum(
    "COUNTRY_SNAPSHOT", "INDICATOR_BRIEF", "SECTOR_ANALYSIS", name="summary_type_enum"
)
AuditActionEnum = Enum("INSERT", "UPDATE", "DELETE", name="audit_action_enum")
LineageStatusEnum = Enum("SUCCESS", "PARTIAL", "FAILED", name="lineage_status_enum")
ChatRoleEnum = Enum("user", "assistant", name="chat_role_enum")


# ── Models ─────────────────────────────────────────────────────────────────────

class SourceConfig(Base):
    __tablename__ = "source_config"

    source_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_code = Column(String(50), unique=True, nullable=False)
    source_name = Column(String(200), nullable=False)
    source_url = Column(Text)
    source_type = Column(SourceTypeEnum, nullable=False)
    extraction_prompt = Column(Text)
    frequency = Column(FrequencyEnum, nullable=False)
    is_active = Column(Boolean, default=True)
    last_run_at = Column(DateTime)
    last_error_at = Column(DateTime)
    error_message = Column(Text)
    reputation_score = Column(Float, default=80.0)
    retry_count = Column(Integer, default=0)
    max_retries = Column(Integer, default=3)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    metadata_ = Column("metadata", JSONB, default=dict)


class IndicatorDefinition(Base):
    __tablename__ = "indicator_definitions"

    indicator_code = Column(String(100), primary_key=True)
    indicator_name = Column(String(200), nullable=False)
    category = Column(String(100))
    standard_unit = Column(String(50))
    description = Column(Text)
    formula = Column(Text)
    min_value = Column(Float)
    max_value = Column(Float)
    frequency = Column(FrequencyEnum)
    is_derived = Column(Boolean, default=False)
    is_leading = Column(Boolean, default=False)
    country_iso3 = Column(String(3))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    deprecated_at = Column(DateTime)
    metadata_ = Column("metadata", JSONB, default=dict)


class BronzeRecord(Base):
    __tablename__ = "bronze_records"

    record_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_code = Column(String(50), ForeignKey("source_config.source_code"), nullable=False)
    indicator_code = Column(String(100), ForeignKey("indicator_definitions.indicator_code"), nullable=False)
    country_code = Column(String(3), nullable=False)
    period = Column(String(20), nullable=False)
    raw_value = Column(Text)
    raw_unit = Column(String(50))
    source_url = Column(Text)
    extraction_method = Column(ExtractionMethodEnum, nullable=False)
    crawled_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    raw_json = Column(JSONB)
    request_id = Column(String(100))

    __table_args__ = (
        Index("ix_bronze_source_indicator", "source_code", "indicator_code"),
        Index("ix_bronze_country_period", "country_code", "period"),
        Index("ix_bronze_crawled_at", "crawled_at"),
    )


class SilverRecord(Base):
    __tablename__ = "silver_records"

    record_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    bronze_id = Column(UUID(as_uuid=True), ForeignKey("bronze_records.record_id"), nullable=False)
    source_code = Column(String(50), ForeignKey("source_config.source_code"), nullable=False)
    indicator_code = Column(String(100), ForeignKey("indicator_definitions.indicator_code"), nullable=False)
    country_code = Column(String(3), nullable=False)
    period = Column(String(20), nullable=False)
    value = Column(Float)
    standard_unit = Column(String(50))
    is_forecast = Column(Boolean, default=False)
    dq_score = Column(Float)
    dq_breakdown = Column(JSONB)
    dq_status = Column(DQStatusEnum)
    failure_reasons = Column(ARRAY(Text))
    normalisation_applied = Column(Text)
    processed_at = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)
    metadata_ = Column("metadata", JSONB, default=dict)

    __table_args__ = (
        Index("ix_silver_indicator_country_period", "indicator_code", "country_code", "period"),
        Index("ix_silver_dq_status", "dq_status"),
    )


class GoldRecord(Base):
    __tablename__ = "gold_records"

    record_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    silver_id = Column(UUID(as_uuid=True), ForeignKey("silver_records.record_id"), nullable=False)
    indicator_code = Column(String(100), ForeignKey("indicator_definitions.indicator_code"), nullable=False)
    country_code = Column(String(3), nullable=False)
    period = Column(String(20), nullable=False)
    value = Column(Float, nullable=False)
    standard_unit = Column(String(50))
    is_forecast = Column(Boolean, default=False)
    source_name = Column(String(200))
    source_url = Column(Text)
    source_code = Column(String(50))
    crawled_at = Column(DateTime)
    revision_flag = Column(Boolean, default=False)
    revision_delta = Column(Float)
    dq_score = Column(Float)
    approved_by = Column(String(100), default="auto")
    promoted_at = Column(DateTime, default=datetime.utcnow)
    embedding = Column(Vector(1024))
    embedding_model = Column(String(100))
    embedding_generated_at = Column(DateTime)

    __table_args__ = (
        Index("ix_gold_indicator_country_period", "indicator_code", "country_code", "period"),
        Index("ix_gold_promoted_at", "promoted_at"),
        Index("ix_gold_source_code", "source_code"),
    )


class ReviewQueue(Base):
    __tablename__ = "review_queue"

    queue_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    silver_id = Column(UUID(as_uuid=True), ForeignKey("silver_records.record_id"), unique=True, nullable=False)
    indicator_code = Column(String(100), ForeignKey("indicator_definitions.indicator_code"))
    country_code = Column(String(3))
    period = Column(String(20))
    extracted_value = Column(Text)
    dq_score = Column(Float)
    dq_breakdown = Column(JSONB)
    failure_reasons = Column(ARRAY(Text))
    source_url = Column(Text)
    status = Column(ReviewStatusEnum, default="PENDING")
    reviewed_by = Column(String(100))
    reviewed_at = Column(DateTime)
    adjusted_value = Column(Float)
    review_notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    sla_deadline = Column(DateTime)

    __table_args__ = (
        Index("ix_review_queue_status", "status"),
        Index("ix_review_queue_created_at", "created_at"),
    )


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    session_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(String(100))
    created_at = Column(DateTime, default=datetime.utcnow)
    last_active = Column(DateTime, default=datetime.utcnow)
    metadata_ = Column("metadata", JSONB, default=dict)

    messages = relationship("ChatMessage", back_populates="session", cascade="all, delete-orphan")


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    message_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(UUID(as_uuid=True), ForeignKey("chat_sessions.session_id"), nullable=False)
    role = Column(ChatRoleEnum, nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    tokens_used = Column(Integer)
    context_records_used = Column(ARRAY(UUID(as_uuid=True)))

    session = relationship("ChatSession", back_populates="messages")

    __table_args__ = (Index("ix_chat_messages_session", "session_id"),)


class Summary(Base):
    __tablename__ = "summaries"

    summary_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    country_code = Column(String(3), nullable=False)
    summary_type = Column(SummaryTypeEnum, nullable=False)
    content = Column(Text, nullable=False)
    indicators_used = Column(ARRAY(UUID(as_uuid=True)))
    generated_at = Column(DateTime, default=datetime.utcnow)
    model_used = Column(String(100))
    template_version = Column(String(50))
    quality_score = Column(Float)
    analyst_notes = Column(Text)

    __table_args__ = (
        Index("ix_summaries_country_type", "country_code", "summary_type"),
    )


class AuditLog(Base):
    __tablename__ = "audit_log"

    log_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    table_name = Column(String(100), nullable=False)
    record_id = Column(UUID(as_uuid=True))
    action = Column(AuditActionEnum, nullable=False)
    old_values = Column(JSONB)
    new_values = Column(JSONB)
    actor = Column(String(100), default="system")
    actor_role = Column(String(50))
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False)
    ip_address = Column(String(45))
    request_id = Column(String(100))
    reason = Column(Text)

    __table_args__ = (
        Index("ix_audit_log_table_record", "table_name", "record_id"),
        Index("ix_audit_log_timestamp", "timestamp"),
    )


class DataLineage(Base):
    __tablename__ = "data_lineage"

    lineage_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_record_id = Column(UUID(as_uuid=True), nullable=False)
    target_record_id = Column(UUID(as_uuid=True), nullable=False)
    transformation = Column(String(200))
    transform_version = Column(String(50))
    started_at = Column(DateTime, nullable=False)
    completed_at = Column(DateTime)
    status = Column(LineageStatusEnum, nullable=False)
    error_message = Column(Text)
    metadata_ = Column("metadata", JSONB, default=dict)

    __table_args__ = (
        Index("ix_lineage_source", "source_record_id"),
        Index("ix_lineage_target", "target_record_id"),
    )


def init_db():
    """Create all tables and enable pgvector extension."""
    with engine.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.commit()
    Base.metadata.create_all(bind=engine)
