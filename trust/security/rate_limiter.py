"""Pillar 2 — Security: Token bucket rate limiting backed by PostgreSQL. Satisfies INTERNAL API governance policy."""

import hashlib
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional

import structlog
from fastapi import Depends
from sqlalchemy import Boolean, Column, Date, DateTime, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Session
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from src.database import Base, SessionLocal, get_db

logger = structlog.get_logger().bind(pillar="security")


# ── Tier Config ───────────────────────────────────────────────────────────────

@dataclass
class RateLimitTier:
    daily_limit: int
    burst_per_minute: int


RATE_LIMITS: dict[str, RateLimitTier] = {
    "PUBLIC":             RateLimitTier(daily_limit=10,     burst_per_minute=2),
    "EXTERNAL_BUSINESS":  RateLimitTier(daily_limit=1000,   burst_per_minute=10),
    "INTERNAL_ANALYST":   RateLimitTier(daily_limit=999999, burst_per_minute=999),
    "ADMIN":              RateLimitTier(daily_limit=999999, burst_per_minute=999),
    "DATA_GOVERNANCE":    RateLimitTier(daily_limit=999999, burst_per_minute=999),
}


# ── SQLAlchemy Model ──────────────────────────────────────────────────────────

class RateLimitBucket(Base):
    __tablename__ = "rate_limit_buckets"

    bucket_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    identifier = Column(String(255), unique=True, nullable=False)
    role = Column(String(50), nullable=False, default="PUBLIC")
    daily_count = Column(Integer, default=0, nullable=False)
    minute_count = Column(Integer, default=0, nullable=False)
    day_window = Column(Date, default=date.today, nullable=False)
    minute_window = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)


# ── Rate Limiter ──────────────────────────────────────────────────────────────

class RateLimiter:
    def __init__(self, db: Session) -> None:
        self._db = db

    def _get_or_create_bucket(self, identifier: str, role: str) -> RateLimitBucket:
        bucket = (
            self._db.query(RateLimitBucket)
            .filter(RateLimitBucket.identifier == identifier)
            .with_for_update()
            .first()
        )
        if bucket is None:
            bucket = RateLimitBucket(
                identifier=identifier,
                role=role,
                daily_count=0,
                minute_count=0,
                day_window=date.today(),
                minute_window=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
            self._db.add(bucket)
            self._db.flush()
        return bucket

    def check_and_increment(
        self, identifier: str, role: str
    ) -> tuple[bool, dict]:
        tier = RATE_LIMITS.get(role, RATE_LIMITS["PUBLIC"])
        now = datetime.utcnow()
        today = now.date()

        bucket = self._get_or_create_bucket(identifier, role)

        # Reset daily window
        if bucket.day_window != today:
            bucket.day_window = today
            bucket.daily_count = 0

        # Reset minute window
        if (now - bucket.minute_window) >= timedelta(minutes=1):
            bucket.minute_window = now
            bucket.minute_count = 0

        daily_remaining = max(0, tier.daily_limit - bucket.daily_count)
        minute_remaining = max(0, tier.burst_per_minute - bucket.minute_count)

        # Reset timestamp for next day window
        next_day = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        reset_ts = int(next_day.timestamp())

        headers = {
            "X-RateLimit-Remaining-Daily": str(daily_remaining),
            "X-RateLimit-Remaining-Minute": str(minute_remaining),
            "X-RateLimit-Reset": str(reset_ts),
        }

        if bucket.daily_count >= tier.daily_limit:
            headers["Retry-After"] = str(int((next_day - now).total_seconds()))
            logger.warning(
                "rate_limit_exceeded",
                identifier=identifier,
                role=role,
                window="daily",
                count=bucket.daily_count,
                limit=tier.daily_limit,
            )
            self._db.commit()
            return False, headers

        if bucket.minute_count >= tier.burst_per_minute:
            next_minute = bucket.minute_window + timedelta(minutes=1)
            retry_after = max(1, int((next_minute - now).total_seconds()))
            headers["Retry-After"] = str(retry_after)
            logger.warning(
                "rate_limit_exceeded",
                identifier=identifier,
                role=role,
                window="minute",
                count=bucket.minute_count,
                limit=tier.burst_per_minute,
            )
            self._db.commit()
            return False, headers

        bucket.daily_count += 1
        bucket.minute_count += 1
        bucket.updated_at = now

        # Update remaining after increment
        headers["X-RateLimit-Remaining-Daily"] = str(max(0, tier.daily_limit - bucket.daily_count))
        headers["X-RateLimit-Remaining-Minute"] = str(max(0, tier.burst_per_minute - bucket.minute_count))

        self._db.commit()
        return True, headers


# ── Middleware ────────────────────────────────────────────────────────────────

def _hash_identifier(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()[:64]


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next):
        raw_key = request.headers.get("X-API-Key", "")
        if raw_key:
            identifier = _hash_identifier(raw_key[:8])
        else:
            forwarded_for = request.headers.get("X-Forwarded-For", "")
            client_ip = forwarded_for.split(",")[0].strip() if forwarded_for else (
                request.client.host if request.client else "unknown"
            )
            identifier = _hash_identifier(client_ip)

        auth = getattr(request.state, "auth", None)
        role = auth.role.value if auth is not None else "PUBLIC"

        db: Session = SessionLocal()
        try:
            limiter = RateLimiter(db)
            allowed, rate_headers = limiter.check_and_increment(identifier, role)
        finally:
            db.close()

        if not allowed:
            return Response(
                content='{"detail":"Rate limit exceeded."}',
                status_code=429,
                media_type="application/json",
                headers={"Retry-After": rate_headers.get("Retry-After", "60")},
            )

        response = await call_next(request)
        for header_name, header_value in rate_headers.items():
            response.headers[header_name] = header_value
        return response
