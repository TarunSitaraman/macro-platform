"""Data Explorer — filter, visualise, and inspect gold records."""

import altair as alt
import pandas as pd
import streamlit as st

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
    year_from = st.number_input("From Year", value=2010, min_value=2000, max_value=2025)
with col4:
    year_to = st.number_input("To Year", value=2025, min_value=2000, max_value=2030)


@st.cache_data(ttl=120)
def load_explorer_data(indicators, countries, y_from, y_to, tenant_id):
    db = SessionLocal()
    try:
        q = db.query(GoldRecord).filter(
            (GoldRecord.tenant_id == None) | (GoldRecord.tenant_id == tenant_id)
        )
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
    tuple(ind_sel), tuple(country_sel), int(year_from), int(year_to),
    st.session_state.tenant_id
)

if not data:
    st.info("No records match the current filters.")
    st.stop()

df = pd.DataFrame(data)
st.success(f"Found {len(df):,} records")

# ── Chart ───────────────────────────────────────────────────────────────────────
if not df.empty and ind_sel:
    # Ensure native pandas for Altair
    chart_df = df.copy()
    
    # Filter out forecast data before charting
    chart_df = chart_df[~chart_df["Forecast"]]
    
    for ind in ind_sel:
        ind_df = chart_df[chart_df["Indicator"] == ind].copy()
        if ind_df.empty:
            continue

        meta = INDICATOR_CATALOGUE.get(ind, {})
        unit = ind_df["Unit"].iloc[0]
        title = f"{meta.get('name', ind)}  ({unit})"

        highlight = alt.selection_point(fields=["Country"], bind="legend")

        base = alt.Chart(ind_df).encode(
            x=alt.X("Period:O", axis=alt.Axis(labelAngle=-45, title="Year")),
            y=alt.Y("Value:Q", title=unit),
            color=alt.Color("Country:N", legend=alt.Legend(orient="bottom", columns=5)),
            opacity=alt.condition(highlight, alt.value(1.0), alt.value(0.15)),
            tooltip=[
                alt.Tooltip("Country:N"),
                alt.Tooltip("Period:O", title="Year"),
                alt.Tooltip("Value:Q", format=".2f"),
                alt.Tooltip("Source:N"),
                alt.Tooltip("DQ Score:Q", format=".1f"),
            ],
        ).add_params(highlight)

        lines = base.mark_line()
        points = base.mark_point(filled=True, size=55)

        chart = (lines + points).properties(
            height=380,
            title=alt.TitleParams(title, anchor="start", fontSize=14),
        ).interactive()

        st.subheader(f"📈 {meta.get('name', ind)}")
        st.altair_chart(chart, use_container_width=True)

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
