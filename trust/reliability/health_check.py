"""Pillar 1 — Reliability: Health check endpoints for system observability.

Satisfies SOX internal controls monitoring.
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Literal

import asyncpg
import httpx
import structlog
from fastapi import APIRouter

from src.config import get_settings

logger = structlog.get_logger().bind(pillar="reliability")

router = APIRouter(prefix="/health", tags=["health"])


# ── Data types ────────────────────────────────────────────────────────────────

@dataclass
class HealthStatus:
    status: Literal["ok", "degraded", "down"]
    latency_ms: float
    detail: str


# ── Individual checks ─────────────────────────────────────────────────────────

async def check_database(timeout_s: float = 5.0) -> HealthStatus:
    """Connect to Neon PostgreSQL via asyncpg and run SELECT 1."""
    settings = get_settings()
    start = asyncio.get_event_loop().time()
    try:
        conn = await asyncio.wait_for(
            asyncpg.connect(settings.database_url),
            timeout=timeout_s,
        )
        try:
            await asyncio.wait_for(conn.fetchval("SELECT 1"), timeout=timeout_s)
        finally:
            await conn.close()
        latency_ms = (asyncio.get_event_loop().time() - start) * 1000
        return HealthStatus(status="ok", latency_ms=round(latency_ms, 2), detail="SELECT 1 succeeded")
    except asyncio.TimeoutError:
        latency_ms = (asyncio.get_event_loop().time() - start) * 1000
        logger.warning("health_check_timeout", check="database", timeout_s=timeout_s)
        return HealthStatus(status="down", latency_ms=round(latency_ms, 2), detail="Connection timed out")
    except Exception as exc:
        latency_ms = (asyncio.get_event_loop().time() - start) * 1000
        logger.warning("health_check_failed", check="database", error=str(exc))
        return HealthStatus(status="down", latency_ms=round(latency_ms, 2), detail=str(exc))


async def check_pgvector(timeout_s: float = 5.0) -> HealthStatus:
    """Verify pgvector is active by counting rows with a non-null embedding."""
    settings = get_settings()
    start = asyncio.get_event_loop().time()
    try:
        conn = await asyncio.wait_for(
            asyncpg.connect(settings.database_url),
            timeout=timeout_s,
        )
        try:
            count = await asyncio.wait_for(
                conn.fetchval(
                    "SELECT COUNT(*) FROM gold_records WHERE embedding IS NOT NULL"
                ),
                timeout=timeout_s,
            )
        finally:
            await conn.close()
        latency_ms = (asyncio.get_event_loop().time() - start) * 1000
        return HealthStatus(
            status="ok",
            latency_ms=round(latency_ms, 2),
            detail=f"pgvector active; {count} embedded gold records",
        )
    except asyncio.TimeoutError:
        latency_ms = (asyncio.get_event_loop().time() - start) * 1000
        logger.warning("health_check_timeout", check="pgvector", timeout_s=timeout_s)
        return HealthStatus(status="down", latency_ms=round(latency_ms, 2), detail="Query timed out")
    except Exception as exc:
        latency_ms = (asyncio.get_event_loop().time() - start) * 1000
        logger.warning("health_check_failed", check="pgvector", error=str(exc))
        return HealthStatus(status="down", latency_ms=round(latency_ms, 2), detail=str(exc))


async def check_scheduler(timeout_s: float = 5.0) -> HealthStatus:
    """Check whether APScheduler has a running scheduler singleton."""
    start = asyncio.get_event_loop().time()
    try:
        from apscheduler.schedulers.background import BackgroundScheduler  # type: ignore

        # Attempt to locate a running scheduler via the orchestration module
        try:
            from src.orchestration import scheduler as _sched  # type: ignore
            running = getattr(_sched, "running", False)
            latency_ms = (asyncio.get_event_loop().time() - start) * 1000
            if running:
                return HealthStatus(
                    status="ok",
                    latency_ms=round(latency_ms, 2),
                    detail="APScheduler running",
                )
            return HealthStatus(
                status="degraded",
                latency_ms=round(latency_ms, 2),
                detail="APScheduler imported but not running",
            )
        except ImportError:
            latency_ms = (asyncio.get_event_loop().time() - start) * 1000
            return HealthStatus(
                status="degraded",
                latency_ms=round(latency_ms, 2),
                detail="No scheduler singleton found in src.orchestration",
            )
    except ImportError as exc:
        latency_ms = (asyncio.get_event_loop().time() - start) * 1000
        logger.warning("health_check_failed", check="scheduler", error=str(exc))
        return HealthStatus(
            status="degraded",
            latency_ms=round(latency_ms, 2),
            detail=f"APScheduler not importable: {exc}",
        )
    except Exception as exc:
        latency_ms = (asyncio.get_event_loop().time() - start) * 1000
        logger.warning("health_check_failed", check="scheduler", error=str(exc))
        return HealthStatus(status="down", latency_ms=round(latency_ms, 2), detail=str(exc))


async def check_llm_providers(timeout_s: float = 5.0) -> Dict[str, HealthStatus]:
    """Probe each configured LLM provider's base URL with a lightweight GET."""
    settings = get_settings()

    providers = {
        "groq": settings.groq_base_url,
        "gemini": settings.gemini_base_url,
        "openrouter": settings.openrouter_base_url,
    }

    async def probe(name: str, base_url: str) -> tuple[str, HealthStatus]:
        start = asyncio.get_event_loop().time()
        # Strip path components so we only hit the root — avoids 404 on /openai/v1
        from urllib.parse import urlparse
        parsed = urlparse(base_url)
        probe_url = f"{parsed.scheme}://{parsed.netloc}/"
        try:
            async with httpx.AsyncClient(timeout=timeout_s) as client:
                resp = await asyncio.wait_for(
                    client.get(probe_url),
                    timeout=timeout_s,
                )
            latency_ms = (asyncio.get_event_loop().time() - start) * 1000
            # Any HTTP response (even 401/403) means the provider is reachable
            status: Literal["ok", "degraded", "down"] = "ok" if resp.status_code < 500 else "degraded"
            return name, HealthStatus(
                status=status,
                latency_ms=round(latency_ms, 2),
                detail=f"HTTP {resp.status_code}",
            )
        except asyncio.TimeoutError:
            latency_ms = (asyncio.get_event_loop().time() - start) * 1000
            logger.warning("health_check_timeout", check=f"llm_{name}", timeout_s=timeout_s)
            return name, HealthStatus(status="down", latency_ms=round(latency_ms, 2), detail="Timed out")
        except Exception as exc:
            latency_ms = (asyncio.get_event_loop().time() - start) * 1000
            logger.warning("health_check_failed", check=f"llm_{name}", error=str(exc))
            return name, HealthStatus(status="down", latency_ms=round(latency_ms, 2), detail=str(exc))

    results = await asyncio.gather(*[probe(n, u) for n, u in providers.items()])
    return dict(results)


# ── Status aggregation ────────────────────────────────────────────────────────

_STATUS_RANK: Dict[str, int] = {"ok": 0, "degraded": 1, "down": 2}


def _worst_status(*statuses: str) -> Literal["ok", "degraded", "down"]:
    ranked = sorted(statuses, key=lambda s: _STATUS_RANK.get(s, 0), reverse=True)
    return ranked[0]  # type: ignore[return-value]


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/")
async def health_basic():
    """Fast health check: database, pgvector, and scheduler in parallel."""
    db_status, pgv_status, sched_status = await asyncio.gather(
        check_database(),
        check_pgvector(),
        check_scheduler(),
    )

    checks = {
        "database": {"status": db_status.status, "latency_ms": db_status.latency_ms, "detail": db_status.detail},
        "pgvector": {"status": pgv_status.status, "latency_ms": pgv_status.latency_ms, "detail": pgv_status.detail},
        "scheduler": {"status": sched_status.status, "latency_ms": sched_status.latency_ms, "detail": sched_status.detail},
    }
    overall = _worst_status(db_status.status, pgv_status.status, sched_status.status)

    logger.info("health_check_basic", overall=overall)
    return {
        "status": overall,
        "checks": checks,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/deep")
async def health_deep():
    """Deep health check: all checks including LLM provider probes."""
    db_status, pgv_status, sched_status, llm_statuses = await asyncio.gather(
        check_database(),
        check_pgvector(),
        check_scheduler(),
        check_llm_providers(),
    )

    checks: dict = {
        "database": {"status": db_status.status, "latency_ms": db_status.latency_ms, "detail": db_status.detail},
        "pgvector": {"status": pgv_status.status, "latency_ms": pgv_status.latency_ms, "detail": pgv_status.detail},
        "scheduler": {"status": sched_status.status, "latency_ms": sched_status.latency_ms, "detail": sched_status.detail},
        "llm_providers": {
            name: {"status": hs.status, "latency_ms": hs.latency_ms, "detail": hs.detail}
            for name, hs in llm_statuses.items()
        },
    }

    all_statuses = [
        db_status.status,
        pgv_status.status,
        sched_status.status,
        *[hs.status for hs in llm_statuses.values()],
    ]
    overall = _worst_status(*all_statuses)

    logger.info("health_check_deep", overall=overall)
    return {
        "status": overall,
        "checks": checks,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
