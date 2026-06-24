"""RAG-powered chatbot agent with tool-augmented orchestration and guardrails."""

import logging
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from src.agents.runtime.orchestrator import AgentOrchestrator
from src.agents.tools.chat_registry import build_chat_tool_registry
from src.database import AgentRun, AgentStep as AgentStepModel, ChatMessage, ChatSession

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

SUGGESTED_QUESTIONS = [
    "What is the GDP growth rate of USA in 2023?",
    "Compare inflation rates across G7 countries",
    "Which country has the highest government debt as % of GDP?",
    "Show me unemployment trends in Germany",
    "What is China's current account balance?",
]


class ChatbotAgent:
    """Handles multi-turn agentic conversation with tools, verification, and guardrails."""

    def __init__(self, db: Session, tenant_id: UUID, user_id: Optional[UUID] = None):
        self.db = db
        self.tenant_id = tenant_id
        self.user_id = user_id

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
                    "That topic is outside my scope. I specialise in macroeconomic indicators "
                    "(GDP, inflation, unemployment, trade, fiscal data). How can I help with those?"
                )
        return None

    async def get_or_create_session(self, session_id: Optional[str] = None) -> ChatSession:
        if session_id:
            sess = self.db.query(ChatSession).filter(
                ChatSession.session_id == session_id,
                ChatSession.tenant_id == self.tenant_id,
            ).first()
            if sess:
                if self.user_id and sess.user_id != self.user_id:
                    raise PermissionError("Session does not belong to this user")
                sess.last_active = datetime.now(timezone.utc)
                self.db.flush()
                return sess

        if not self.user_id:
            raise ValueError("user_id is required to create a chat session")

        sess = ChatSession(
            tenant_id=self.tenant_id,
            user_id=self.user_id,
            last_active=datetime.now(timezone.utc),
        )
        self.db.add(sess)
        self.db.flush()
        return sess

    async def chat(
        self, session_id: Optional[str], user_message: str
    ) -> dict:
        """
        Process a user message via the agent orchestrator.
        Returns enriched dict with citations, tool_trace, confidence, and run_id.
        """
        if not user_message or not user_message.strip():
            raise ValueError("Message cannot be empty")

        sess = await self.get_or_create_session(session_id)

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
                "citations": [],
                "tool_trace": [],
                "confidence": "high",
                "grounding_warnings": [],
                "run_id": None,
            }

        history = (
            self.db.query(ChatMessage)
            .filter(ChatMessage.session_id == sess.session_id)
            .order_by(ChatMessage.created_at)
            .all()
        )
        messages = [
            {
                "role": m.role.name if hasattr(m.role, "name") else str(m.role),
                "content": m.content,
            }
            for m in history[-6:]
        ]

        registry = build_chat_tool_registry()
        orchestrator = AgentOrchestrator(
            self.db,
            self.tenant_id,
            registry,
            user_id=self.user_id,
            session_id=sess.session_id,
            agent_name="ChatbotAgent",
        )

        result = await orchestrator.run(user_message, history=messages)

        self._save_message(sess.session_id, "user", user_message)
        ctx_uuids = self._parse_record_uuids(result.context_record_ids)
        self._save_message(
            sess.session_id,
            "assistant",
            result.response,
            context_record_ids=ctx_uuids,
        )
        self.db.commit()

        return {
            "session_id": str(sess.session_id),
            "response": result.response,
            "context_records": result.context_records,
            "model_used": result.model_used,
            "guardrail_triggered": False,
            "suggested_questions": SUGGESTED_QUESTIONS[:3],
            "citations": result.citations,
            "tool_trace": result.tool_trace,
            "confidence": result.confidence,
            "grounding_warnings": result.grounding_warnings,
            "run_id": result.run_id,
        }

    def _parse_record_uuids(self, record_ids: Optional[list]) -> Optional[list]:
        if not record_ids:
            return None
        parsed = []
        for rid in record_ids:
            try:
                parsed.append(UUID(str(rid)))
            except ValueError:
                logger.warning("Skipping invalid context record id: %s", rid)
        return parsed or None

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
            .join(ChatSession)
            .filter(
                ChatMessage.session_id == session_id,
                ChatSession.tenant_id == self.tenant_id,
                ChatSession.user_id == self.user_id,
            )
            .order_by(ChatMessage.created_at)
            .all()
        )
        return [
            {
                "role": m.role.name if hasattr(m.role, "name") else str(m.role),
                "content": m.content,
                "created_at": m.created_at.isoformat(),
            }
            for m in messages
        ]

    def get_agent_run(self, session_id: str, run_id: str) -> Optional[dict]:
        """Fetch a persisted agent run for audit (tenant + user scoped)."""
        run = (
            self.db.query(AgentRun)
            .filter(
                AgentRun.run_id == run_id,
                AgentRun.tenant_id == self.tenant_id,
                AgentRun.user_id == self.user_id,
                AgentRun.session_id == session_id,
            )
            .first()
        )
        if not run:
            return None

        steps = (
            self.db.query(AgentStepModel)
            .filter(AgentStepModel.run_id == run.run_id)
            .order_by(AgentStepModel.step_index)
            .all()
        )
        return {
            "run_id": str(run.run_id),
            "agent_name": run.agent_name,
            "query": run.query,
            "response": run.response,
            "model_used": run.model_used,
            "confidence": run.confidence,
            "grounding_warnings": run.grounding_warnings or [],
            "context_record_ids": run.context_record_ids or [],
            "status": run.status,
            "created_at": run.created_at.isoformat() if run.created_at else None,
            "completed_at": run.completed_at.isoformat() if run.completed_at else None,
            "steps": [
                {
                    "step_index": s.step_index,
                    "step_type": s.step_type,
                    "payload": s.payload,
                    "created_at": s.created_at.isoformat() if s.created_at else None,
                }
                for s in steps
            ],
        }
