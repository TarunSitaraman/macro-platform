"""Autonomous Research — deep-dive reports using multi-source agents."""

import asyncio
import os
import streamlit as st
import pandas as pd
from datetime import datetime

from src.agents.researcher import ResearcherAgent
from src.database import SessionLocal
from src.utils.reporting import generate_pdf_report

st.title("🕵️ Autonomous Researcher")
st.caption("Deep-dive macroeconomic reports combining internal gold data and live web search")

# ── Report Generation ──────────────────────────────────────────────────────────
st.subheader("New Research Request")

col1, col2 = st.columns([3, 1])
with col1:
    topic = st.text_input(
        "Research Topic", 
        placeholder="e.g., US Inflation Outlook 2025, impact of BRICS expansion on global trade"
    )
with col2:
    st.markdown("<br>", unsafe_allow_html=True)
    run_research = st.button("🚀 Start Research", use_container_width=True)

if run_research:
    if not topic.strip():
        st.error("Please enter a research topic.")
    else:
        with st.status("Researching...", expanded=True) as status:
            st.write("Initializing Lead Researcher Agent...")
            db = SessionLocal()
            try:
                agent = ResearcherAgent(db, tenant_id=st.session_state.tenant_id)
                
                st.write("🔍 Searching web via DuckDuckGo...")
                st.write("📊 Retrieving internal Gold & Analytics records...")
                st.write("📰 Analyzing recent news sentiment...")
                
                # Run the agent
                report = asyncio.run(agent.compile_report(topic))
                
                st.write("✍️ Synthesizing findings into professional report...")
                
                # Save to session state
                st.session_state.last_report = report
                
                # Generate PDF
                pdf_filename = f"research_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
                pdf_path = os.path.join("temp", pdf_filename)
                os.makedirs("temp", exist_ok=True)
                generate_pdf_report(topic, report["content"], pdf_path)
                st.session_state.last_pdf = pdf_path
                
                status.update(label="Research Complete!", state="complete", expanded=False)
                st.success("✅ Deep-dive report compiled.")
            except Exception as e:
                st.error(f"Research failed: {e}")
            finally:
                db.close()

# ── Display Last Report ────────────────────────────────────────────────────────
if "last_report" in st.session_state:
    report = st.session_state.last_report
    st.divider()
    st.subheader(f"📄 Report: {report['topic']}")
    
    col_meta1, col_meta2 = st.columns(2)
    col_meta1.caption(f"Generated: {report['generated_at']}")
    col_meta2.caption(f"Analyst Brain: {report['model']}")
    
    st.markdown(report["content"])
    
    if "last_pdf" in st.session_state:
        with open(st.session_state.last_pdf, "rb") as f:
            st.download_button(
                "⬇ Download PDF Report",
                f,
                file_name=os.path.basename(st.session_state.last_pdf),
                mime="application/pdf",
                use_container_width=True
            )

st.divider()
st.info(
    "The Autonomous Researcher uses a multi-source intelligence gathering process: "
    "it queries the platform's Gold layer for verified historicals, the News layer for "
    "recent events, and performs real-time web searches to capture the latest market sentiment."
)
