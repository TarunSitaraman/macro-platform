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
import requests
from src.config import get_settings
from src.database import SessionLocal, User

settings = get_settings()

st.set_page_config(
    page_title="Macro Intelligence Platform",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Authentication ─────────────────────────────────────────────────────────────

def login():
    st.sidebar.subheader("Authentication")
    email = st.sidebar.text_input("Email")
    password = st.sidebar.text_input("Password", type="password")
    if st.sidebar.button("Login"):
        # For now, verify directly against DB to keep it simple within Streamlit
        db = SessionLocal()
        try:
            from src.utils.auth import verify_password
            user = db.query(User).filter(User.email == email).first()
            if user and verify_password(password, user.hashed_password):
                st.session_state.authenticated = True
                st.session_state.user_id = str(user.user_id)
                st.session_state.tenant_id = str(user.tenant_id)
                st.session_state.user_email = user.email
                st.session_state.user_role = user.role
                st.success(f"Logged in as {user.full_name}")
                st.rerun()
            else:
                st.error("Invalid credentials")
        finally:
            db.close()

def logout():
    if st.sidebar.button("Logout"):
        st.session_state.authenticated = False
        st.session_state.user_id = None
        st.session_state.tenant_id = None
        st.rerun()

if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

if not st.session_state.authenticated:
    st.title("📊 Macro Intelligence Platform")
    st.info("Please login to access the platform.")
    login()
    st.stop()

# ── Navigation ────────────────────────────────────────────────────────────────

page_list = [
    st.Page("_pages/1_overview.py", title="Platform Overview", icon="🏠"),
    st.Page("_pages/2_static_data.py", title="Static Data Product", icon="📥"),
    st.Page("_pages/3_crawler.py", title="Dynamic Crawler", icon="🕷️"),
    st.Page("_pages/4_explorer.py", title="Data Explorer", icon="📊"),
    st.Page("_pages/5_review_queue.py", title="Review Queue", icon="👁️"),
    st.Page("_pages/6_chatbot.py", title="Chatbot", icon="💬"),
    st.Page("_pages/7_summaries.py", title="Summary Engine", icon="📝"),
    st.Page("_pages/8_anomalies.py", title="Anomaly Detection", icon="🚨"),
    st.Page("_pages/9_researcher.py", title="Autonomous Researcher", icon="🕵️"),
]

with st.sidebar:
    st.markdown(f"## {st.session_state.user_email}")
    st.caption(f"Role: {st.session_state.user_role.upper()}")
    logout()
    st.divider()

pg = st.navigation(page_list)
pg.run()
