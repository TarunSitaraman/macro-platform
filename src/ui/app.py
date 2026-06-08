"""Streamlit main entry point — multipage app."""

import sys
import os

# Ensure project root is on the path so `src.*` imports work from page files
_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
if _root not in sys.path:
    sys.path.insert(0, _root)

# Allow asyncio.run() inside Streamlit's event loop
import nest_asyncio
nest_asyncio.apply()

import streamlit as st

st.set_page_config(
    page_title="Hexaware Macro Platform",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Paths are relative to this file (src/ui/app.py), so _pages/ lives here
page_list = [
    st.Page("_pages/1_overview.py", title="Platform Overview", icon="🏠"),
    st.Page("_pages/2_static_data.py", title="Static Data Product", icon="📥"),
    st.Page("_pages/3_crawler.py", title="Dynamic Crawler", icon="🕷️"),
    st.Page("_pages/4_explorer.py", title="Data Explorer", icon="📊"),
    st.Page("_pages/5_review_queue.py", title="Review Queue", icon="👁️"),
    st.Page("_pages/6_chatbot.py", title="Chatbot", icon="💬"),
    st.Page("_pages/7_summaries.py", title="Summary Engine", icon="📝"),
]

with st.sidebar:
    st.markdown("## Hexaware Macro Platform")
    st.caption("Financial Services Practice")
    st.divider()

pg = st.navigation(page_list)
pg.run()
