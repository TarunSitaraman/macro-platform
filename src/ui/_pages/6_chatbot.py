"""Chatbot — multi-turn RAG conversation with citation and guardrails."""

import asyncio

import streamlit as st

from src.agents.chatbot import SUGGESTED_QUESTIONS, ChatbotAgent
from src.database import SessionLocal

st.title("💬 Macro AI Chatbot")
st.caption("Ask questions about macroeconomic indicators — all answers cited from gold data")

# ── Session management ─────────────────────────────────────────────────────────
if "chat_session_id" not in st.session_state:
    st.session_state.chat_session_id = None
if "chat_history" not in st.session_state:
    st.session_state.chat_history = []

col1, col2 = st.columns([3, 1])
with col2:
    if st.button("🗑️ New Session"):
        st.session_state.chat_session_id = None
        st.session_state.chat_history = []
        st.rerun()

# ── Suggested questions ─────────────────────────────────────────────────────────
if not st.session_state.chat_history:
    st.subheader("💡 Try asking:")
    for q in SUGGESTED_QUESTIONS:
        if st.button(q, key=f"sq_{q}"):
            st.session_state["_pending_message"] = q

# ── Chat history display ────────────────────────────────────────────────────────
for msg in st.session_state.chat_history:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg.get("context_records"):
            with st.expander(f"📚 {len(msg['context_records'])} source records used"):
                for rid in msg["context_records"]:
                    st.caption(f"Gold record: {rid}")

# ── Input ───────────────────────────────────────────────────────────────────────
pending = st.session_state.pop("_pending_message", None)
user_input = st.chat_input("Ask about GDP, inflation, unemployment...") or pending

if user_input:
    st.session_state.chat_history.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)

    with st.chat_message("assistant"):
        with st.spinner("Retrieving data and generating response..."):
            db = SessionLocal()
            try:
                tenant_id = st.session_state.tenant_id
                user_id = st.session_state.user_id
                agent = ChatbotAgent(db, tenant_id=tenant_id, user_id=user_id)
                result = asyncio.run(
                    agent.chat(
                        session_id=st.session_state.chat_session_id,
                        user_message=user_input,
                    )
                )
                st.session_state.chat_session_id = result["session_id"]
            finally:
                db.close()

        st.markdown(result["response"])

        if result.get("guardrail_triggered"):
            st.warning("⚠️ Guardrail triggered — out-of-scope or investment advice request")

        if result.get("context_records"):
            with st.expander(f"📚 {len(result['context_records'])} gold records used as context"):
                for rid in result["context_records"]:
                    st.caption(f"Record: {rid}")

        model_label = f"*Model: {result.get('model_used', 'unknown')}*"
        st.caption(model_label)

    st.session_state.chat_history.append({
        "role": "assistant",
        "content": result["response"],
        "context_records": result.get("context_records", []),
    })

# ── Export ───────────────────────────────────────────────────────────────────────
if st.session_state.chat_history:
    transcript = "\n\n".join(
        f"**{m['role'].upper()}**: {m['content']}"
        for m in st.session_state.chat_history
    )
    st.download_button(
        "⬇ Export Conversation",
        transcript.encode("utf-8"),
        "conversation.md",
        "text/markdown",
    )

# ── Guardrails notice ──────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### ⚙️ Session Info")
    st.caption(f"Session: {st.session_state.chat_session_id or 'Not started'}")
    st.divider()
    st.markdown("### 🛡️ Guardrails")
    st.info(
        "This chatbot is scoped to macroeconomic indicators only. "
        "It will decline investment advice, out-of-scope topics, and unverified claims."
    )
