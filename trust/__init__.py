"""
Trustworthy AI Framework — TrustLayer orchestrator.

Wires all nine trust pillars into a single FastAPI middleware chain and
registers all pillar routers.  Import and call ``TrustLayer.mount(app)``
from the FastAPI application factory.

Middleware order (outermost → innermost):
  1. APIKeyAuth          — identity resolution (Pillar 2)
  2. RateLimitMiddleware — quota enforcement (Pillar 2)
  3. ConsentMiddleware   — GDPR consent gate (Pillar 4)
  4. QuerySanitizer      — PII redaction (Pillar 4) [applied in route logic]
  5. GuardrailEngine     — safety filters (Pillar 3) [applied in route logic]
  6. OutputValidator     — citation/accuracy checks (Pillar 3) [applied in route logic]
  7. CitationFormatter   — inline citation injection (Pillar 8) [applied in route logic]
  8. CoverageDisclosure  — coverage notice (Pillar 9) [applied in route logic]

Pillars 1, 5, 6, 7 run as background jobs and are registered via their
APScheduler job functions rather than as HTTP middleware.
"""

from fastapi import FastAPI
import structlog

logger = structlog.get_logger().bind(module="trust")

# ── Pillar 1 — Reliability ────────────────────────────────────────────────────
from trust.reliability.health_check import router as health_router
from trust.reliability.sla_monitor import router as sla_router
from trust.reliability.extraction_thresholds import router as threshold_router

# ── Pillar 2 — Security ───────────────────────────────────────────────────────
from trust.security.auth import router as auth_router
from trust.security.rate_limiter import RateLimitMiddleware
from trust.security.bot_detection import router as bot_detection_router

# ── Pillar 3 — Safety ─────────────────────────────────────────────────────────
from trust.safety.guardrails import GuardrailEngine
from trust.safety.output_validator import OutputValidator

# ── Pillar 4 — Privacy ────────────────────────────────────────────────────────
from trust.privacy.pii_scanner import router as privacy_router
from trust.privacy.consent_manager import router as consent_router

# ── Pillar 5 — Sustainability ─────────────────────────────────────────────────
from trust.sustainability.cost_tracker import router as cost_router
from trust.sustainability.crawl_optimizer import router as crawl_optimizer_router
from trust.sustainability.resource_profiler import router as resource_router

# ── Pillar 6 — Explainability ─────────────────────────────────────────────────
from trust.explainability.source_selector import router as source_selector_router
from trust.explainability.anomaly_explainer import router as anomaly_router
from trust.explainability.quality_score_breakdown import router as quality_breakdown_router
from trust.explainability.llm_trace import router as llm_trace_router

# ── Pillar 7 — Data Quality ───────────────────────────────────────────────────
from trust.data_quality.conflict_resolver import router as conflict_router
from trust.data_quality.revision_tracker import router as revision_router
from trust.data_quality.scorecard import router as scorecard_router

# ── Pillar 8 — Transparency ───────────────────────────────────────────────────
from trust.transparency.citation_trail import router as citation_router
from trust.transparency.governance_artefacts import router as governance_router

# ── Pillar 9 — Fairness ───────────────────────────────────────────────────────
from trust.fairness.coverage_disclosure import router as coverage_router
from trust.fairness.accountability_chain import router as accountability_router
from trust.fairness.human_oversight import router as oversight_router
from trust.fairness.bias_monitor import router as bias_router


class TrustLayer:
    """
    Single entry point for mounting the entire Trustworthy AI Framework.

    Usage::

        from trust import TrustLayer
        TrustLayer.mount(app)
    """

    @staticmethod
    def mount(app: FastAPI) -> None:
        """
        Add all trust middleware and register all pillar routers onto *app*.

        Starlette executes middleware in LIFO order (last added = outermost),
        so we add them in reverse of the desired execution order.
        """
        # RateLimitMiddleware wraps every request after identity has been set
        # by the APIKeyAuth dependency inside each protected route.
        app.add_middleware(RateLimitMiddleware)

        # ── Pillar 1: Reliability ─────────────────────────────────────────────
        app.include_router(health_router, tags=["Trust – Reliability"])
        app.include_router(sla_router, tags=["Trust – Reliability"])
        app.include_router(threshold_router, tags=["Trust – Reliability"])

        # ── Pillar 2: Security ────────────────────────────────────────────────
        app.include_router(auth_router, tags=["Trust – Security"])
        app.include_router(bot_detection_router, tags=["Trust – Security"])

        # ── Pillar 4: Privacy ─────────────────────────────────────────────────
        app.include_router(privacy_router, tags=["Trust – Privacy"])
        app.include_router(consent_router, tags=["Trust – Privacy"])

        # ── Pillar 5: Sustainability ──────────────────────────────────────────
        app.include_router(cost_router, tags=["Trust – Sustainability"])
        app.include_router(crawl_optimizer_router, tags=["Trust – Sustainability"])
        app.include_router(resource_router, tags=["Trust – Sustainability"])

        # ── Pillar 6: Explainability ──────────────────────────────────────────
        app.include_router(source_selector_router, tags=["Trust – Explainability"])
        app.include_router(anomaly_router, tags=["Trust – Explainability"])
        app.include_router(quality_breakdown_router, tags=["Trust – Explainability"])
        app.include_router(llm_trace_router, tags=["Trust – Explainability"])

        # ── Pillar 7: Data Quality ────────────────────────────────────────────
        app.include_router(conflict_router, tags=["Trust – Data Quality"])
        app.include_router(revision_router, tags=["Trust – Data Quality"])
        app.include_router(scorecard_router, tags=["Trust – Data Quality"])

        # ── Pillar 8: Transparency ────────────────────────────────────────────
        app.include_router(citation_router, tags=["Trust – Transparency"])
        app.include_router(governance_router, tags=["Trust – Transparency"])

        # ── Pillar 9: Fairness ────────────────────────────────────────────────
        app.include_router(coverage_router, tags=["Trust – Fairness"])
        app.include_router(accountability_router, tags=["Trust – Fairness"])
        app.include_router(oversight_router, tags=["Trust – Fairness"])
        app.include_router(bias_router, tags=["Trust – Fairness"])

        logger.info("trust_layer_mounted", pillar_count=9, router_count=22)


__all__ = [
    "TrustLayer",
    "GuardrailEngine",
    "OutputValidator",
]
