"""Unit tests for the tool registry."""

import pytest
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from src.agents.runtime.registry import ToolRegistry
from src.agents.runtime.types import ToolResult, ToolSpec


@pytest.mark.asyncio
async def test_registry_unknown_tool():
    registry = ToolRegistry()
    result = await registry.execute(
        "nonexistent",
        {},
        tenant_id=uuid4(),
        db=MagicMock(),
    )
    assert result.success is False
    assert "Unknown tool" in (result.error or "")


@pytest.mark.asyncio
async def test_registry_executes_registered_tool():
    registry = ToolRegistry()

    async def echo_tool(db, tenant_id, message: str, **kwargs):
        return ToolResult(
            tool_name="echo",
            success=True,
            data={"message": message},
        )

    registry.register(
        ToolSpec(
            name="echo",
            description="echo",
            parameters={
                "type": "object",
                "properties": {"message": {"type": "string"}},
                "required": ["message"],
            },
        ),
        echo_tool,
    )

    result = await registry.execute(
        "echo",
        {"message": "hello"},
        tenant_id=uuid4(),
        db=MagicMock(),
    )
    assert result.success is True
    assert result.data["message"] == "hello"


def test_chat_registry_has_six_tools():
    from src.agents.tools.chat_registry import build_chat_tool_registry

    registry = build_chat_tool_registry()
    names = registry.get_tool_names()
    assert "search_gold_records" in names
    assert "get_indicator_timeseries" in names
    assert "compare_countries" in names
    assert "get_forecast" in names
    assert "search_news" in names
    assert "explain_data_lineage" in names
    assert len(names) == 6


def test_openai_schema_format():
    from src.agents.tools.chat_registry import build_chat_tool_registry

    registry = build_chat_tool_registry()
    tools = registry.get_openai_tools()
    assert all(t["type"] == "function" for t in tools)
    assert all("name" in t["function"] for t in tools)
