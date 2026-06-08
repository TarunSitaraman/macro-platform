"""Summary Engine — AI-generated country snapshots and indicator briefs."""

import asyncio

import streamlit as st

from src.agents.summarizer import SummarizerAgent
from src.config import INDICATOR_CATALOGUE, PHASE1_COUNTRIES
from src.database import SessionLocal

st.title("📝 Summary Engine")
st.caption("AI-generated macroeconomic narratives with full data citation")

# ── Generate ───────────────────────────────────────────────────────────────────
st.subheader("Generate Summary")

col1, col2, col3 = st.columns(3)
with col1:
    summary_type = st.selectbox(
        "Summary Type",
        ["COUNTRY_SNAPSHOT", "INDICATOR_BRIEF", "SECTOR_ANALYSIS"],
        format_func=lambda x: {
            "COUNTRY_SNAPSHOT": "🌍 Country Snapshot",
            "INDICATOR_BRIEF": "📈 Indicator Brief",
            "SECTOR_ANALYSIS": "🏭 Sector Analysis",
        }[x],
    )
with col2:
    country_sel = st.selectbox("Country", PHASE1_COUNTRIES)
with col3:
    if summary_type == "INDICATOR_BRIEF":
        ind_sel = st.selectbox("Indicator", list(INDICATOR_CATALOGUE.keys()))
    else:
        ind_sel = None

if st.button("✨ Generate Summary", type="primary"):
    with st.spinner("Generating AI summary..."):
        db = SessionLocal()
        try:
            agent = SummarizerAgent(db)
            if summary_type == "COUNTRY_SNAPSHOT":
                summary = asyncio.run(agent.generate_country_snapshot(country_sel))
            elif summary_type == "INDICATOR_BRIEF":
                summary = asyncio.run(agent.generate_indicator_brief(ind_sel))
            else:
                summary = asyncio.run(agent.generate_sector_analysis(country_sel))
        finally:
            db.close()

    st.success(f"Summary generated using `{summary.model_used}`")
    st.markdown("---")
    st.markdown(summary.content)
    st.caption(f"Generated: {summary.generated_at.strftime('%Y-%m-%d %H:%M UTC')}")

    # Download
    st.download_button(
        "⬇ Download Summary",
        summary.content.encode("utf-8"),
        f"summary_{summary.country_code}_{summary.summary_type}.md",
        "text/markdown",
    )

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
def load_summaries(country, stype):
    db = SessionLocal()
    try:
        agent = SummarizerAgent(db)
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


previous = load_summaries(filter_country, filter_type)
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
