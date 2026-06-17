"""Agent run tracer — persists steps to DB and emits OTel spans."""

import logging
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from src.agents.runtime.types import AgentStep
from src.database import AgentRun, AgentStep as AgentStepModel, AuditLog
from src.utils.observability import get_tracer

logger = logging.getLogger(__name__)
_tracer = get_tracer("macro.agents")


class AgentTracer:
    """Records agent runs and individual steps for audit and debugging."""

    def __init__(
        self,
        db: Session,
        tenant_id: UUID,
        user_id: Optional[UUID],
        agent_name: str,
        session_id: Optional[UUID] = None,
    ) -> None:
        self.db = db
        self.tenant_id = tenant_id
        self.user_id = user_id
        self.agent_name = agent_name
        self.session_id = session_id
        self._run: Optional[AgentRun] = None
        self._step_index = 0

    def start_run(self, query: str) -> UUID:
        self._run = AgentRun(
            tenant_id=self.tenant_id,
            user_id=self.user_id,
            session_id=self.session_id,
            agent_name=self.agent_name,
            query=query,
            status="running",
        )
        self.db.add(self._run)
        self.db.flush()
        return self._run.run_id

    def record_step(self, step: AgentStep) -> None:
        if not self._run:
            return
        with _tracer.start_as_current_span(f"agent.{step.step_type}") as span:
            span.set_attribute("agent.step_index", self._step_index)
            span.set_attribute("agent.step_type", step.step_type)
            row = AgentStepModel(
                run_id=self._run.run_id,
                step_index=self._step_index,
                step_type=step.step_type,
                payload=step.payload,
            )
            self.db.add(row)
            self.db.flush()
            self._step_index += 1

    def complete_run(
        self,
        response: str,
        model_used: str,
        confidence: str,
        grounding_warnings: list[str],
        context_record_ids: list[str],
        status: str = "completed",
    ) -> None:
        if not self._run:
            return
        self._run.response = response
        self._run.model_used = model_used
        self._run.confidence = confidence
        self._run.grounding_warnings = grounding_warnings
        self._run.context_record_ids = context_record_ids
        self._run.status = status
        self._run.completed_at = datetime.now(timezone.utc)

        audit = AuditLog(
            tenant_id=self.tenant_id,
            table_name="agent_runs",
            record_id=self._run.run_id,
            action="INSERT",
            actor=self.agent_name,
            new_values={
                "confidence": confidence,
                "model_used": model_used,
                "status": status,
                "step_count": self._step_index,
            },
            reason=f"Agent run completed with confidence={confidence}",
        )
        self.db.add(audit)

    def get_run_id(self) -> Optional[str]:
        return str(self._run.run_id) if self._run else None

    def get_run(self) -> Optional[AgentRun]:
        return self._run
