"""
Pillar: ALL — Trust Layer Streamlit Admin Dashboard.

Provides a tabbed admin view across all nine trust pillars.  Run with::

    streamlit run trust/dashboard/streamlit_trust_dashboard.py

Regulations satisfied: SOX (audit), MiFID II (quality/transparency),
GDPR (privacy/retention), INTERNAL governance.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st
from sqlalchemy import text

from src.database import SessionLocal

st.set_page_config(
    page_title="Trust Layer Admin",
    page_icon="🛡️",
    layout="wide",
)

st.title("🛡️ Trustworthy AI — Platform Dashboard")
st.caption("Nine-pillar monitoring for the Macro Intelligence Platform")


# ── Shared DB helper ──────────────────────────────────────────────────────────

@st.cache_resource
def _get_db():
    return SessionLocal()


def _query(sql: str, params: dict | None = None) -> pd.DataFrame:
    db = _get_db()
    try:
        result = db.execute(text(sql), params or {})
        rows = result.fetchall()
        cols = list(result.keys())
        return pd.DataFrame(rows, columns=cols)
    except Exception as exc:
        st.error(f"Query failed: {exc}")
        return pd.DataFrame()


# ── Tab layout ────────────────────────────────────────────────────────────────

tab_reliability, tab_security, tab_quality, tab_coverage, tab_audit = st.tabs([
    "📊 Reliability",
    "🔒 Security",
    "✅ Data Quality",
    "🌍 Coverage",
    "🔍 Audit",
])

# ── Tab 1: Reliability ────────────────────────────────────────────────────────

with tab_reliability:
    st.subheader("SLA Compliance")

    sla_df = _query("""
        SELECT
            tier,
            COUNT(*) AS total_checks,
            SUM(CASE WHEN actual_age_minutes <= expected_window_minutes THEN 1 ELSE 0 END) AS compliant,
            ROUND(
                100.0 * SUM(CASE WHEN actual_age_minutes <= expected_window_minutes THEN 1 ELSE 0 END)
                / NULLIF(COUNT(*), 0), 1
            ) AS compliance_pct
        FROM sla_violations
        WHERE violated_at >= NOW() - INTERVAL '24 hours'
        GROUP BY tier
        ORDER BY tier
    """)
    if not sla_df.empty:
        st.dataframe(sla_df, use_container_width=True)
        st.metric("Overall Compliance (24h)", f"{sla_df['compliance_pct'].mean():.1f}%")
    else:
        st.info("No SLA data yet — run the data pipeline first.")

    st.subheader("Extraction Decision Distribution (Last 7 Days)")
    threshold_df = _query("""
        SELECT decision, COUNT(*) AS count,
               ROUND(AVG(confidence) * 100, 1) AS avg_confidence_pct
        FROM extraction_decisions
        WHERE decided_at >= NOW() - INTERVAL '7 days'
        GROUP BY decision
        ORDER BY count DESC
    """)
    if not threshold_df.empty:
        col1, col2 = st.columns(2)
        with col1:
            st.dataframe(threshold_df, use_container_width=True)
        with col2:
            st.bar_chart(threshold_df.set_index("decision")["count"])
    else:
        st.info("No extraction decisions recorded yet.")

# ── Tab 2: Security ───────────────────────────────────────────────────────────

with tab_security:
    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Rate Limit Hits (Last Hour)")
        rl_df = _query("""
            SELECT identifier, role, daily_count, minute_count, updated_at
            FROM rate_limit_buckets
            WHERE updated_at >= NOW() - INTERVAL '1 hour'
              AND (minute_count > 5 OR daily_count > 50)
            ORDER BY daily_count DESC
            LIMIT 20
        """)
        if not rl_df.empty:
            st.dataframe(rl_df, use_container_width=True)
        else:
            st.info("No rate limit pressure in the last hour.")

    with col2:
        st.subheader("Blocked Crawler Sources")
        blocked_df = _query("""
            SELECT source_url, block_count, cooldown_until, is_permanent, reason
            FROM blocked_sources
            ORDER BY blocked_at DESC
            LIMIT 20
        """)
        if not blocked_df.empty:
            st.dataframe(blocked_df, use_container_width=True)
        else:
            st.success("No blocked sources.")

    st.subheader("Guardrail Audit (Last 24h)")
    guardrail_df = _query("""
        SELECT
            triggered_filter,
            COUNT(*) AS triggers,
            SUM(CASE WHEN passed THEN 1 ELSE 0 END) AS passed_count,
            SUM(CASE WHEN NOT passed THEN 1 ELSE 0 END) AS blocked_count
        FROM guardrail_audit_log
        WHERE logged_at >= NOW() - INTERVAL '24 hours'
        GROUP BY triggered_filter
        ORDER BY triggers DESC
    """)
    if not guardrail_df.empty:
        st.dataframe(guardrail_df, use_container_width=True)
    else:
        st.info("No guardrail events in the last 24 hours.")

# ── Tab 3: Data Quality ───────────────────────────────────────────────────────

with tab_quality:
    st.subheader("Source Reputation Scorecards")
    scorecard_df = _query("""
        SELECT s.source_code, s.source_reputation_score,
               s.extraction_success_rate, s.avg_quality_score,
               s.revision_frequency, s.conflict_rate, s.week_start
        FROM source_scorecards s
        INNER JOIN (
            SELECT source_code, MAX(week_start) AS max_week
            FROM source_scorecards
            GROUP BY source_code
        ) latest ON s.source_code = latest.source_code AND s.week_start = latest.max_week
        ORDER BY s.source_reputation_score DESC
        LIMIT 20
    """)
    if not scorecard_df.empty:
        st.dataframe(
            scorecard_df.style.background_gradient(
                subset=["source_reputation_score"], cmap="RdYlGn"
            ),
            use_container_width=True,
        )
    else:
        st.info("No scorecards yet — scorecards are computed weekly.")

    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Recent Conflicts")
        conflict_df = _query("""
            SELECT indicator_code, country_code, period, variance_pct, severity, resolved_at
            FROM conflict_log
            ORDER BY resolved_at DESC
            LIMIT 15
        """)
        if not conflict_df.empty:
            st.dataframe(conflict_df, use_container_width=True)
        else:
            st.success("No conflicts recorded.")

    with col2:
        st.subheader("Review Queue")
        review_df = _query("""
            SELECT indicator_code, country_code, period,
                   current_level, sla_deadline,
                   ROUND(EXTRACT(EPOCH FROM (sla_deadline - NOW())) / 3600, 1) AS hours_remaining
            FROM accountability_tasks
            WHERE status = 'PENDING'
            ORDER BY sla_deadline ASC
            LIMIT 15
        """)
        if not review_df.empty:
            st.dataframe(review_df, use_container_width=True)
            st.metric("Open tasks", len(review_df))
        else:
            st.success("Review queue is empty.")

    st.subheader("Significant Revisions (Last 30 Days)")
    revision_df = _query("""
        SELECT indicator_code, country_code, period,
               old_value, new_value, revision_pct, revised_at
        FROM indicator_revisions
        WHERE is_significant = TRUE
          AND revised_at >= NOW() - INTERVAL '30 days'
        ORDER BY ABS(revision_pct) DESC
        LIMIT 20
    """)
    if not revision_df.empty:
        st.dataframe(revision_df, use_container_width=True)
    else:
        st.info("No significant revisions in the last 30 days.")

# ── Tab 4: Coverage ───────────────────────────────────────────────────────────

with tab_coverage:
    st.subheader("Indicator Coverage by Country")

    coverage_df = _query("""
        SELECT country_code,
               COUNT(DISTINCT indicator_code) AS indicators_covered,
               ROUND(COUNT(DISTINCT indicator_code) * 100.0 / 11, 1) AS coverage_pct,
               MAX(crawled_at) AS last_updated
        FROM gold_records
        GROUP BY country_code
        ORDER BY coverage_pct DESC
    """)

    if not coverage_df.empty:
        col1, col2, col3 = st.columns(3)
        col1.metric("Countries with data", len(coverage_df))
        col2.metric("Avg coverage", f"{coverage_df['coverage_pct'].mean():.1f}%")
        col3.metric("Full coverage (100%)", int((coverage_df["coverage_pct"] == 100).sum()))

        st.dataframe(
            coverage_df.style.background_gradient(subset=["coverage_pct"], cmap="RdYlGn"),
            use_container_width=True,
        )

        low_coverage = coverage_df[coverage_df["coverage_pct"] < 60]
        if not low_coverage.empty:
            st.warning(
                f"⚠️  {len(low_coverage)} countries have coverage below 60%: "
                + ", ".join(low_coverage["country_code"].tolist())
            )
    else:
        st.info("No gold records found — run the data pipeline to populate coverage.")

    st.subheader("Platform-wide Data Summary")
    summary_df = _query("""
        SELECT
            (SELECT COUNT(*) FROM bronze_records) AS bronze_records,
            (SELECT COUNT(*) FROM silver_records) AS silver_records,
            (SELECT COUNT(*) FROM gold_records)   AS gold_records,
            (SELECT COUNT(*) FROM gold_records WHERE embedding IS NOT NULL) AS with_embeddings
    """)
    if not summary_df.empty:
        s = summary_df.iloc[0]
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Bronze records", int(s["bronze_records"]))
        c2.metric("Silver records", int(s["silver_records"]))
        c3.metric("Gold records", int(s["gold_records"]))
        c4.metric("With embeddings", int(s["with_embeddings"]))

# ── Tab 5: Audit ──────────────────────────────────────────────────────────────

with tab_audit:
    st.subheader("Citation Trails")
    citation_search = st.text_input("Search by indicator code", "GDP_GROWTH")
    if citation_search:
        citation_df = _query(
            """
            SELECT indicator_code, country_code, period,
                   level1->>'source_name' AS source_name,
                   level1->>'crawl_method' AS crawl_method,
                   level2->>'quality_score' AS quality_score,
                   level3->>'api_calls_count' AS api_calls,
                   updated_at
            FROM citation_trails
            WHERE indicator_code ILIKE :code
            ORDER BY updated_at DESC
            LIMIT 25
            """,
            {"code": f"%{citation_search}%"},
        )
        if not citation_df.empty:
            st.dataframe(citation_df, use_container_width=True)
        else:
            st.info(f"No citation trails for '{citation_search}'")

    st.subheader("Governance Policies")
    policy_df = _query("""
        SELECT policy_name, version, approved_by, effective_date, is_active
        FROM governance_policies
        ORDER BY policy_name, effective_date DESC
    """)
    if not policy_df.empty:
        st.dataframe(policy_df, use_container_width=True)
    else:
        st.info("No governance policies seeded yet.  Run PolicyManager.seed_default_policy().")

    st.subheader("LLM Extraction Traces (Last 50)")
    trace_df = _query("""
        SELECT trace_id, model_used, confidence, tokens_consumed,
               ROUND(latency_ms) AS latency_ms, traced_at
        FROM llm_extraction_traces
        ORDER BY traced_at DESC
        LIMIT 50
    """)
    if not trace_df.empty:
        st.dataframe(trace_df, use_container_width=True)

        col1, col2 = st.columns(2)
        col1.metric("Avg confidence", f"{trace_df['confidence'].mean():.2f}")
        col2.metric("Avg latency (ms)", f"{trace_df['latency_ms'].mean():.0f}")
    else:
        st.info("No LLM traces recorded yet.")

    st.subheader("Privacy Audit Log (Last 30 Days)")
    pii_df = _query("""
        SELECT
            COUNT(*) AS total_pii_events,
            unnest(pii_types_found) AS pii_type
        FROM privacy_audit_log
        WHERE redacted_at >= NOW() - INTERVAL '30 days'
        GROUP BY pii_type
        ORDER BY total_pii_events DESC
    """)
    if not pii_df.empty:
        st.bar_chart(pii_df.set_index("pii_type")["total_pii_events"])
    else:
        st.success("No PII detected in the last 30 days.")

    st.subheader("Oversight Approvals (Last 7 Days)")
    oversight_df = _query("""
        SELECT decision, decided_by, COUNT(*) AS count,
               ROUND(AVG(dq_score), 1) AS avg_dq_score
        FROM oversight_approvals
        WHERE decided_at >= NOW() - INTERVAL '7 days'
        GROUP BY decision, decided_by
        ORDER BY count DESC
    """)
    if not oversight_df.empty:
        st.dataframe(oversight_df, use_container_width=True)
    else:
        st.info("No oversight decisions in the last 7 days.")
