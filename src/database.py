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

class Tenant(Base):
    __tablename__ = "tenants"

    tenant_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(200), unique=True, nullable=False)
    slug = Column(String(50), unique=True, nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    metadata_ = Column("metadata", JSONB, default=dict)

    users = relationship("User", back_populates="tenant", cascade="all, delete-orphan")
    api_keys = relationship("TenantAPIKey", back_populates="tenant", cascade="all, delete-orphan")


class User(Base):
    __tablename__ = "users"

    user_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.tenant_id"), nullable=False)
    email = Column(String(255), unique=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    full_name = Column(String(200))
    role = Column(String(50), default="viewer")  # admin, analyst, viewer
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    tenant = relationship("Tenant", back_populates="users")
    chat_sessions = relationship("ChatSession", back_populates="user")


class TenantAPIKey(Base):
    __tablename__ = "tenant_api_keys"

    key_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.tenant_id"), nullable=False)
    name = Column(String(100), nullable=False)
    prefix = Column(String(10), nullable=False)
    hashed_key = Column(String(255), nullable=False, unique=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime)
    last_used_at = Column(DateTime)
    is_active = Column(Boolean, default=True)

    tenant = relationship("Tenant", back_populates="api_keys")


class SourceConfig(Base):
    __tablename__ = "source_config"

    source_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.tenant_id"), nullable=True) # Null for global sources
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
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.tenant_id"), nullable=True) # Null for global indicators
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
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.tenant_id"), nullable=True)
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
        Index("ix_bronze_tenant", "tenant_id"),
    )


class SilverRecord(Base):
    __tablename__ = "silver_records"

    record_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.tenant_id"), nullable=True)
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
        Index("ix_silver_tenant", "tenant_id"),
    )


class GoldRecord(Base):
    __tablename__ = "gold_records"

    record_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.tenant_id"), nullable=True)
    silver_id = Column(UUID(as_uuid=True), ForeignKey("silver_records.record_id"), nullable=True)
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
        Index("ix_gold_tenant", "tenant_id"),
    )


class ReviewQueue(Base):
    __tablename__ = "review_queue"

    queue_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.tenant_id"), nullable=True)
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
        Index("ix_review_queue_tenant", "tenant_id"),
    )


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    session_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.tenant_id"), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_active = Column(DateTime, default=datetime.utcnow)
    metadata_ = Column("metadata", JSONB, default=dict)

    messages = relationship("ChatMessage", back_populates="session", cascade="all, delete-orphan")
    user = relationship("User", back_populates="chat_sessions")


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
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.tenant_id"), nullable=True)
    country_code = Column(String(10), nullable=False)
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
        Index("ix_summaries_tenant", "tenant_id"),
    )


class AuditLog(Base):
    __tablename__ = "audit_log"

    log_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.tenant_id"), nullable=True)
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
        Index("ix_audit_log_tenant", "tenant_id"),
    )


class DataLineage(Base):
    __tablename__ = "data_lineage"

    lineage_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.tenant_id"), nullable=True)
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
        Index("ix_lineage_tenant", "tenant_id"),
    )


class NewsRecord(Base):
    __tablename__ = "news_records"

    news_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.tenant_id"), nullable=True)
    source_name = Column(String(200), nullable=False)
    title = Column(Text, nullable=False)
    content = Column(Text)
    url = Column(Text, unique=True)
    published_at = Column(DateTime)
    category = Column(String(100))  # e.g., Fiscal, Monetary, Trade
    sentiment_score = Column(Float)  # -1 to 1
    sentiment_label = Column(String(20))  # Positive, Neutral, Negative
    impact_indicators = Column(ARRAY(String))  # Indicators this news might affect
    country_code = Column(String(3))
    created_at = Column(DateTime, default=datetime.utcnow)
    embedding = Column(Vector(1024))

    __table_args__ = (
        Index("ix_news_tenant", "tenant_id"),
        Index("ix_news_published", "published_at"),
    )


class AgentRun(Base):
    __tablename__ = "agent_runs"

    run_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.tenant_id"), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=True)
    session_id = Column(UUID(as_uuid=True), ForeignKey("chat_sessions.session_id"), nullable=True)
    agent_name = Column(String(100), nullable=False)
    query = Column(Text, nullable=False)
    response = Column(Text)
    model_used = Column(String(100))
    confidence = Column(String(20))
    grounding_warnings = Column(JSONB, default=list)
    context_record_ids = Column(ARRAY(Text))
    status = Column(String(50), default="running")
    created_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime)

    steps = relationship("AgentStep", back_populates="run", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_agent_runs_tenant", "tenant_id"),
        Index("ix_agent_runs_session", "session_id"),
        Index("ix_agent_runs_created", "created_at"),
    )


class AgentStep(Base):
    __tablename__ = "agent_steps"

    step_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id = Column(UUID(as_uuid=True), ForeignKey("agent_runs.run_id"), nullable=False)
    step_index = Column(Integer, nullable=False)
    step_type = Column(String(50), nullable=False)
    payload = Column(JSONB, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow)

    run = relationship("AgentRun", back_populates="steps")

    __table_args__ = (Index("ix_agent_steps_run", "run_id"),)


def init_db():
    """Create all tables and enable pgvector extension."""
    with engine.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.commit()
    Base.metadata.create_all(bind=engine)
