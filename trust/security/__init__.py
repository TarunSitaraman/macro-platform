"""Pillar 2 — Security: public API for the trust.security package."""

from trust.security.auth import (
    APIKey,
    APIKeyAuth,
    AuthContext,
    UserRole,
    create_api_key,
    require_role,
    router as auth_router,
    verify_hmac_key,
)
from trust.security.bot_detection import (
    BlockedSource,
    BlockedSourceRegistry,
    CrawlerGuard,
    rotating_headers,
    router as crawler_router,
)
from trust.security.rate_limiter import (
    RATE_LIMITS,
    RateLimitBucket,
    RateLimitMiddleware,
    RateLimiter,
    RateLimitTier,
)
from trust.security.secret_manager import (
    SecretManager,
    SafeLogger,
    SecretRotationAlert,
    mask_secret,
    safe_logger,
)

__all__ = [
    # auth
    "APIKey",
    "APIKeyAuth",
    "AuthContext",
    "UserRole",
    "create_api_key",
    "require_role",
    "verify_hmac_key",
    "auth_router",
    # bot detection
    "BlockedSource",
    "BlockedSourceRegistry",
    "CrawlerGuard",
    "rotating_headers",
    "crawler_router",
    # rate limiting
    "RATE_LIMITS",
    "RateLimitBucket",
    "RateLimitMiddleware",
    "RateLimiter",
    "RateLimitTier",
    # secret manager
    "SecretManager",
    "SafeLogger",
    "SecretRotationAlert",
    "mask_secret",
    "safe_logger",
]
