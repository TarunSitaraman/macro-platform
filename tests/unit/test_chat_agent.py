"""Unit tests for the tool-augmented chatbot agent."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from src.agents.chatbot import ChatbotAgent
from src.agents.runtime.types import AgentRunResult


@pytest.fixture
def mock_db():
    db = MagicMock()
    db.add = MagicMock()
    db.flush = MagicMock()
    db.commit = MagicMock()
    db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
    return db


@pytest.mark.asyncio
async def test_guardrail_blocks_investment_advice(mock_db):
    tenant_id = uuid4()
    user_id = uuid4()
    agent = ChatbotAgent(mock_db, tenant_id=tenant_id, user_id=user_id)

    with patch.object(agent, "get_or_create_session", new_callable=AsyncMock) as mock_sess:
        sess = MagicMock()
        sess.session_id = uuid4()
        mock_sess.return_value = sess

        result = await agent.chat(None, "should i buy tech stocks?")
        assert result["guardrail_triggered"] is True
        assert result["model_used"] == "guardrail"
        mock_db.commit.assert_called_once()


@pytest.mark.asyncio
@patch("src.agents.chatbot.AgentOrchestrator")
@patch("src.agents.chatbot.build_chat_tool_registry")
async def test_chat_uses_orchestrator(mock_registry, mock_orchestrator_cls, mock_db):
    tenant_id = uuid4()
    user_id = uuid4()
    agent = ChatbotAgent(mock_db, tenant_id=tenant_id, user_id=user_id)

    sess = MagicMock()
    sess.session_id = uuid4()

    with patch.object(agent, "get_or_create_session", new_callable=AsyncMock) as mock_sess:
        mock_sess.return_value = sess

        mock_orch = AsyncMock()
        mock_orch.run = AsyncMock(return_value=AgentRunResult(
            response="GDP grew steadily in the current period [Source: World Bank, 2023]. This represents a steady growth rate compared to the previous year.",
            model_used="test/model",
            steps=[],
            context_record_ids=[str(uuid4())],
            citations=[{"source_name": "World Bank", "period": "2023"}],
            confidence="high",
            grounding_warnings=[],
            run_id="run-123",
            tool_trace=[{"tool": "search_gold_records", "success": True, "record_count": 1}],
            context_records=[{
                "record_id": str(uuid4()),
                "type": "gold",
                "source_name": "World Bank",
                "indicator_code": "GDP_GROWTH",
                "country_code": "USA",
                "period": "2023",
                "value": 2.1,
                "unit": "%",
            }],
        ))
        mock_orchestrator_cls.return_value = mock_orch

        result = await agent.chat(str(sess.session_id), "What is US GDP growth?")

        assert result["confidence"] == "high"
        assert result["run_id"] == "run-123"
        assert len(result["tool_trace"]) == 1
        assert len(result["context_records"]) == 1
        mock_orch.run.assert_called_once()
        assert mock_db.commit.call_count >= 1
