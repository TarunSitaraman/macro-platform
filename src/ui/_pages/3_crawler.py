"""Dynamic Crawler — trigger HTML source crawling and view extractions."""

import asyncio

import pandas as pd
import streamlit as st

from src.agents.crawler import CURATED_ARTICLES, DynamicCrawlerAgent
from src.config import INDICATOR_CATALOGUE
from src.database import SessionLocal, SourceConfig

st.title("🕷️ Dynamic Crawler")
st.caption("Crawl HTML sources and extract macroeconomic data using AI")

# ── Source selector ─────────────────────────────────────────────────────────────
db = SessionLocal()
try:
    html_sources = (
        db.query(SourceConfig)
        .filter(SourceConfig.source_type.in_(["HTML", "PDF"]))
        .all()
    )
finally:
    db.close()

if not html_sources:
    st.warning("No HTML/PDF sources configured. Add sources in source_config table.")
    st.stop()

source_map = {s.source_name: s for s in html_sources}
selected_name = st.selectbox("Select Source", list(source_map.keys()))
source = source_map[selected_name]

col1, col2 = st.columns(2)
col1.markdown(f"**URL:** [{source.source_url}]({source.source_url})")
col2.markdown(f"**Frequency:** {source.frequency} | **Reputation:** {source.reputation_score}/100")

if source.extraction_prompt:
    with st.expander("🔍 Extraction Prompt"):
        st.code(source.extraction_prompt, language="text")

st.divider()

# ── Article discovery ───────────────────────────────────────────────────────────
with st.expander("🔍 Browse Articles", expanded=True):
    curated = CURATED_ARTICLES.get(source.source_code, [])
    col_disc, col_info = st.columns([2, 3])
    with col_disc:
        run_discovery = st.button("Discover from Source Page", use_container_width=True)
    with col_info:
        st.caption("Scans the source index page for article links. Falls back to curated list.")

    articles: list[dict] = []
    if run_discovery:
        with st.spinner("Scanning source page for articles..."):
            crawler_tmp = DynamicCrawlerAgent()
            articles = asyncio.run(
                crawler_tmp.discover_articles(source.source_url, source.source_code)
            )
        if articles:
            st.success(f"Found {len(articles)} articles")
        else:
            st.info("Dynamic discovery returned nothing — showing curated list.")
            articles = curated
    elif curated:
        articles = curated

    if articles:
        st.markdown("**Select an article to crawl:**")
        for art in articles:
            c1, c2 = st.columns([5, 1])
            c1.markdown(f"[{art['title']}]({art['url']})", unsafe_allow_html=False)
            if c2.button("Use", key=f"use_{art['url']}", use_container_width=True):
                st.session_state["selected_crawl_url"] = art["url"]
                st.rerun()

st.divider()

# ── Manual URL crawl ────────────────────────────────────────────────────────────
st.subheader("Crawl & Extract")
_default_url = st.session_state.pop("selected_crawl_url", "")
custom_url = st.text_input("URL to crawl (leave blank to use source URL)", value=_default_url)
url_to_crawl = custom_url.strip() if custom_url.strip() else source.source_url

if st.button("🕷 Run Crawler", type="primary"):
    with st.spinner(f"Crawling {url_to_crawl}..."):
        crawler = DynamicCrawlerAgent()
        try:
            extracted = asyncio.run(
                crawler.crawl_and_extract(
                    url=url_to_crawl,
                    extraction_prompt=source.extraction_prompt,
                )
            )
        except Exception as e:
            st.error(f"Crawler error: {e}")
            extracted = []

    if extracted:
        st.success(f"Extracted {len(extracted)} indicator records")
        df = pd.DataFrame(extracted)
        st.dataframe(df, use_container_width=True, hide_index=True)

        # Run through pipeline
        if st.button("▶ Push Extracted Records Through Pipeline"):
            from src.agents.pipeline import Pipeline
            db = SessionLocal()
            try:
                pipeline = Pipeline(db)
                promoted = queued = rejected = 0
                for rec in extracted:
                    ind_code = rec.get("indicator_code", "")
                    if ind_code not in INDICATOR_CATALOGUE:
                        rejected += 1
                        continue
                    unit = INDICATOR_CATALOGUE[ind_code]["standard_unit"]
                    try:
                        result = asyncio.run(pipeline.run(
                            source_code=source.source_code,
                            indicator_code=ind_code,
                            country_code=rec.get("country_code", ""),
                            period=str(rec.get("period", "")),
                            raw_value=str(rec.get("raw_value", "")),
                            raw_unit=rec.get("raw_unit", unit),
                            source_url=url_to_crawl,
                            extraction_method="HTML_LLM",
                            raw_json=rec,
                            standard_unit=unit,
                            source_name=source.source_name,
                        ))
                        if result["status"] == "promoted":
                            promoted += 1
                        elif result["status"] == "review":
                            queued += 1
                        else:
                            rejected += 1
                    except Exception as e:
                        rejected += 1
            finally:
                db.close()

            c1, c2, c3 = st.columns(3)
            c1.metric("✅ Promoted", promoted)
            c2.metric("⏳ Queued", queued)
            c3.metric("❌ Rejected", rejected)
    else:
        st.warning("No indicator data extracted from this page. Try a different URL or check the extraction prompt.")

st.divider()

# ── Crawl history ──────────────────────────────────────────────────────────────
st.subheader("Source Run History")
db = SessionLocal()
try:
    sources_all = db.query(SourceConfig).filter(SourceConfig.source_type.in_(["HTML", "PDF"])).all()
    for s in sources_all:
        status = "🟢" if not s.error_message else "🔴"
        last = s.last_run_at.strftime("%Y-%m-%d %H:%M") if s.last_run_at else "Never"
        st.markdown(f"{status} **{s.source_name}** — Last run: `{last}`")
        if s.error_message:
            st.caption(f"Error: {s.error_message}")
finally:
    db.close()
