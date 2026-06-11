"""Anomaly Detection — identifying irregular macroeconomic deviations."""

import asyncio
import pandas as pd
import streamlit as st
import altair as alt

from src.agents.forecaster import ForecasterAgent
from src.database import GoldRecord, SessionLocal
from src.config import INDICATOR_CATALOGUE, PHASE1_COUNTRIES

st.title("🚨 Anomaly Detection")
st.caption("AI-powered identification of macroeconomic outliers and trend deviations")

# ── Load Anomaly Data ──────────────────────────────────────────────────────────
tenant_id = st.session_state.tenant_id

@st.cache_data(ttl=300)
def load_anomalies(t_id):
    db = SessionLocal()
    try:
        # Find unique pairs for analysis
        pairs = db.query(GoldRecord.indicator_code, GoldRecord.country_code).filter(
            (GoldRecord.tenant_id == None) | (GoldRecord.tenant_id == t_id)
        ).distinct().all()
        
        agent = ForecasterAgent(db, tenant_id=t_id)
        all_anomalies = []
        
        for ind, country in pairs:
            records = db.query(GoldRecord).filter(
                GoldRecord.indicator_code == ind,
                GoldRecord.country_code == country,
                (GoldRecord.tenant_id == None) | (GoldRecord.tenant_id == t_id),
                GoldRecord.is_forecast == False
            ).order_by(GoldRecord.period.asc()).all()
            
            anomalies = agent.detect_anomalies(records)
            for a in anomalies:
                all_anomalies.append({
                    "Indicator": ind,
                    "Country": country,
                    "Date": a["ds"].strftime("%Y-%m-%d"),
                    "Actual": a["actual"],
                    "Expected": a["expected"],
                    "Deviation %": ((a["actual"] - a["expected"]) / abs(a["expected"])) * 100 if a["expected"] != 0 else 0
                })
        return all_anomalies
    finally:
        db.close()

with st.spinner("Analyzing recent data for anomalies..."):
    anomalies = load_anomalies(tenant_id)

if not anomalies:
    st.success("✅ No significant anomalies detected in recent data.")
else:
    df = pd.DataFrame(anomalies)
    st.warning(f"⚠️ Detected {len(df)} anomalies that require investigation.")
    
    # ── Summary Table ─────────────────────────────────────────────────────────
    st.subheader("Recent Deviations")
    st.dataframe(
        df.sort_values("Deviation %", ascending=False),
        use_container_width=True,
        hide_index=True
    )
    
    # ── Visualisation ─────────────────────────────────────────────────────────
    st.subheader("Deviation Heatmap")
    # Ensure native pandas for Altair
    heatmap_df = df.copy()
    chart = alt.Chart(heatmap_df).mark_rect().encode(
        x=alt.X("Country:N"),
        y=alt.Y("Indicator:N"),
        color=alt.Color("Deviation %:Q", scale=alt.Scale(scheme="redblue", domain=[-20, 20])),
        tooltip=["Indicator", "Country", "Actual", "Expected", "Deviation %"]
    ).properties(height=400)
    st.altair_chart(chart, use_container_width=True)

st.divider()
st.info(
    "Anomalies are detected using Prophet's uncertainty intervals. "
    "A point is flagged if it falls outside the 99% confidence interval "
    "predicted by a model trained on 90% of the historical series."
)
