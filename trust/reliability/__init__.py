"""trust.reliability — Pillar 1: Reliability.

Exports the primary classes and helpers for retry policies, circuit breakers,
health checks, SLA monitoring, and extraction threshold decisions.
"""

from trust.reliability.retry_policy import (
    RetryPolicy,
    with_retry,
    CircuitBreaker,
    CircuitBreakerOpenError,
    circuit_breaker,
    get_circuit_breaker,
    all_circuit_breaker_states,
)
from trust.reliability.health_check import (
    HealthStatus,
    check_database,
    check_pgvector,
    check_scheduler,
    check_llm_providers,
    router as health_router,
)
from trust.reliability.sla_monitor import (
    SLATier,
    SLA_WINDOWS,
    TIER_INDICATORS,
    SLAViolation,
    SLAStatus,
    SLAMonitor,
    router as sla_router,
)
from trust.reliability.extraction_thresholds import (
    ThresholdDecision,
    ExtractionThreshold,
    ExtractionDecision,
    evaluate_extraction,
    router as thresholds_router,
)

__all__ = [
    # retry_policy
    "RetryPolicy",
    "with_retry",
    "CircuitBreaker",
    "CircuitBreakerOpenError",
    "circuit_breaker",
    "get_circuit_breaker",
    "all_circuit_breaker_states",
    # health_check
    "HealthStatus",
    "check_database",
    "check_pgvector",
    "check_scheduler",
    "check_llm_providers",
    "health_router",
    # sla_monitor
    "SLATier",
    "SLA_WINDOWS",
    "TIER_INDICATORS",
    "SLAViolation",
    "SLAStatus",
    "SLAMonitor",
    "sla_router",
    # extraction_thresholds
    "ThresholdDecision",
    "ExtractionThreshold",
    "ExtractionDecision",
    "evaluate_extraction",
    "thresholds_router",
]
