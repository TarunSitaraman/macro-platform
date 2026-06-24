"""Static Data Product — trigger API ingestion and view results."""

import asyncio
import pandas as pd
import streamlit as st

from src.agents.static import FREDAgent, IMFAgent, WorldBankAgent
from src.agents.pipeline import Pipeline
from src.config import INDICATOR_CATALOGUE, PHASE1_COUNTRIES
from src.database import GoldRecord, SessionLocal, SourceConfig

st.title("Static Data Product")
st.caption("Pull macroeconomic data from World Bank, IMF, OECD, and FRED APIs")

# ── Run pipeline ───────────────────────────────────────────────────────────────
st.subheader("Trigger Data Ingestion")

col1, col2 = st.columns(2)
with col1:
    source = st.selectbox(
        "Select Source",
        ["WORLD_BANK", "IMF_WEO", "FRED"],
        format_func=lambda x: {
            "WORLD_BANK": "World Bank Open Data",
            "IMF_WEO": "IMF World Economic Outlook",
            "FRED": "FRED (US Federal Reserve)",
        }[x],
    )
with col2:
    year_from = st.number_input("From Year", value=2010, min_value=2000, max_value=2024)

if st.button("Run Ingestion Pipeline", type="primary"):
    db = SessionLocal()
    try:
        with st.spinner(f"Fetching data from {source}..."):
            if source == "WORLD_BANK":
                raw = asyncio.run(WorldBankAgent().run_all(year_from=int(year_from)))
                source_name = "World Bank Open Data"
                source_url = "https://api.worldbank.org/v2"
            elif source == "IMF_WEO":
                raw = asyncio.run(IMFAgent().run_all())
                source_name = "IMF World Economic Outlook"
                source_url = "https://www.imf.org/external/datamapper/api/v1"
            else:
                raw = asyncio.run(FREDAgent().run_all())
                source_name = "Federal Reserve Economic Data"
                source_url = "https://api.stlouisfed.org/fred"

        st.info(f"Fetched {len(raw)} raw records. Running pipeline...")

        unit_map = {k: v["standard_unit"] for k, v in INDICATOR_CATALOGUE.items()}

        with st.spinner("Processing Bronze -> Silver -> Gold (no embeddings yet)..."):
            pipeline = Pipeline(db, tenant_id=st.session_state.tenant_id)
            counts = pipeline.run_bulk_sync(
                raw_records=raw,
                source_code=source,
                source_name=source_name,
                source_url=source_url,
                extraction_method="API",
                standard_unit_map=unit_map,
            )

        src_row = db.query(SourceConfig).filter(SourceConfig.source_code == source).first()
        if src_row:
            from datetime import datetime, timezone
            src_row.last_run_at = datetime.now(timezone.utc)
            db.commit()

        col_a, col_b, col_c = st.columns(3)
        col_a.metric("Promoted to Gold", counts["promoted"])
        col_b.metric("Queued for Review", counts["queued"])
        col_c.metric("Rejected", counts["rejected"])
        st.success("Done! Now click Generate Embeddings below to enable the chatbot.")

    except Exception as e:
        st.error(f"Pipeline error: {e}")
        db.rollback()
    finally:
        db.close()

# ── Generate embeddings ────────────────────────────────────────────────────────
st.divider()
st.subheader("Generate Embeddings (enables Chatbot RAG)")

tenant_id = st.session_state.tenant_id
db = SessionLocal()
try:
    from sqlalchemy import func
    missing = (
        db.query(func.count(GoldRecord.record_id))
        .filter(GoldRecord.embedding.is_(None))
        .filter(GoldRecord.tenant_id == tenant_id)
        .scalar() or 0
    )
    total = (
        db.query(func.count(GoldRecord.record_id))
        .filter((GoldRecord.tenant_id == None) | (GoldRecord.tenant_id == tenant_id))
        .scalar() or 0
    )
finally:
    db.close()

col1, col2 = st.columns(2)
col1.metric("Gold Records (Tenant)", total)
col2.metric("Missing Embeddings", missing)

if missing > 0:
    if st.button("Generate Embeddings", type="secondary"):
        db = SessionLocal()
        try:
            with st.spinner(f"Batching {missing} records to Jina AI..."):
                pipeline = Pipeline(db, tenant_id=tenant_id)
                updated = asyncio.run(pipeline.generate_embeddings(batch_size=200))
            st.success(f"Done — {updated} embeddings generated. Chatbot is now active.")
        except Exception as e:
            st.error(f"Embedding error: {e}")
        finally:
            db.close()
else:
    st.success("All gold records have embeddings.")

st.divider()

# ── Automated Orchestration ───────────────────────────────────────────────────
st.divider()
st.subheader("🚀 Automated Orchestration (Dagster)")
st.info(
    "Run the full Medallion pipeline (Bronze → Silver → Gold) with automated "
    "retries, dependency management, and data lineage tracking."
)

if st.button("Trigger Full Orchestration Run", type="primary"):
    with st.spinner("Launching Dagster job via API..."):
        try:
            db = SessionLocal()
            from src.orchestration.jobs import defs

            run_config = {
                "ops": {
                    "world_bank_bronze": {"config": {"tenant_id": st.session_state.tenant_id}},
                    "imf_bronze": {"config": {"tenant_id": st.session_state.tenant_id}},
                    "fred_bronze": {"config": {"tenant_id": st.session_state.tenant_id}},
                    "silver_records": {"config": {"tenant_id": st.session_state.tenant_id}},
                    "gold_records": {"config": {"tenant_id": st.session_state.tenant_id}},
                    "macro_news": {"config": {"tenant_id": st.session_state.tenant_id}},
                    "macro_alerts": {"config": {"tenant_id": st.session_state.tenant_id}},
                    "macro_forecasts": {"config": {"tenant_id": st.session_state.tenant_id}},
                }
            }
            
            # Use execute_in_process to bypass daemon requirements in Streamlit
            job_def = defs.get_job_def("full_ingestion_job")
            result = job_def.execute_in_process(run_config=run_config)
            
            if result.success:
                st.success(f"✅ Job completed successfully! Run ID: `{result.run_id}`")
            else:
                st.error("Orchestration job failed. Check console logs.")
            st.markdown(
                f"Monitor progress in the **Dagster UI** (usually at http://localhost:3000)"
            )
        except Exception as e:
            st.error(f"Failed to launch orchestrator: {e}")
            st.caption("Ensure Dagster is correctly installed and initialized.")

with st.expander("🛠️ How to run Dagster UI"):
    st.markdown("""
    To see the live asset graph and monitor runs, open a new terminal and run:
    ```bash
    dagster dev -f src/orchestration/jobs.py
    ```
    Then visit http://localhost:3000
    """)

st.divider()

# ── Browse gold records ────────────────────────────────────────────────────────
st.subheader("Browse Gold Records")

fcol1, fcol2, fcol3 = st.columns(3)
with fcol1:
    ind_filter = st.selectbox("Indicator", ["All"] + list(INDICATOR_CATALOGUE.keys()))
with fcol2:
    country_filter = st.selectbox("Country", ["All"] + PHASE1_COUNTRIES)
with fcol3:
    source_filter = st.selectbox("Source", ["All", "WORLD_BANK", "IMF_WEO", "FRED"])


@st.cache_data(ttl=60)
def load_gold_records(ind, country, src, t_id):
    db = SessionLocal()
    try:
        q = db.query(GoldRecord).filter(
            (GoldRecord.tenant_id == None) | (GoldRecord.tenant_id == t_id)
        )
        if ind != "All":
            q = q.filter(GoldRecord.indicator_code == ind)
        if country != "All":
            q = q.filter(GoldRecord.country_code == country)
        if src != "All":
            q = q.filter(GoldRecord.source_code == src)
        rows = q.order_by(GoldRecord.period.desc()).limit(500).all()
        return [
            {
                "Indicator": r.indicator_code,
                "Country": r.country_code,
                "Period": r.period,
                "Value": r.value,
                "Unit": r.standard_unit,
                "DQ Score": r.dq_score,
                "Source": r.source_name,
                "Revision": r.revision_flag,
                "Embedding": "yes" if r.embedding is not None else "no",
            }
            for r in rows
        ]
    finally:
        db.close()


data = load_gold_records(ind_filter, country_filter, source_filter, tenant_id)
if data:
    df = pd.DataFrame(data)
    st.dataframe(df, use_container_width=True, hide_index=True)
    csv = df.to_csv(index=False).encode("utf-8")
    st.download_button("Download CSV", csv, "gold_records.csv", "text/csv")
else:
    st.info("No gold records yet. Run the ingestion pipeline above.")
