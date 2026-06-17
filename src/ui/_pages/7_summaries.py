"""Summary Engine — AI-generated country snapshots and indicator briefs."""

import asyncio

import altair as alt
import pandas as pd
import streamlit as st

from src.agents.summarizer import SummarizerAgent
from src.config import INDICATOR_CATALOGUE, PHASE1_COUNTRIES
from src.database import GoldRecord, SessionLocal

st.title("📝 Summary Engine")
st.caption("AI-generated macroeconomic narratives with full data citation")

# ── Generate ───────────────────────────────────────────────────────────────────
st.subheader("Generate Summary")

summary_type = st.selectbox(
    "Summary Type",
    ["COUNTRY_SNAPSHOT", "INDICATOR_BRIEF", "SECTOR_ANALYSIS"],
    format_func=lambda x: {
        "COUNTRY_SNAPSHOT": "🌍 Country Snapshot — narrative for one or more countries",
        "INDICATOR_BRIEF":  "📈 Indicator Brief — deep-dive on one indicator across countries",
        "SECTOR_ANALYSIS":  "🏭 Sector Analysis — cross-indicator theme for selected countries",
    }[x],
)

st.markdown("---")

# ── Dynamic sub-options per type ───────────────────────────────────────────────
if summary_type == "COUNTRY_SNAPSHOT":
    col1, col2 = st.columns([3, 1])
    with col1:
        countries_sel = st.multiselect(
            "Countries (up to 5)",
            PHASE1_COUNTRIES,
            default=["USA"],
            max_selections=5,
        )
    with col2:
        year_from = st.number_input("From Year", value=2018, min_value=2000, max_value=2025)

    ind_focus = st.multiselect(
        "Focus Indicators (leave blank for all)",
        list(INDICATOR_CATALOGUE.keys()),
        default=[],
        format_func=lambda x: INDICATOR_CATALOGUE[x]["name"],
    )
    indicators_sel = ind_focus or None

elif summary_type == "INDICATOR_BRIEF":
    col1, col2 = st.columns([2, 2])
    with col1:
        ind_sel = st.selectbox(
            "Indicator",
            list(INDICATOR_CATALOGUE.keys()),
            format_func=lambda x: INDICATOR_CATALOGUE[x]["name"],
        )
    with col2:
        year_from = st.number_input("From Year", value=2018, min_value=2000, max_value=2025)

    countries_sel = st.multiselect(
        "Countries to compare (leave blank for all 20)",
        PHASE1_COUNTRIES,
        default=["USA", "CHN", "DEU", "IND", "GBR"],
    )
    indicators_sel = None

else:  # SECTOR_ANALYSIS
    col1, col2 = st.columns([2, 2])
    with col1:
        countries_sel = st.multiselect(
            "Countries (up to 5)",
            PHASE1_COUNTRIES,
            default=["USA"],
            max_selections=5,
        )
    with col2:
        sector_theme = st.selectbox(
            "Sector Theme",
            list(SummarizerAgent.SECTOR_INDICATORS.keys()),
        )
    year_from = st.number_input("From Year", value=2018, min_value=2000, max_value=2025, key="sector_year")
    indicators_sel = None

st.markdown("---")

if st.button("✨ Generate Summary", type="primary"):
    if not countries_sel and summary_type != "INDICATOR_BRIEF":
        st.warning("Select at least one country.")
        st.stop()

    with st.spinner("Generating AI summary..."):
        db = SessionLocal()
        try:
            tenant_id = st.session_state.tenant_id
            agent = SummarizerAgent(db, tenant_id=tenant_id)
            if summary_type == "COUNTRY_SNAPSHOT":
                summary = asyncio.run(
                    agent.generate_country_snapshot(
                        country=countries_sel[0], # Updated to single country per roadmap logic
                        year_from=int(year_from),
                        indicators=indicators_sel,
                    )
                )
            elif summary_type == "INDICATOR_BRIEF":
                summary = asyncio.run(
                    agent.generate_indicator_brief(
                        indicator_code=ind_sel,
                        countries=countries_sel or None,
                        year_from=int(year_from),
                    )
                )
            else:
                summary = asyncio.run(
                    agent.generate_sector_analysis(
                        country=countries_sel[0], # Updated to single country
                        sector_theme=sector_theme,
                    )
                )

            # Load chart data while session is open (with tenant isolation)
            chart_query = db.query(GoldRecord).filter(
                (GoldRecord.tenant_id == None) | (GoldRecord.tenant_id == tenant_id)
            )
            if summary_type == "INDICATOR_BRIEF":
                chart_query = chart_query.filter(
                    GoldRecord.indicator_code == ind_sel,
                    GoldRecord.period >= str(year_from),
                )
                if countries_sel:
                    chart_query = chart_query.filter(GoldRecord.country_code.in_(countries_sel))
            else:
                chart_query = chart_query.filter(
                    GoldRecord.country_code.in_(countries_sel),
                    GoldRecord.period >= str(year_from),
                )
                ind_for_chart = indicators_sel or (
                    list(SummarizerAgent.SECTOR_INDICATORS.get(sector_theme, []))
                    if summary_type == "SECTOR_ANALYSIS"
                    else list(INDICATOR_CATALOGUE.keys())
                )
                chart_query = chart_query.filter(GoldRecord.indicator_code.in_(ind_for_chart))

            chart_rows = chart_query.order_by(GoldRecord.period).limit(2000).all()
            chart_data = [
                {
                    "Indicator": r.indicator_code,
                    "Country": r.country_code,
                    "Period": r.period,
                    "Value": r.value,
                    "Unit": r.standard_unit,
                    "Forecast": r.is_forecast,
                }
                for r in chart_rows
            ]
        finally:
            db.close()

    st.success(f"Summary generated using `{summary.model_used}`")
    st.markdown(summary.content)
    st.caption(f"Generated: {summary.generated_at.strftime('%Y-%m-%d %H:%M UTC')}")
    st.download_button(
        "⬇ Download as Markdown",
        summary.content.encode("utf-8"),
        f"summary_{summary.country_code}_{summary.summary_type}.md",
        "text/markdown",
    )

    # ── Charts ─────────────────────────────────────────────────────────────────
    if chart_data:
        st.markdown("---")
        st.subheader("📊 Data Used in This Summary")
        df = pd.DataFrame(chart_data)

        indicators_in_data = df["Indicator"].unique().tolist()
        for ind in indicators_in_data:
            ind_df = df[df["Indicator"] == ind]
            if ind_df.empty:
                continue
            meta = INDICATOR_CATALOGUE.get(ind, {})
            unit = ind_df["Unit"].iloc[0]

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
                ],
            ).add_params(highlight)

            lines = base.mark_line().encode(
                strokeDash=alt.condition(
                    "datum.Forecast", alt.value([6, 4]), alt.value([1, 0])
                )
            )
            points = base.mark_point(filled=True, size=50)

            chart = (lines + points).properties(
                height=300,
                title=alt.TitleParams(
                    meta.get("name", ind) + f"  ({unit})", anchor="start", fontSize=13
                ),
            ).interactive()

            st.altair_chart(chart, use_container_width=True)

st.divider()

# ── Previous summaries ─────────────────────────────────────────────────────────
st.subheader("Previous Summaries")

fcol1, fcol2 = st.columns(2)
with fcol1:
    filter_country = st.selectbox("Filter by Country", ["All"] + PHASE1_COUNTRIES, key="hist_country")
with fcol2:
    filter_type = st.selectbox(
        "Filter by Type",
        ["All", "COUNTRY_SNAPSHOT", "INDICATOR_BRIEF", "SECTOR_ANALYSIS"],
        key="hist_type",
    )


@st.cache_data(ttl=60)
def load_summaries(country, stype, t_id):
    db = SessionLocal()
    try:
        agent = SummarizerAgent(db, tenant_id=t_id)
        rows = agent.list_summaries(
            country_code=None if country == "All" else country,
            summary_type=None if stype == "All" else stype,
        )
        return [
            {
                "summary_id": str(r.summary_id),
                "Country": r.country_code,
                "Type": r.summary_type,
                "Generated": r.generated_at.strftime("%Y-%m-%d %H:%M"),
                "Model": r.model_used,
                "Content": r.content,
            }
            for r in rows
        ]
    finally:
        db.close()


previous = load_summaries(filter_country, filter_type, st.session_state.tenant_id)
if not previous:
    st.info("No summaries yet. Generate one above.")
else:
    for s in previous:
        label = f"🌍 {s['Country']} | {s['Type']} | {s['Generated']}"
        with st.expander(label):
            st.markdown(s["Content"])
            st.caption(f"Model: {s['Model']}")
            st.download_button(
                "⬇ Export",
                s["Content"].encode("utf-8"),
                f"summary_{s['summary_id']}.md",
                "text/markdown",
                key=f"dl_{s['summary_id']}",
            )
