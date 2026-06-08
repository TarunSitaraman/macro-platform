"""Platform Overview — KPIs, medallion architecture, source registry."""

import streamlit as st
from sqlalchemy import func

from src.database import (
    BronzeRecord, GoldRecord, ReviewQueue, SessionLocal,
    SilverRecord, SourceConfig,
)

st.title("🏠 Hexaware Macro Data Platform")
st.caption("AI-Enabled Financial Services Macroeconomic Intelligence")

# ── KPI row ────────────────────────────────────────────────────────────────────
db = SessionLocal()
try:
    n_gold = db.query(func.count(GoldRecord.record_id)).scalar() or 0
    n_bronze = db.query(func.count(BronzeRecord.record_id)).scalar() or 0
    n_pending = (
        db.query(func.count(ReviewQueue.queue_id))
        .filter(ReviewQueue.status == "PENDING")
        .scalar() or 0
    )
    n_sources = (
        db.query(func.count(SourceConfig.source_id))
        .filter(SourceConfig.is_active == True)
        .scalar() or 0
    )
    avg_dq = db.query(func.avg(GoldRecord.dq_score)).scalar()
finally:
    db.close()

col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("Gold Records", f"{n_gold:,}", help="Production-ready data points")
col2.metric("Total Ingested", f"{n_bronze:,}", help="Raw bronze records")
col3.metric("Pending Review", n_pending, help="Records awaiting human approval", delta_color="inverse")
col4.metric("Active Sources", n_sources)
col5.metric("Avg DQ Score", f"{avg_dq:.1f}%" if avg_dq else "N/A")

st.divider()

# ── Medallion architecture diagram ─────────────────────────────────────────────
st.subheader("Medallion Architecture")
st.markdown("""
```
External Sources          Bronze Layer            Silver Layer            Gold Layer
─────────────────        ─────────────           ─────────────           ──────────────
World Bank API    ──▶    Raw records     ──▶     Cleaned &       ──▶     Production    ──▶  Chatbot RAG
IMF WEO API       ──▶   (append-only)           DQ-scored               data with          Dashboards
FRED API          ──▶   Full audit trail         DQ ≥ 90% ──▶ Auto      full citation      Summaries
IMF Blog (HTML)   ──▶                            70-90%   ──▶ Review     + Embeddings       API
WB Blog (HTML)    ──▶                            < 70%    ──▶ Reject     pgvector index
```
""")

# ── DQ threshold explanation ───────────────────────────────────────────────────
col_a, col_b, col_c = st.columns(3)
with col_a:
    st.success("**DQ ≥ 90% → Auto-Promoted**\nDirectly to Gold layer. No human review required.")
with col_b:
    st.warning("**70% ≤ DQ < 90% → Review Queue**\nHuman analyst sign-off required within 4-hour SLA.")
with col_c:
    st.error("**DQ < 70% → Rejected**\nLogged with failure reasons. Source flagged for investigation.")

st.divider()

# ── Source registry ─────────────────────────────────────────────────────────────
st.subheader("Data Source Registry")

db = SessionLocal()
try:
    sources = db.query(SourceConfig).order_by(SourceConfig.reputation_score.desc()).all()
finally:
    db.close()

for src in sources:
    status = "🟢 Active" if src.is_active else "🔴 Inactive"
    last_run = src.last_run_at.strftime("%Y-%m-%d %H:%M UTC") if src.last_run_at else "Never"
    with st.expander(f"{status} | {src.source_name} (`{src.source_code}`)"):
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Type", src.source_type)
        c2.metric("Frequency", src.frequency)
        c3.metric("Reputation", f"{src.reputation_score:.0f}/100")
        c4.metric("Last Run", last_run)
        if src.error_message:
            st.error(f"Last error: {src.error_message}")

# ── Indicator catalogue ────────────────────────────────────────────────────────
st.divider()
st.subheader("Indicator Catalogue (Phase 1)")

from src.config import INDICATOR_CATALOGUE
import pandas as pd

rows = [
    {
        "Code": k,
        "Name": v["name"],
        "Category": v["category"],
        "Unit": v["standard_unit"],
        "Frequency": v["frequency"],
    }
    for k, v in INDICATOR_CATALOGUE.items()
]
st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
