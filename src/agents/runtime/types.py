"""Shared types for the agent runtime."""

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class ToolSpec:
    """OpenAI-compatible tool definition."""

    name: str
    description: str
    parameters: dict[str, Any]

    def to_openai_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass
class ToolResult:
    """Result of executing a single tool."""

    tool_name: str
    success: bool
    data: Any
    error: Optional[str] = None
    record_ids: list[str] = field(default_factory=list)
    # Structured, normalized records backing this result. Used to build
    # citations and rich context records without re-parsing the LLM's prose.
    records: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class AgentStep:
    """One step in an agent run (tool call, LLM turn, verifier, etc.)."""

    step_type: str
    payload: dict[str, Any]
    step_index: int = 0


@dataclass
class LLMCompletion:
    """Structured LLM response including optional tool calls."""

    content: Optional[str]
    tool_calls: list[dict[str, Any]]
    model_used: str


@dataclass
class AgentRunResult:
    """Final output of an agent orchestration run."""

    response: str
    model_used: str
    steps: list[AgentStep]
    context_record_ids: list[str]
    citations: list[dict[str, Any]]
    confidence: str
    grounding_warnings: list[str]
    run_id: Optional[str] = None
    tool_trace: list[dict[str, Any]] = field(default_factory=list)
    # Rich, deduplicated metadata for each record used as context (replaces the
    # bare UUID list for frontends that render source cards).
    context_records: list[dict[str, Any]] = field(default_factory=list)
