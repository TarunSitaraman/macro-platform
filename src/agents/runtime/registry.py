"""Tool registry — register and execute tenant-scoped agent tools."""

import json
import logging
from typing import Any, Awaitable, Callable, Optional
from uuid import UUID

from src.agents.runtime.types import ToolResult, ToolSpec

logger = logging.getLogger(__name__)

ToolExecutor = Callable[..., Awaitable[ToolResult]]


class ToolRegistry:
    """Registry of tools available to the agent orchestrator."""

    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec] = {}
        self._executors: dict[str, ToolExecutor] = {}

    def register(self, spec: ToolSpec, executor: ToolExecutor) -> None:
        self._specs[spec.name] = spec
        self._executors[spec.name] = executor

    def get_openai_tools(self) -> list[dict[str, Any]]:
        return [spec.to_openai_schema() for spec in self._specs.values()]

    def get_tool_names(self) -> list[str]:
        return list(self._specs.keys())

    async def execute(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        tenant_id: UUID,
        db: Any,
    ) -> ToolResult:
        if name not in self._executors:
            return ToolResult(
                tool_name=name,
                success=False,
                data=None,
                error=f"Unknown tool: {name}",
            )
        try:
            return await self._executors[name](
                db=db,
                tenant_id=tenant_id,
                **arguments,
            )
        except Exception as exc:
            logger.warning("Tool %s failed: %s", name, exc)
            return ToolResult(
                tool_name=name,
                success=False,
                data=None,
                error=str(exc),
            )

    @staticmethod
    def parse_tool_arguments(raw: Any) -> dict[str, Any]:
        if raw is None:
            return {}
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, str):
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return {}
        return {}
