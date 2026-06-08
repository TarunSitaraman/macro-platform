"""Data Explorer — filter, visualise, and inspect gold records."""

import pandas as pd
import streamlit as st

try:
    import plotly.express as px
    HAS_PLOTLY = True
except ImportError:
    HAS_PLOTLY = False

from src.config import INDICATOR_CATALOGUE, PHASE1_COUNTRIES
from src.database import GoldRecord, SessionLocal

st.title("📊 Data Explorer")
st.caption("Filter, visualise, and inspect gold-layer macroeconomic data")

# ── Filters ─────────────────────────────────────────────────────────────────────
st.subheader("Filters")
col1, col2, col3, col4 = st.columns(4)

with col1:
    ind_sel = st.multiselect(
        "Indicators",
        list(INDICATOR_CATALOGUE.keys()),
        default=["GDP_GROWTH", "CPI_INFLATION"],
    )
with col2:
    country_sel = st.multiselect(
        "Countries",
        PHASE1_COUNTRIES,
        default=["USA", "GBR", "CHN"],
    )
with col3:
    year_from = st.number_input("From Year", value=2015, min_value=2000, max_value=2024)
with col4:
    year_to = st.number_input("To Year", value=2024, min_value=2000, max_value=2030)


@st.cache_data(ttl=120)
def load_explorer_data(indicators, countries, y_from, y_to):
    db = SessionLocal()
    try:
        q = db.query(GoldRecord)
        if indicators:
            q = q.filter(GoldRecord.indicator_code.in_(indicators))
        if countries:
            q = q.filter(GoldRecord.country_code.in_(countries))
        q = q.filter(
            GoldRecord.period >= str(y_from),
            GoldRecord.period <= str(y_to),
        )
        rows = q.order_by(GoldRecord.period).limit(2000).all()
        return [
            {
                "record_id": str(r.record_id),
                "Indicator": r.indicator_code,
                "Country": r.country_code,
                "Period": r.period,
                "Value": r.value,
                "Unit": r.standard_unit,
                "Forecast": r.is_forecast,
                "DQ Score": r.dq_score,
                "Source": r.source_name,
                "Revision": r.revision_flag,
                "Source URL": r.source_url,
                "Approved By": r.approved_by,
            }
            for r in rows
        ]
    finally:
        db.close()


data = load_explorer_data(
    tuple(ind_sel), tuple(country_sel), int(year_from), int(year_to)
)

if not data:
    st.info("No records match the current filters.")
    st.stop()

df = pd.DataFrame(data)
st.success(f"Found {len(df):,} records")

# ── Chart ───────────────────────────────────────────────────────────────────────
if HAS_PLOTLY and len(ind_sel) == 1 and not df.empty:
    st.subheader(f"📈 {ind_sel[0]} over Time")
    fig = px.line(
        df,
        x="Period",
        y="Value",
        color="Country",
        title=f"{INDICATOR_CATALOGUE.get(ind_sel[0], {}).get('name', ind_sel[0])} ({df['Unit'].iloc[0]})",
        markers=True,
        line_dash="Forecast",
    )
    fig.update_layout(height=450)
    st.plotly_chart(fig, use_container_width=True)
elif not HAS_PLOTLY:
    st.info("Install plotly for interactive charts: `pip install plotly`")

# ── Table ───────────────────────────────────────────────────────────────────────
st.subheader("Data Table")
display_cols = ["Indicator", "Country", "Period", "Value", "Unit", "Forecast", "DQ Score", "Source", "Revision"]
st.dataframe(df[display_cols], use_container_width=True, hide_index=True)

# ── Record detail ───────────────────────────────────────────────────────────────
st.subheader("Record Detail")
if not df.empty:
    selected_id = st.selectbox(
        "Select record for full detail",
        df["record_id"].tolist(),
        format_func=lambda x: df.loc[df["record_id"] == x, ["Indicator", "Country", "Period"]].values[0].tolist().__str__()
    )
    row = df[df["record_id"] == selected_id].iloc[0]
    with st.expander("Full Record Details", expanded=True):
        for col in df.columns:
            st.markdown(f"**{col}**: {row[col]}")

# ── Download ─────────────────────────────────────────────────────────────────────
csv = df[display_cols].to_csv(index=False).encode("utf-8")
st.download_button("⬇ Download CSV", csv, "explorer_data.csv", "text/csv")
