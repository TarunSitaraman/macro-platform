"""Pillar 2 — Security: Authentication and RBAC for FastAPI. Satisfies MiFID II access control requirements."""

import os
import secrets
import string
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from functools import wraps
from typing import Callable, Optional

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import APIKeyHeader
from jose import JWTError, jwk, jwt
from jose.constants import ALGORITHMS
from passlib.context import CryptContext
from pydantic import BaseModel
from sqlalchemy import Boolean, Column, DateTime, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Session

from src.database import Base, get_db

logger = structlog.get_logger().bind(pillar="security")

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


# ── Role Enum ─────────────────────────────────────────────────────────────────

class UserRole(str, Enum):
    INTERNAL_ANALYST = "INTERNAL_ANALYST"
    EXTERNAL_BUSINESS = "EXTERNAL_BUSINESS"
    PUBLIC = "PUBLIC"
    ADMIN = "ADMIN"
    DATA_GOVERNANCE = "DATA_GOVERNANCE"


# ── SQLAlchemy Model ──────────────────────────────────────────────────────────

class APIKey(Base):
    __tablename__ = "api_keys"

    key_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(100), nullable=False)
    prefix = Column(String(10), nullable=False)
    hashed_key = Column(String(255), unique=True, nullable=False)
    role = Column(String(50), default="PUBLIC", nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)

    compliance_context = "MiFID II - Access Control"


# ── Auth Context ──────────────────────────────────────────────────────────────

@dataclass
class AuthContext:
    user_id: str
    role: UserRole
    is_internal: bool
    request_id: str


# ── Key Helpers ───────────────────────────────────────────────────────────────

def verify_hmac_key(raw_key: str, db: Session) -> Optional[APIKey]:
    """Verify a raw API key against the stored bcrypt hash. Returns APIKey or None."""
    if not raw_key or len(raw_key) < 8:
        return None
    prefix = raw_key[:8]
    record = db.query(APIKey).filter(
        APIKey.prefix == prefix,
        APIKey.is_active.is_(True),
    ).first()
    if record is None:
        return None
    if not _pwd_context.verify(raw_key, record.hashed_key):
        return None
    return record


def create_api_key(name: str, role: UserRole, db: Session) -> tuple[str, APIKey]:
    """
    Generate a new API key, persist its bcrypt hash, and return (raw_key, APIKey).
    The raw_key is shown once and never stored.
    """
    alphabet = string.ascii_letters + string.digits
    prefix = "".join(secrets.choice(alphabet) for _ in range(8))
    token = secrets.token_urlsafe(32)
    raw_key = f"{prefix}.{token}"
    hashed = _pwd_context.hash(raw_key)

    record = APIKey(
        name=name,
        prefix=prefix,
        hashed_key=hashed,
        role=role.value,
    )
    db.add(record)
    db.commit()
    db.refresh(record)

    logger.info(
        "api_key_created",
        key_id=str(record.key_id),
        name=name,
        role=role.value,
    )
    return raw_key, record


# ── JWKS cache (module-level, TTL 1 hour) ────────────────────────────────────

_jwks_cache: dict = {}       # {tenant_id: {"keys": [...], "fetched_at": float}}
_JWKS_TTL = 3600             # seconds


def _get_jwks(tenant_id: str) -> list[dict]:
    """Return cached JWKS for the tenant, refreshing if stale."""
    cached = _jwks_cache.get(tenant_id)
    if cached and time.monotonic() - cached["fetched_at"] < _JWKS_TTL:
        return cached["keys"]
    jwks_uri = f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys"
    resp = httpx.get(jwks_uri, timeout=5.0)
    resp.raise_for_status()
    keys = resp.json().get("keys", [])
    _jwks_cache[tenant_id] = {"keys": keys, "fetched_at": time.monotonic()}
    return keys


# ── JWT Validation (Azure AD / RS256) ────────────────────────────────────────

def _validate_azure_jwt(token: str) -> Optional[dict]:
    """
    Validate an RS256 JWT issued by Azure AD.

    Fetches the tenant's JWKS, selects the key matching the token's 'kid'
    header, and calls jwt.decode with full signature verification enabled.
    Returns the claims dict on success, or None if validation fails for
    any reason (missing config, unknown kid, bad signature, expired, etc.).
    """
    tenant_id = os.getenv("AZURE_AD_TENANT_ID")
    client_id = os.getenv("AZURE_AD_CLIENT_ID")
    if not tenant_id or not client_id:
        return None

    try:
        unverified_header = jwt.get_unverified_header(token)
        kid = unverified_header.get("kid")
        if not kid:
            logger.warning("jwt_missing_kid")
            return None

        keys = _get_jwks(tenant_id)
        matching = [k for k in keys if k.get("kid") == kid]
        if not matching:
            logger.warning("jwt_unknown_kid", kid=kid)
            return None

        public_key = jwk.construct(matching[0], algorithm=ALGORITHMS.RS256)
        issuer = f"https://login.microsoftonline.com/{tenant_id}/v2.0"

        claims = jwt.decode(
            token,
            key=public_key,
            algorithms=[ALGORITHMS.RS256],
            audience=client_id,
            issuer=issuer,
            options={"verify_signature": True},   # always verify
        )
        return claims

    except httpx.HTTPError as exc:
        logger.warning("jwks_fetch_failed", error=str(exc))
        return None
    except JWTError as exc:
        logger.warning("jwt_validation_failed", error=str(exc))
        return None


# ── APIKeyAuth Dependency ─────────────────────────────────────────────────────

class APIKeyAuth:
    """FastAPI dependency that resolves an AuthContext from request headers."""

    async def __call__(
        self,
        request: Request,
        db: Session = Depends(get_db),
    ) -> AuthContext:
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))

        raw_key = request.headers.get("X-API-Key")
        if raw_key:
            record = verify_hmac_key(raw_key, db)
            if record is None:
                logger.warning("invalid_api_key", request_id=request_id)
                raise HTTPException(status_code=401, detail="Invalid or revoked API key.")
            role = UserRole(record.role)
            ctx = AuthContext(
                user_id=str(record.key_id),
                role=role,
                is_internal=role in (UserRole.INTERNAL_ANALYST, UserRole.ADMIN, UserRole.DATA_GOVERNANCE),
                request_id=request_id,
            )
            request.state.auth = ctx
            return ctx

        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            jwt_token = auth_header[len("Bearer "):]
            claims = _validate_azure_jwt(jwt_token)
            if claims is not None:
                raw_role = claims.get("roles", ["PUBLIC"])[0] if claims.get("roles") else "PUBLIC"
                try:
                    role = UserRole(raw_role)
                except ValueError:
                    role = UserRole.PUBLIC
                ctx = AuthContext(
                    user_id=claims.get("oid", "unknown"),
                    role=role,
                    is_internal=role in (UserRole.INTERNAL_ANALYST, UserRole.ADMIN, UserRole.DATA_GOVERNANCE),
                    request_id=request_id,
                )
                request.state.auth = ctx
                return ctx

        ctx = AuthContext(
            user_id="anonymous",
            role=UserRole.PUBLIC,
            is_internal=False,
            request_id=request_id,
        )
        request.state.auth = ctx
        return ctx


_auth_dependency = APIKeyAuth()


# ── RBAC Decorator ────────────────────────────────────────────────────────────

def require_role(*roles: UserRole) -> Callable:
    """
    FastAPI Depends factory that enforces role membership.
    Usage:  Depends(require_role(UserRole.ADMIN, UserRole.DATA_GOVERNANCE))
    """
    async def _check(request: Request) -> AuthContext:
        ctx: AuthContext = getattr(request.state, "auth", None)
        if ctx is None:
            raise HTTPException(status_code=401, detail="Not authenticated.")
        if ctx.role not in roles:
            logger.warning(
                "access_denied",
                user_id=ctx.user_id,
                user_role=ctx.role.value,
                required_roles=[r.value for r in roles],
                path=request.url.path,
                pillar="security",
            )
            raise HTTPException(
                status_code=403,
                detail=f"Role '{ctx.role.value}' is not permitted for this endpoint.",
            )
        return ctx

    return _check


# ── Request / Response Schemas ────────────────────────────────────────────────

class CreateKeyRequest(BaseModel):
    name: str
    role: UserRole


class CreateKeyResponse(BaseModel):
    key_id: str
    name: str
    role: str
    raw_key: str
    warning: str = "Store this key securely — it will never be shown again."


class DeactivateKeyResponse(BaseModel):
    key_id: str
    deactivated: bool


# ── Router ────────────────────────────────────────────────────────────────────

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/keys", response_model=CreateKeyResponse)
async def create_key(
    body: CreateKeyRequest,
    request: Request,
    auth: AuthContext = Depends(_auth_dependency),
    _: AuthContext = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
) -> CreateKeyResponse:
    """Create a new API key. ADMIN role required. Raw key shown once."""
    raw_key, record = create_api_key(body.name, body.role, db)
    return CreateKeyResponse(
        key_id=str(record.key_id),
        name=record.name,
        role=record.role,
        raw_key=raw_key,
    )


@router.delete("/keys/{key_id}", response_model=DeactivateKeyResponse)
async def deactivate_key(
    key_id: str,
    request: Request,
    auth: AuthContext = Depends(_auth_dependency),
    _: AuthContext = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
) -> DeactivateKeyResponse:
    """Deactivate an API key by ID. ADMIN role required."""
    try:
        uid = uuid.UUID(key_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid key_id format.")

    record = db.query(APIKey).filter(APIKey.key_id == uid).first()
    if record is None:
        raise HTTPException(status_code=404, detail="API key not found.")

    record.is_active = False
    db.commit()

    logger.info(
        "api_key_deactivated",
        key_id=key_id,
        actor=auth.user_id,
    )
    return DeactivateKeyResponse(key_id=key_id, deactivated=True)
