"""Agent runtime — orchestration, tools, verification, and tracing."""

from src.agents.runtime.types import AgentRunResult, AgentStep, ToolResult, ToolSpec

__all__ = [
    "AgentOrchestrator",
    "AgentRunResult",
    "AgentStep",
    "ResponseVerifier",
    "ToolRegistry",
    "ToolResult",
    "ToolSpec",
]


def __getattr__(name: str):
    if name == "AgentOrchestrator":
        from src.agents.runtime.orchestrator import AgentOrchestrator
        return AgentOrchestrator
    if name == "ToolRegistry":
        from src.agents.runtime.registry import ToolRegistry
        return ToolRegistry
    if name == "ResponseVerifier":
        from src.agents.runtime.verifier import ResponseVerifier
        return ResponseVerifier
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
