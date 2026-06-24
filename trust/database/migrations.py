"""
Pillar: ALL — Trust Layer Database Migration.

Creates every table required by the nine trust pillars.  The migration is
idempotent (uses IF NOT EXISTS) so it is safe to run on an already-migrated
database.

Run directly::

    python -m trust.database.migrations

Or call ``create_trust_tables()`` from the FastAPI lifespan hook.

Regulations satisfied: SOX, MiFID II, GDPR, INTERNAL governance.
"""

from __future__ import annotations

import logging

from sqlalchemy import text

from src.database import engine

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# All DDL statements in dependency order
# ---------------------------------------------------------------------------

_TRUST_DDL: list[tuple[str, str]] = [
    # ── Pillar 1: Reliability ────────────────────────────────────────────────
    (
        "sla_violations",
        """
        CREATE TABLE IF NOT EXISTS sla_violations (
            violation_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            indicator_code      VARCHAR(100) NOT NULL,
            country_code        VARCHAR(3),
            tier                VARCHAR(10) NOT NULL,
            expected_window_minutes INTEGER NOT NULL,
            actual_age_minutes  DOUBLE PRECISION NOT NULL,
            last_crawled_at     TIMESTAMPTZ,
            violated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            compliance_context  TEXT DEFAULT 'MiFID II Article 25 - Data Quality'
        );
        CREATE INDEX IF NOT EXISTS ix_sla_violations_indicator
            ON sla_violations (indicator_code, violated_at DESC);
        """,
    ),
    (
        "extraction_decisions",
        """
        CREATE TABLE IF NOT EXISTS extraction_decisions (
            decision_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            indicator_code      VARCHAR(100),
            source_code         VARCHAR(50),
            confidence          DOUBLE PRECISION NOT NULL,
            decision            VARCHAR(20) NOT NULL,
            decided_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            compliance_context  TEXT DEFAULT 'INTERNAL - Data Quality Gate'
        );
        CREATE INDEX IF NOT EXISTS ix_extraction_decisions_decided_at
            ON extraction_decisions (decided_at DESC);
        """,
    ),
    # ── Pillar 2: Security ───────────────────────────────────────────────────
    (
        "api_keys",
        """
        CREATE TABLE IF NOT EXISTS api_keys (
            key_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name        VARCHAR(100) NOT NULL,
            prefix      VARCHAR(10)  NOT NULL,
            hashed_key  VARCHAR(255) NOT NULL UNIQUE,
            role        VARCHAR(50)  NOT NULL DEFAULT 'PUBLIC',
            created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            is_active   BOOLEAN      NOT NULL DEFAULT TRUE
        );
        CREATE INDEX IF NOT EXISTS ix_api_keys_prefix ON api_keys (prefix);
        """,
    ),
    (
        "rate_limit_buckets",
        """
        CREATE TABLE IF NOT EXISTS rate_limit_buckets (
            bucket_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            identifier      VARCHAR(255) NOT NULL UNIQUE,
            role            VARCHAR(50)  NOT NULL DEFAULT 'PUBLIC',
            daily_count     INTEGER      NOT NULL DEFAULT 0,
            minute_count    INTEGER      NOT NULL DEFAULT 0,
            day_window      DATE         NOT NULL DEFAULT CURRENT_DATE,
            minute_window   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
        );
        """,
    ),
    (
        "blocked_sources",
        """
        CREATE TABLE IF NOT EXISTS blocked_sources (
            block_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            source_url      TEXT NOT NULL,
            source_code     VARCHAR(50),
            blocked_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            block_count     INTEGER     NOT NULL DEFAULT 1,
            cooldown_until  TIMESTAMPTZ,
            reason          VARCHAR(500),
            is_permanent    BOOLEAN     NOT NULL DEFAULT FALSE,
            compliance_context TEXT DEFAULT 'INTERNAL - Crawler Governance'
        );
        CREATE INDEX IF NOT EXISTS ix_blocked_sources_url ON blocked_sources (source_url);
        """,
    ),
    # ── Pillar 3: Safety ─────────────────────────────────────────────────────
    (
        "guardrail_audit_log",
        """
        CREATE TABLE IF NOT EXISTS guardrail_audit_log (
            log_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            query_hash       VARCHAR(64) NOT NULL,
            triggered_filter VARCHAR(100),
            passed           BOOLEAN     NOT NULL,
            response_length  INTEGER,
            logged_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            compliance_context TEXT DEFAULT 'INTERNAL - AI Safety'
        );
        CREATE INDEX IF NOT EXISTS ix_guardrail_audit_logged_at
            ON guardrail_audit_log (logged_at DESC);
        """,
    ),
    # ── Pillar 4: Privacy ────────────────────────────────────────────────────
    (
        "privacy_audit_log",
        """
        CREATE TABLE IF NOT EXISTS privacy_audit_log (
            event_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            query_hash      VARCHAR(64) NOT NULL,
            pii_types_found TEXT[]      NOT NULL DEFAULT '{}',
            redacted_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            compliance_context TEXT DEFAULT 'GDPR Article 17 - Right to Erasure'
        );
        CREATE INDEX IF NOT EXISTS ix_privacy_audit_redacted_at
            ON privacy_audit_log (redacted_at DESC);
        """,
    ),
    (
        "user_consents",
        """
        CREATE TABLE IF NOT EXISTS user_consents (
            consent_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id_hash    VARCHAR(64) NOT NULL,
            consent_type    VARCHAR(50) NOT NULL,
            granted_at      TIMESTAMPTZ,
            revoked_at      TIMESTAMPTZ,
            ip_hash         VARCHAR(64),
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            compliance_context TEXT DEFAULT 'GDPR Article 7 - Consent'
        );
        CREATE INDEX IF NOT EXISTS ix_user_consents_user_type
            ON user_consents (user_id_hash, consent_type);
        """,
    ),
    (
        "retention_audit_log",
        """
        CREATE TABLE IF NOT EXISTS retention_audit_log (
            audit_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            table_name          VARCHAR(100) NOT NULL,
            records_deleted     INTEGER      NOT NULL,
            date_range_start    TIMESTAMPTZ,
            date_range_end      TIMESTAMPTZ,
            deleted_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            compliance_context  TEXT DEFAULT 'GDPR Article 5(1)(e) - Storage Limitation'
        );
        """,
    ),
    # ── Pillar 5: Sustainability ──────────────────────────────────────────────
    (
        "cost_tracking",
        """
        CREATE TABLE IF NOT EXISTS cost_tracking (
            event_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            source_id       VARCHAR(100) NOT NULL,
            category        VARCHAR(50)  NOT NULL,
            provider        VARCHAR(100),
            tokens_used     INTEGER,
            duration_seconds DOUBLE PRECISION,
            bytes_written   BIGINT,
            cost_usd        DOUBLE PRECISION NOT NULL DEFAULT 0.0,
            occurred_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            compliance_context TEXT DEFAULT 'INTERNAL - Platform Cost Governance'
        );
        CREATE INDEX IF NOT EXISTS ix_cost_tracking_source_date
            ON cost_tracking (source_id, occurred_at DESC);
        CREATE INDEX IF NOT EXISTS ix_cost_tracking_category_date
            ON cost_tracking (category, occurred_at DESC);
        """,
    ),
    (
        "crawl_opt_log",
        """
        CREATE TABLE IF NOT EXISTS crawl_opt_log (
            log_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            source_code     VARCHAR(50) NOT NULL,
            action          VARCHAR(20) NOT NULL,
            reason          VARCHAR(500),
            content_hash    VARCHAR(64),
            logged_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            compliance_context TEXT DEFAULT 'INTERNAL - Resource Governance'
        );
        CREATE INDEX IF NOT EXISTS ix_crawl_opt_source_date
            ON crawl_opt_log (source_code, logged_at DESC);
        """,
    ),
    (
        "resource_metrics",
        """
        CREATE TABLE IF NOT EXISTS resource_metrics (
            metric_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            db_size_bytes       BIGINT,
            pgvector_rows       INTEGER,
            bronze_rows         INTEGER,
            silver_rows         INTEGER,
            gold_rows           INTEGER,
            review_queue_rows   INTEGER,
            measured_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            compliance_context  TEXT DEFAULT 'INTERNAL - Infrastructure Governance'
        );
        CREATE INDEX IF NOT EXISTS ix_resource_metrics_measured_at
            ON resource_metrics (measured_at DESC);
        """,
    ),
    # ── Pillar 6: Explainability ──────────────────────────────────────────────
    (
        "explainability_log",
        """
        CREATE TABLE IF NOT EXISTS explainability_log (
            event_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            indicator_code      VARCHAR(100) NOT NULL,
            country_code        VARCHAR(3),
            period              VARCHAR(20),
            candidate_sources   JSONB,
            selected_source     VARCHAR(50),
            selection_rationale TEXT,
            decided_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            compliance_context  TEXT DEFAULT 'MiFID II - Research Transparency'
        );
        CREATE INDEX IF NOT EXISTS ix_explainability_indicator_date
            ON explainability_log (indicator_code, decided_at DESC);
        """,
    ),
    (
        "llm_extraction_traces",
        """
        CREATE TABLE IF NOT EXISTS llm_extraction_traces (
            trace_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            source_url          TEXT,
            raw_excerpt         TEXT,
            extraction_prompt   TEXT,
            extracted_json      JSONB,
            confidence          DOUBLE PRECISION,
            model_used          VARCHAR(100),
            tokens_consumed     INTEGER,
            latency_ms          DOUBLE PRECISION,
            traced_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            compliance_context  TEXT DEFAULT 'SOX - Audit Trail'
        );
        CREATE INDEX IF NOT EXISTS ix_llm_traces_traced_at
            ON llm_extraction_traces (traced_at DESC);
        """,
    ),
    # ── Pillar 7: Data Quality ────────────────────────────────────────────────
    (
        "conflict_log",
        """
        CREATE TABLE IF NOT EXISTS conflict_log (
            conflict_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            indicator_code      VARCHAR(100) NOT NULL,
            country_code        VARCHAR(3),
            period              VARCHAR(20),
            candidate_values    JSONB,
            selected_source     VARCHAR(50),
            selected_value      DOUBLE PRECISION,
            variance_pct        DOUBLE PRECISION,
            severity            VARCHAR(20),
            resolution_rationale TEXT,
            resolved_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            requires_review     BOOLEAN NOT NULL DEFAULT FALSE,
            compliance_context  TEXT DEFAULT 'MiFID II - Data Integrity'
        );
        CREATE INDEX IF NOT EXISTS ix_conflict_log_indicator_date
            ON conflict_log (indicator_code, resolved_at DESC);
        """,
    ),
    (
        "indicator_revisions",
        """
        CREATE TABLE IF NOT EXISTS indicator_revisions (
            revision_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            indicator_code  VARCHAR(100) NOT NULL,
            country_code    VARCHAR(3)   NOT NULL,
            period          VARCHAR(20)  NOT NULL,
            source_id       VARCHAR(50),
            old_value       DOUBLE PRECISION NOT NULL,
            new_value       DOUBLE PRECISION NOT NULL,
            revision_pct    DOUBLE PRECISION,
            is_significant  BOOLEAN NOT NULL DEFAULT FALSE,
            gold_record_id  UUID,
            revised_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            compliance_context TEXT DEFAULT 'SOX - Financial Data Accuracy'
        );
        CREATE INDEX IF NOT EXISTS ix_indicator_revisions_key
            ON indicator_revisions (indicator_code, country_code, period);
        CREATE INDEX IF NOT EXISTS ix_indicator_revisions_date
            ON indicator_revisions (revised_at DESC);
        """,
    ),
    (
        "source_scorecards",
        """
        CREATE TABLE IF NOT EXISTS source_scorecards (
            scorecard_id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            source_code                 VARCHAR(50) NOT NULL,
            week_start                  DATE        NOT NULL,
            extraction_success_rate     DOUBLE PRECISION,
            avg_quality_score           DOUBLE PRECISION,
            revision_frequency          DOUBLE PRECISION,
            conflict_rate               DOUBLE PRECISION,
            availability_uptime         DOUBLE PRECISION,
            source_reputation_score     DOUBLE PRECISION,
            computed_at                 TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            compliance_context          TEXT DEFAULT 'INTERNAL - Data Governance',
            UNIQUE (source_code, week_start)
        );
        CREATE INDEX IF NOT EXISTS ix_source_scorecards_source_week
            ON source_scorecards (source_code, week_start DESC);
        """,
    ),
    # ── Pillar 8: Transparency ────────────────────────────────────────────────
    (
        "citation_trails",
        """
        CREATE TABLE IF NOT EXISTS citation_trails (
            trail_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            indicator_code  VARCHAR(100) NOT NULL,
            country_code    VARCHAR(3)   NOT NULL,
            period          VARCHAR(20)  NOT NULL,
            level1          JSONB,
            level2          JSONB,
            level3          JSONB,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            compliance_context TEXT DEFAULT 'MiFID II - Research Audit Trail',
            UNIQUE (indicator_code, country_code, period)
        );
        CREATE INDEX IF NOT EXISTS ix_citation_trails_indicator
            ON citation_trails (indicator_code);
        """,
    ),
    (
        "governance_policies",
        """
        CREATE TABLE IF NOT EXISTS governance_policies (
            policy_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            policy_name     VARCHAR(200) NOT NULL,
            version         VARCHAR(20)  NOT NULL,
            effective_date  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            approved_by     VARCHAR(100),
            content         JSONB,
            is_active       BOOLEAN NOT NULL DEFAULT TRUE,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            compliance_context TEXT DEFAULT 'INTERNAL - Governance Framework',
            UNIQUE (policy_name, version)
        );
        CREATE INDEX IF NOT EXISTS ix_governance_policies_name
            ON governance_policies (policy_name, is_active);
        """,
    ),
    # ── Pillar 9: Fairness & Accountability ──────────────────────────────────
    (
        "bias_alerts",
        """
        CREATE TABLE IF NOT EXISTS bias_alerts (
            alert_id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id_hash            VARCHAR(64) NOT NULL,
            session_count           INTEGER,
            dominant_region         VARCHAR(100),
            other_regions_missing   TEXT[],
            alerted_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            compliance_context      TEXT DEFAULT 'INTERNAL - Fairness Policy'
        );
        CREATE INDEX IF NOT EXISTS ix_bias_alerts_user ON bias_alerts (user_id_hash);
        """,
    ),
    (
        "accountability_tasks",
        """
        CREATE TABLE IF NOT EXISTS accountability_tasks (
            task_id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            silver_record_id        UUID,
            indicator_code          VARCHAR(100) NOT NULL,
            country_code            VARCHAR(3),
            period                  VARCHAR(20),
            current_level           INTEGER NOT NULL DEFAULT 2,
            assigned_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            sla_deadline            TIMESTAMPTZ NOT NULL,
            status                  VARCHAR(50) NOT NULL DEFAULT 'PENDING',
            resolved_by             VARCHAR(100),
            resolved_at             TIMESTAMPTZ,
            resolution_notes        TEXT,
            escalated_from_level    INTEGER,
            compliance_context      TEXT DEFAULT 'SOX - Internal Controls'
        );
        CREATE INDEX IF NOT EXISTS ix_accountability_tasks_sla
            ON accountability_tasks (status, sla_deadline ASC);
        """,
    ),
    (
        "oversight_approvals",
        """
        CREATE TABLE IF NOT EXISTS oversight_approvals (
            approval_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            silver_record_id    UUID NOT NULL,
            indicator_code      VARCHAR(100),
            country_code        VARCHAR(3),
            period              VARCHAR(20),
            dq_score            DOUBLE PRECISION,
            decision            VARCHAR(50) NOT NULL,
            decided_by          VARCHAR(100) NOT NULL DEFAULT 'system',
            decided_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            notes               TEXT,
            is_immutable        BOOLEAN NOT NULL DEFAULT TRUE,
            compliance_context  TEXT DEFAULT 'SOX Section 404 - Internal Controls'
        );
        CREATE INDEX IF NOT EXISTS ix_oversight_approvals_record
            ON oversight_approvals (silver_record_id);
        CREATE INDEX IF NOT EXISTS ix_oversight_approvals_date
            ON oversight_approvals (decided_at DESC);
        """,
    ),
]


def create_trust_tables() -> None:
    """Create all trust-layer tables.  Safe to call multiple times."""
    with engine.begin() as conn:
        for table_name, ddl in _TRUST_DDL:
            try:
                conn.execute(text(ddl))
                logger.debug("trust_table_ok", table=table_name)
            except Exception as exc:  # noqa: BLE001
                logger.error("trust_table_error", table=table_name, error=str(exc))
                raise
    logger.info("trust_tables_created", count=len(_TRUST_DDL))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    create_trust_tables()
    print(f"Created {len(_TRUST_DDL)} trust tables successfully.")
