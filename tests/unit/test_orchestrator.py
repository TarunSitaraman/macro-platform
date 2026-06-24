"""Unit tests for the agent orchestrator."""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from src.agents.runtime.orchestrator import AgentOrchestrator
from src.agents.runtime.registry import ToolRegistry
from src.agents.runtime.types import LLMCompletion, ToolResult, ToolSpec


@pytest.fixture
def mock_db():
    db = MagicMock()
    db.add = MagicMock()
    db.flush = MagicMock()
    return db


@pytest.fixture
def simple_registry():
    registry = ToolRegistry()

    async def mock_search(db, tenant_id, query: str, **kwargs):
        return ToolResult(
            tool_name="search_gold_records",
            success=True,
            data={"records": [{"value": 3.2, "source_name": "IMF", "period": "2023"}]},
            record_ids=["rec-1"],
            records=[{
                "record_id": "rec-1",
                "type": "gold",
                "source_name": "IMF",
                "indicator_code": "CPI_INFLATION",
                "country_code": "USA",
                "period": "2023",
                "value": 3.2,
                "unit": "%",
            }],
        )

    registry.register(
        ToolSpec(
            name="search_gold_records",
            description="search",
            parameters={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        ),
        mock_search,
    )
    return registry


@pytest.mark.asyncio
@patch("src.agents.runtime.orchestrator.get_llm_client")
async def test_orchestrator_tool_then_answer(mock_get_client, mock_db, simple_registry):
    client = AsyncMock()
    mock_get_client.return_value = client

    client.chat_with_tools = AsyncMock(side_effect=[
        LLMCompletion(
            content=None,
            tool_calls=[{
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": "search_gold_records",
                    "arguments": json.dumps({"query": "US inflation"}),
                },
            }],
            model_used="test/model",
        ),
        LLMCompletion(
            content="US inflation was 3.2% [Source: IMF, 2023].",
            tool_calls=[],
            model_used="test/model",
        ),
    ])

    orchestrator = AgentOrchestrator(
        mock_db,
        uuid4(),
        simple_registry,
        user_id=uuid4(),
        agent_name="TestAgent",
        max_steps=5,
    )

    result = await orchestrator.run("What is US inflation?")
    assert "3.2" in result.response
    assert result.model_used == "test/model"
    assert "rec-1" in result.context_record_ids
    assert len(result.tool_trace) == 1
    assert client.chat_with_tools.call_count == 2
    # Citations and rich context records are built from structured tool data,
    # not parsed from the model's prose.
    assert len(result.context_records) == 1
    assert result.context_records[0]["indicator_code"] == "CPI_INFLATION"
    assert len(result.citations) == 1
    assert result.citations[0]["source_name"] == "IMF"
    assert result.citations[0]["record_id"] == "rec-1"


@pytest.mark.asyncio
@patch("src.agents.runtime.orchestrator.get_llm_client")
async def test_orchestrator_max_steps(mock_get_client, mock_db, simple_registry):
    client = AsyncMock()
    mock_get_client.return_value = client

    client.chat_with_tools = AsyncMock(return_value=LLMCompletion(
        content=None,
        tool_calls=[{
            "id": "call_1",
            "type": "function",
            "function": {
                "name": "search_gold_records",
                "arguments": json.dumps({"query": "test"}),
            },
        }],
        model_used="test/model",
    ))

    orchestrator = AgentOrchestrator(
        mock_db,
        uuid4(),
        simple_registry,
        max_steps=2,
    )

    result = await orchestrator.run("loop forever")
    assert "unable to complete" in result.response.lower() or result.tool_trace
