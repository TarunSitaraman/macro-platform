"""Pillar 1 — Reliability: Retry policies and circuit breakers for all external calls.

Satisfies MiFID II operational resilience requirements.
"""

import asyncio
import functools
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

import structlog
from tenacity import (
    AsyncRetrying,
    RetryError,
    stop_after_attempt,
    wait_exponential,
    wait_random,
    wait_combine,
    before_sleep_log,
    retry_if_exception_type,
)

logger = structlog.get_logger().bind(pillar="reliability")


# ── Retry Policy ──────────────────────────────────────────────────────────────

@dataclass
class RetryPolicy:
    max_attempts: int = 3
    backoff_base: float = 1.0
    backoff_max: float = 60.0
    jitter: bool = True


def with_retry(policy: RetryPolicy):
    """Decorator that wraps async functions with exponential backoff + jitter.

    Logs each retry attempt at WARNING level, then re-raises the final exception.
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            wait_strategy = wait_exponential(
                min=policy.backoff_base,
                max=policy.backoff_max,
            )
            if policy.jitter:
                wait_strategy = wait_combine(wait_strategy, wait_random(0, 1))

            attempt_number = 0

            try:
                async for attempt in AsyncRetrying(
                    stop=stop_after_attempt(policy.max_attempts),
                    wait=wait_strategy,
                    reraise=True,
                ):
                    with attempt:
                        attempt_number += 1
                        if attempt_number > 1:
                            logger.warning(
                                "retry_attempt",
                                function=func.__qualname__,
                                attempt=attempt_number,
                                max_attempts=policy.max_attempts,
                            )
                        return await func(*args, **kwargs)
            except Exception as exc:
                logger.warning(
                    "retry_exhausted",
                    function=func.__qualname__,
                    max_attempts=policy.max_attempts,
                    exception_type=type(exc).__name__,
                    exception=str(exc),
                )
                raise

        return wrapper
    return decorator


# ── Circuit Breaker ───────────────────────────────────────────────────────────

class CircuitBreakerOpenError(Exception):
    """Raised when a call is attempted against an OPEN circuit breaker."""

    def __init__(self, name: str) -> None:
        super().__init__(f"Circuit breaker '{name}' is OPEN — call rejected.")
        self.name = name


_CIRCUIT_BREAKER_REGISTRY: Dict[str, "CircuitBreaker"] = {}


class CircuitBreaker:
    """Three-state circuit breaker: CLOSED → OPEN → HALF_OPEN → CLOSED.

    Thread-safe for use in a single-process async environment (no cross-process
    state sharing). State is kept in memory.
    """

    _CLOSED = "CLOSED"
    _OPEN = "OPEN"
    _HALF_OPEN = "HALF_OPEN"

    def __init__(self, name: str, threshold: int = 5, timeout: int = 60) -> None:
        self.name = name
        self.threshold = threshold
        self.timeout = timeout

        self._failure_count: int = 0
        self._last_failure_time: Optional[float] = None
        self._state: str = self._CLOSED
        self._lock = asyncio.Lock()

    def _transition(self, new_state: str) -> None:
        old_state = self._state
        self._state = new_state
        if old_state != new_state:
            logger.info(
                "circuit_breaker_transition",
                name=self.name,
                from_state=old_state,
                to_state=new_state,
                failure_count=self._failure_count,
                pillar="reliability",
            )

    async def call(self, func: Callable, *args: Any, **kwargs: Any) -> Any:
        """Execute *func* through the circuit breaker.

        Raises CircuitBreakerOpenError immediately when the breaker is OPEN
        and the recovery timeout has not yet elapsed.
        """
        async with self._lock:
            now = time.monotonic()

            if self._state == self._OPEN:
                elapsed = now - (self._last_failure_time or 0)
                if elapsed < self.timeout:
                    raise CircuitBreakerOpenError(self.name)
                # Timeout elapsed — probe with a single attempt
                self._transition(self._HALF_OPEN)

        # Execute outside the lock to avoid holding it during I/O
        try:
            result = await func(*args, **kwargs)
        except Exception as exc:
            async with self._lock:
                self._failure_count += 1
                self._last_failure_time = time.monotonic()
                # Any failure in HALF_OPEN or failure that hits threshold → OPEN
                if self._state == self._HALF_OPEN or self._failure_count >= self.threshold:
                    self._transition(self._OPEN)
                logger.warning(
                    "circuit_breaker_failure",
                    name=self.name,
                    state=self._state,
                    failure_count=self._failure_count,
                    exception_type=type(exc).__name__,
                    pillar="reliability",
                )
            raise

        # Success path
        async with self._lock:
            if self._state == self._HALF_OPEN:
                self._failure_count = 0
                self._last_failure_time = None
                self._transition(self._CLOSED)
            elif self._state == self._CLOSED:
                # Reset failure count on any success in CLOSED state
                self._failure_count = 0

        return result

    def get_state(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "state": self._state,
            "failure_count": self._failure_count,
            "last_failure_time": self._last_failure_time,
            "threshold": self.threshold,
            "timeout_s": self.timeout,
        }


def circuit_breaker(name: str, threshold: int = 5, timeout: int = 60):
    """Decorator factory that wraps an async function with a named CircuitBreaker.

    Circuit breakers are stored in a module-level registry so the same instance
    is reused across calls within a process lifetime.
    """
    def decorator(func: Callable) -> Callable:
        if name not in _CIRCUIT_BREAKER_REGISTRY:
            _CIRCUIT_BREAKER_REGISTRY[name] = CircuitBreaker(
                name=name, threshold=threshold, timeout=timeout
            )
        breaker = _CIRCUIT_BREAKER_REGISTRY[name]

        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            return await breaker.call(func, *args, **kwargs)

        return wrapper
    return decorator


def get_circuit_breaker(name: str) -> Optional[CircuitBreaker]:
    """Return an existing circuit breaker from the registry, or None."""
    return _CIRCUIT_BREAKER_REGISTRY.get(name)


def all_circuit_breaker_states() -> Dict[str, Dict[str, Any]]:
    """Return the state of every registered circuit breaker."""
    return {name: cb.get_state() for name, cb in _CIRCUIT_BREAKER_REGISTRY.items()}
