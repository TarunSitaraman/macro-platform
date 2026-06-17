"""Agent orchestrator — plan-act-observe loop with tool calling."""

import json
import logging
from typing import Any, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from src.agents.llm_client import LLMError, get_llm_client
from src.agents.runtime.registry import ToolRegistry
from src.agents.runtime.tracer import AgentTracer
from src.agents.runtime.types import AgentRunResult, AgentStep, ToolResult
from src.agents.runtime.verifier import ResponseVerifier
from src.utils.observability import get_tracer

logger = logging.getLogger(__name__)
_tracer = get_tracer("macro.agents")

AGENT_SYSTEM = """You are a macroeconomic data analyst for the Macro Intelligence Platform.

You have access to tools that query verified gold-layer macro data, forecasts, and news.
Use tools when you need specific data. Call multiple tools if needed to answer thoroughly.

RULES:
1. ONLY discuss macroeconomic topics.
2. CITE every numeric claim as [Source: <source_name>, <period>].
3. Be concise and factual. Do not speculate beyond retrieved data.
4. If data is insufficient after using tools, say so clearly.
5. Never give personalized investment advice.
6. When you have enough data, respond directly without calling more tools.
"""


class AgentOrchestrator:
    """Runs a ReAct-style loop: LLM → tools → observe → repeat."""

    def __init__(
        self,
        db: Session,
        tenant_id: UUID,
        registry: ToolRegistry,
        *,
        user_id: Optional[UUID] = None,
        session_id: Optional[UUID] = None,
        agent_name: str = "ChatbotAgent",
        max_steps: int = 5,
    ) -> None:
        self.db = db
        self.tenant_id = tenant_id
        self.user_id = user_id
        self.session_id = session_id
        self.registry = registry
        self.agent_name = agent_name
        self.max_steps = max_steps
        self.verifier = ResponseVerifier()
        self.tracer = AgentTracer(db, tenant_id, user_id, agent_name, session_id)

    async def run(
        self,
        query: str,
        history: Optional[list[dict[str, str]]] = None,
    ) -> AgentRunResult:
        run_id = self.tracer.start_run(query)
        steps: list[AgentStep] = []
        tool_results: list[ToolResult] = []
        tool_trace: list[dict[str, Any]] = []
        context_record_ids: list[str] = []
        client = get_llm_client()

        messages: list[dict[str, Any]] = list(history or [])
        messages.append({"role": "user", "content": query})

        model_used = "unknown"
        final_response = ""
        status = "completed"

        with _tracer.start_as_current_span("agent.run"):
            for step_num in range(self.max_steps):
                with _tracer.start_as_current_span("agent.plan"):
                    try:
                        completion = await client.chat_with_tools(
                            messages=messages,
                            tools=self.registry.get_openai_tools(),
                            system=AGENT_SYSTEM,
                            tier="complex",
                        )
                    except LLMError:
                        completion = await self._pseudo_tool_completion(
                            messages, client
                        )

                model_used = completion.model_used
                self.tracer.record_step(AgentStep(
                    step_type="llm_call",
                    step_index=step_num,
                    payload={
                        "model_used": model_used,
                        "has_tool_calls": bool(completion.tool_calls),
                    },
                ))
                steps.append(AgentStep(
                    step_type="llm_call",
                    step_index=step_num,
                    payload={"model_used": model_used},
                ))

                if completion.tool_calls:
                    assistant_msg: dict[str, Any] = {
                        "role": "assistant",
                        "content": completion.content or "",
                        "tool_calls": completion.tool_calls,
                    }
                    messages.append(assistant_msg)

                    for tc in completion.tool_calls:
                        fn = tc.get("function", {})
                        tool_name = fn.get("name", "")
                        args = ToolRegistry.parse_tool_arguments(fn.get("arguments"))

                        with _tracer.start_as_current_span("agent.tool_call") as span:
                            span.set_attribute("agent.tool_name", tool_name)
                            result = await self.registry.execute(
                                tool_name,
                                args,
                                tenant_id=self.tenant_id,
                                db=self.db,
                            )

                        tool_results.append(result)
                        context_record_ids.extend(result.record_ids)
                        tool_trace.append({
                            "tool": tool_name,
                            "arguments": args,
                            "success": result.success,
                            "error": result.error,
                            "record_count": len(result.record_ids),
                        })

                        step_payload = {
                            "tool": tool_name,
                            "arguments": args,
                            "success": result.success,
                            "error": result.error,
                        }
                        self.tracer.record_step(AgentStep(
                            step_type="tool_call",
                            step_index=step_num,
                            payload=step_payload,
                        ))
                        steps.append(AgentStep(
                            step_type="tool_call",
                            step_index=step_num,
                            payload=step_payload,
                        ))

                        tool_content = json.dumps({
                            "success": result.success,
                            "data": result.data,
                            "error": result.error,
                        }, default=str)
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.get("id", f"call_{tool_name}_{step_num}"),
                            "content": tool_content[:8000],
                        })
                    continue

                if completion.content:
                    final_response = completion.content
                    break

            if not final_response:
                status = "max_steps"
                final_response = (
                    "I was unable to complete a full analysis within the allowed steps. "
                    "Please try a more specific question."
                )

            confidence, warnings, citations = self.verifier.verify(
                final_response, tool_results
            )

            if confidence == "low" and tool_results:
                revised = await self._attempt_revise(
                    client, messages, final_response, warnings
                )
                if revised:
                    final_response = revised
                    confidence, warnings, citations = self.verifier.verify(
                        final_response, tool_results
                    )
                    self.tracer.record_step(AgentStep(
                        step_type="revise",
                        step_index=self.max_steps,
                        payload={"reason": "grounding_revision"},
                    ))

            self.tracer.record_step(AgentStep(
                step_type="verifier",
                step_index=self.max_steps + 1,
                payload={"confidence": confidence, "warnings": warnings},
            ))
            steps.append(AgentStep(
                step_type="verifier",
                step_index=self.max_steps + 1,
                payload={"confidence": confidence, "warnings": warnings},
            ))

            unique_ids = list(dict.fromkeys(context_record_ids))
            self.tracer.complete_run(
                response=final_response,
                model_used=model_used,
                confidence=confidence,
                grounding_warnings=warnings,
                context_record_ids=unique_ids,
                status=status,
            )

        return AgentRunResult(
            response=final_response,
            model_used=model_used,
            steps=steps,
            context_record_ids=unique_ids,
            citations=citations,
            confidence=confidence,
            grounding_warnings=warnings,
            run_id=str(run_id),
            tool_trace=tool_trace,
        )

    async def _pseudo_tool_completion(self, messages, client):
        """JSON-mode fallback when native tool calling is unavailable."""
        tool_list = ", ".join(self.registry.get_tool_names())
        pseudo_system = (
            AGENT_SYSTEM
            + f"\n\nAvailable tools: {tool_list}\n"
            "Respond with JSON: "
            '{"action":"tool_call","tool":"<name>","arguments":{...}} '
            'or {"action":"answer","content":"<response>"}'
        )
        content, model = await client.chat(
            messages=messages,
            system=pseudo_system,
            tier="complex",
            response_format={"type": "json_object"},
        )
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            return type("C", (), {
                "content": content,
                "tool_calls": [],
                "model_used": model,
            })()

        if parsed.get("action") == "tool_call":
            tc = [{
                "id": "pseudo_0",
                "type": "function",
                "function": {
                    "name": parsed.get("tool", ""),
                    "arguments": json.dumps(parsed.get("arguments", {})),
                },
            }]
            return type("C", (), {
                "content": None,
                "tool_calls": tc,
                "model_used": model,
            })()

        return type("C", (), {
            "content": parsed.get("content", content),
            "tool_calls": [],
            "model_used": model,
        })()

    async def _attempt_revise(
        self,
        client,
        messages: list[dict],
        response: str,
        warnings: list[str],
    ) -> Optional[str]:
        revise_prompt = (
            "Your previous answer had grounding issues:\n"
            + "\n".join(f"- {w}" for w in warnings)
            + "\n\nRevise using ONLY the data from tool results. "
            "Cite every number as [Source: name, period]."
        )
        revise_messages = messages + [
            {"role": "assistant", "content": response},
            {"role": "user", "content": revise_prompt},
        ]
        try:
            revised, _ = await client.chat(
                messages=revise_messages,
                system=AGENT_SYSTEM,
                tier="complex",
            )
            return revised
        except LLMError:
            return None
