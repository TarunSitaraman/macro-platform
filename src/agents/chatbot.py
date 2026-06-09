"""RAG-powered chatbot agent with citation enforcement and guardrails."""

import logging
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from src.agents.embeddings import embed_text
from src.agents.llm_client import get_llm_client
from src.database import ChatMessage, ChatSession, GoldRecord

logger = logging.getLogger(__name__)

# Guardrail keyword sets
INVESTMENT_ADVICE_TRIGGERS = {
    "should i buy", "should i sell", "portfolio recommendation", "invest in",
    "buy stocks", "trading advice", "stock picks", "financial advice",
    "should i invest", "where to invest",
}
OUT_OF_SCOPE_TRIGGERS = {
    "weather", "recipe", "sports", "movie", "music", "gaming",
    "celebrity", "dating", "cooking", "fashion",
}

CHAT_SYSTEM = """You are a macroeconomic data analyst assistant for the Macro Intelligence Platform.
You answer questions about macroeconomic indicators: GDP, inflation, unemployment, trade balances, government debt, and related topics.

RULES:
1. ONLY discuss macroeconomic topics. Decline anything else politely.
2. CITE every numeric claim as [Source: <source_name>, <period>].
3. Be concise and factual. Do not speculate beyond the data provided.
4. If data is insufficient, say so clearly.
5. Never give personalized investment advice.

CONTEXT DATA (use this to answer):
{context}
"""

SUGGESTED_QUESTIONS = [
    "What is the GDP growth rate of USA in 2023?",
    "Compare inflation rates across G7 countries",
    "Which country has the highest government debt as % of GDP?",
    "Show me unemployment trends in Germany",
    "What is China's current account balance?",
]


class ChatbotAgent:
    """Handles multi-turn RAG conversation with full citation and guardrails."""

    def __init__(self, db: Session):
        self.db = db

    def _check_guardrails(self, query: str) -> Optional[str]:
        """Return a refusal message if the query violates guardrails, else None."""
        q = query.lower()
        for phrase in INVESTMENT_ADVICE_TRIGGERS:
            if phrase in q:
                return (
                    "I'm a macroeconomic data assistant and cannot provide personalized "
                    "investment advice. For financial decisions, please consult a qualified advisor."
                )
        for phrase in OUT_OF_SCOPE_TRIGGERS:
            if phrase in q:
                return (
                    f"That topic is outside my scope. I specialise in macroeconomic indicators "
                    f"(GDP, inflation, unemployment, trade, fiscal data). How can I help with those?"
                )
        return None

    async def _retrieve_context(self, query: str, limit: int = 6) -> list[GoldRecord]:
        """Vector similarity search to find relevant gold records."""
        try:
            query_embedding = await embed_text(query)
            embedding_str = "[" + ",".join(str(x) for x in query_embedding) + "]"

            raw = self.db.execute(
                text(
                    "SELECT record_id FROM gold_records "
                    "WHERE embedding IS NOT NULL "
                    "ORDER BY embedding <-> CAST(:emb AS vector) "
                    "LIMIT :lim"
                ),
                {"emb": embedding_str, "lim": limit},
            ).fetchall()
            ids = [row[0] for row in raw]
        except Exception as exc:
            logger.warning("Vector search failed (%s), falling back to recent records", exc)
            self.db.rollback()  # reset aborted transaction so fallback query works
            records = (
                self.db.query(GoldRecord)
                .order_by(GoldRecord.promoted_at.desc())
                .limit(limit)
                .all()
            )
            return records

        if not ids:
            return (
                self.db.query(GoldRecord)
                .order_by(GoldRecord.promoted_at.desc())
                .limit(limit)
                .all()
            )

        return self.db.query(GoldRecord).filter(GoldRecord.record_id.in_(ids)).all()

    def _build_context_block(self, records: list[GoldRecord], max_chars: int = 1800) -> str:
        if not records:
            return "No relevant data found in the database."
        lines = []
        for r in records:
            forecast_tag = " [FORECAST]" if r.is_forecast else ""
            lines.append(
                f"- {r.indicator_code} | {r.country_code} | {r.period} | "
                f"{r.value} {r.standard_unit}{forecast_tag} "
                f"[Source: {r.source_name}, {r.period}]"
            )
        block = "\n".join(lines)
        return block[:max_chars]

    async def get_or_create_session(self, session_id: Optional[str] = None) -> ChatSession:
        if session_id:
            sess = self.db.query(ChatSession).filter(
                ChatSession.session_id == session_id
            ).first()
            if sess:
                sess.last_active = datetime.now(timezone.utc)
                self.db.flush()
                return sess

        sess = ChatSession(last_active=datetime.now(timezone.utc))
        self.db.add(sess)
        self.db.flush()
        return sess

    async def chat(
        self, session_id: Optional[str], user_message: str
    ) -> dict:
        """
        Process a user message and return the assistant's response.
        Returns dict: {session_id, response, context_records, model_used, guardrail_triggered}
        """
        sess = await self.get_or_create_session(session_id)

        # Guardrail check
        refusal = self._check_guardrails(user_message)
        if refusal:
            self._save_message(sess.session_id, "user", user_message)
            self._save_message(sess.session_id, "assistant", refusal)
            self.db.commit()
            return {
                "session_id": str(sess.session_id),
                "response": refusal,
                "context_records": [],
                "model_used": "guardrail",
                "guardrail_triggered": True,
            }

        # Retrieve context
        context_records = await self._retrieve_context(user_message)
        context_block = self._build_context_block(context_records)
        context_ids = [str(r.record_id) for r in context_records]

        # Build conversation history
        history = (
            self.db.query(ChatMessage)
            .filter(ChatMessage.session_id == sess.session_id)
            .order_by(ChatMessage.created_at)
            .all()
        )
        messages = [
            {"role": m.role, "content": m.content}
            for m in history[-6:]  # last 6 turns keeps request size manageable on free tiers
        ]
        messages.append({"role": "user", "content": user_message})

        system_prompt = CHAT_SYSTEM.format(context=context_block)

        client = get_llm_client()
        response_text, model_used = await client.chat(
            messages=messages,
            system=system_prompt,
            tier="complex",
        )

        # Persist messages
        self._save_message(sess.session_id, "user", user_message)
        self._save_message(
            sess.session_id, "assistant", response_text,
            tokens_used=None,
            context_record_ids=context_ids,
        )
        self.db.commit()

        return {
            "session_id": str(sess.session_id),
            "response": response_text,
            "context_records": context_ids,
            "model_used": model_used,
            "guardrail_triggered": False,
            "suggested_questions": SUGGESTED_QUESTIONS[:3],
        }

    def _save_message(
        self,
        session_id,
        role: str,
        content: str,
        tokens_used: Optional[int] = None,
        context_record_ids: Optional[list] = None,
    ):
        msg = ChatMessage(
            session_id=session_id,
            role=role,
            content=content,
            tokens_used=tokens_used,
            context_records_used=context_record_ids,
        )
        self.db.add(msg)

    def get_history(self, session_id: str) -> list[dict]:
        messages = (
            self.db.query(ChatMessage)
            .filter(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.created_at)
            .all()
        )
        return [
            {
                "role": m.role,
                "content": m.content,
                "created_at": m.created_at.isoformat(),
            }
            for m in messages
        ]
