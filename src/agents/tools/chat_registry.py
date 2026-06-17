"""Build the tool registry used by the chatbot agent."""

from src.agents.runtime.registry import ToolRegistry
from src.agents.runtime.types import ToolSpec
from src.agents.tools.forecast import get_forecast
from src.agents.tools.gold import (
    compare_countries,
    get_indicator_timeseries,
    search_gold_records,
)
from src.agents.tools.lineage import explain_data_lineage
from src.agents.tools.news import search_news


def build_chat_tool_registry() -> ToolRegistry:
    """Register all tools available to the chat orchestrator."""
    registry = ToolRegistry()

    registry.register(
        ToolSpec(
            name="search_gold_records",
            description="Semantic search over verified macro indicator gold records",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Natural language search query"},
                    "limit": {"type": "integer", "description": "Max records to return", "default": 6},
                },
                "required": ["query"],
            },
        ),
        search_gold_records,
    )

    registry.register(
        ToolSpec(
            name="get_indicator_timeseries",
            description="Get historical values for an indicator in a country",
            parameters={
                "type": "object",
                "properties": {
                    "indicator_code": {"type": "string", "description": "e.g. CPI_INFLATION, GDP_GROWTH"},
                    "country_code": {"type": "string", "description": "ISO3 country code e.g. USA, DEU"},
                    "year_from": {"type": "integer"},
                    "year_to": {"type": "integer"},
                    "limit": {"type": "integer", "default": 50},
                },
                "required": ["indicator_code", "country_code"],
            },
        ),
        get_indicator_timeseries,
    )

    registry.register(
        ToolSpec(
            name="compare_countries",
            description="Compare an indicator across countries (use g7=true for G7 nations)",
            parameters={
                "type": "object",
                "properties": {
                    "indicator_code": {"type": "string"},
                    "countries": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "ISO3 codes",
                    },
                    "period": {"type": "string", "description": "Specific period e.g. 2023"},
                    "g7": {"type": "boolean", "description": "Compare all G7 countries"},
                },
                "required": ["indicator_code"],
            },
        ),
        compare_countries,
    )

    registry.register(
        ToolSpec(
            name="get_forecast",
            description="Prophet forecast for future periods of an indicator",
            parameters={
                "type": "object",
                "properties": {
                    "indicator_code": {"type": "string"},
                    "country_code": {"type": "string"},
                    "periods": {"type": "integer", "default": 4},
                },
                "required": ["indicator_code", "country_code"],
            },
        ),
        get_forecast,
    )

    registry.register(
        ToolSpec(
            name="search_news",
            description="Search recent macroeconomic news articles",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "default": 5},
                    "country_code": {"type": "string"},
                },
                "required": ["query"],
            },
        ),
        search_news,
    )

    registry.register(
        ToolSpec(
            name="explain_data_lineage",
            description="Explain provenance of a gold record (bronze→silver→gold chain)",
            parameters={
                "type": "object",
                "properties": {
                    "gold_record_id": {"type": "string", "description": "UUID of the gold record"},
                },
                "required": ["gold_record_id"],
            },
        ),
        explain_data_lineage,
    )

    return registry
