"""Pillar 2 — Security: Crawler bot detection and source blocking. Satisfies INTERNAL crawling governance policy."""

import random
import uuid
from datetime import datetime, timedelta
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Session

from src.database import Base, get_db
from trust.security.auth import AuthContext, UserRole, _auth_dependency, require_role

logger = structlog.get_logger().bind(pillar="security")

COOLDOWN_HOURS = [1, 4, 24]

REALISTIC_USER_AGENTS = [
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.4.1 Safari/605.1.15"
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) "
        "Gecko/20100101 Firefox/124.0"
    ),
    (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.4; rv:124.0) "
        "Gecko/20100101 Firefox/124.0"
    ),
]

REALISTIC_ACCEPT_LANGUAGES = [
    "en-US,en;q=0.9",
    "en-GB,en;q=0.9,fr;q=0.8",
    "de-DE,de;q=0.9,en;q=0.8",
    "fr-FR,fr;q=0.9,en;q=0.8",
    "en-US,en;q=0.9,es;q=0.8",
]


# ── SQLAlchemy Model ──────────────────────────────────────────────────────────

class BlockedSource(Base):
    __tablename__ = "blocked_sources"

    block_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_url = Column(Text, nullable=False)
    source_code = Column(String(50), nullable=True)
    blocked_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    block_count = Column(Integer, default=1, nullable=False)
    cooldown_until = Column(DateTime, nullable=True)
    reason = Column(String(500), nullable=False)
    is_permanent = Column(Boolean, default=False, nullable=False)

    compliance_context = "INTERNAL - Crawler Governance"


# ── Blocked Source Registry ───────────────────────────────────────────────────

class BlockedSourceRegistry:
    def __init__(self, db: Session) -> None:
        self._db = db

    def is_blocked(self, source_url: str) -> bool:
        """Return True if the source is permanently blocked or still in its cooldown window."""
        record = (
            self._db.query(BlockedSource)
            .filter(BlockedSource.source_url == source_url)
            .first()
        )
        if record is None:
            return False
        if record.is_permanent:
            return True
        if record.cooldown_until and record.cooldown_until > datetime.utcnow():
            return True
        return False

    def record_block(
        self,
        source_url: str,
        reason: str,
        source_code: Optional[str] = None,
    ) -> None:
        """
        Record or update a block for source_url.
        Escalates cooldown on repeated blocks; marks permanent after 3 blocks.
        """
        now = datetime.utcnow()
        record = (
            self._db.query(BlockedSource)
            .filter(BlockedSource.source_url == source_url)
            .first()
        )

        if record is None:
            cooldown_hours = COOLDOWN_HOURS[0]
            record = BlockedSource(
                source_url=source_url,
                source_code=source_code,
                blocked_at=now,
                block_count=1,
                cooldown_until=now + timedelta(hours=cooldown_hours),
                reason=reason,
                is_permanent=False,
            )
            self._db.add(record)
        else:
            record.block_count += 1
            record.reason = reason
            record.blocked_at = now
            if source_code:
                record.source_code = source_code

            if record.block_count >= 3:
                record.is_permanent = True
                record.cooldown_until = None
            else:
                hours_idx = min(record.block_count - 1, len(COOLDOWN_HOURS) - 1)
                cooldown_hours = COOLDOWN_HOURS[hours_idx]
                record.cooldown_until = now + timedelta(hours=cooldown_hours)

        self._db.commit()

        logger.warning(
            "source_blocked",
            source_url=source_url,
            source_code=source_code,
            block_count=record.block_count,
            is_permanent=record.is_permanent,
            cooldown_until=record.cooldown_until.isoformat() if record.cooldown_until else "permanent",
            reason=reason,
            pillar="security",
        )

    def get_blocked_sources(self) -> list[BlockedSource]:
        return self._db.query(BlockedSource).all()


# ── Rotating Headers ──────────────────────────────────────────────────────────

def rotating_headers() -> dict:
    """Return realistic browser-like HTTP headers with randomised User-Agent and Accept-Language."""
    return {
        "User-Agent": random.choice(REALISTIC_USER_AGENTS),
        "Accept-Language": random.choice(REALISTIC_ACCEPT_LANGUAGES),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
    }


# ── Crawler Guard ─────────────────────────────────────────────────────────────

class CrawlerGuard:
    """Inspect HTTP responses from external sources and manage blocking state."""

    _BLOCK_STATUS_CODES = {403, 429, 503}

    def __init__(self, db: Session) -> None:
        self._db = db

    def check_response(
        self,
        url: str,
        status_code: int,
        source_code: Optional[str] = None,
    ) -> bool:
        """
        Evaluate a crawl response. If the status indicates blocking, record it.
        Returns True if it is safe to proceed, False if the source has blocked us.
        """
        if status_code in self._BLOCK_STATUS_CODES:
            registry = BlockedSourceRegistry(self._db)
            registry.record_block(
                source_url=url,
                reason=f"HTTP {status_code}",
                source_code=source_code,
            )
            return False
        return True

    def get_headers(self) -> dict:
        return rotating_headers()


# ── Response Schemas ──────────────────────────────────────────────────────────

class BlockedSourceResponse(BaseModel):
    block_id: str
    source_url: str
    source_code: Optional[str]
    blocked_at: datetime
    block_count: int
    cooldown_until: Optional[datetime]
    reason: str
    is_permanent: bool

    class Config:
        from_attributes = True


class UnblockResponse(BaseModel):
    block_id: str
    unblocked: bool


# ── Router ────────────────────────────────────────────────────────────────────

router = APIRouter(prefix="/api/security/crawler", tags=["crawler-security"])


@router.get("/blocked", response_model=list[BlockedSourceResponse])
async def list_blocked_sources(
    auth: AuthContext = Depends(_auth_dependency),
    _: AuthContext = Depends(require_role(UserRole.DATA_GOVERNANCE, UserRole.ADMIN)),
    db: Session = Depends(get_db),
) -> list[BlockedSourceResponse]:
    """List all blocked crawler sources. DATA_GOVERNANCE or ADMIN role required."""
    registry = BlockedSourceRegistry(db)
    records = registry.get_blocked_sources()
    return [
        BlockedSourceResponse(
            block_id=str(r.block_id),
            source_url=r.source_url,
            source_code=r.source_code,
            blocked_at=r.blocked_at,
            block_count=r.block_count,
            cooldown_until=r.cooldown_until,
            reason=r.reason,
            is_permanent=r.is_permanent,
        )
        for r in records
    ]


@router.delete("/blocked/{block_id}", response_model=UnblockResponse)
async def unblock_source(
    block_id: str,
    auth: AuthContext = Depends(_auth_dependency),
    _: AuthContext = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
) -> UnblockResponse:
    """Remove a block for a crawler source. ADMIN role required."""
    try:
        uid = uuid.UUID(block_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid block_id format.")

    record = db.query(BlockedSource).filter(BlockedSource.block_id == uid).first()
    if record is None:
        raise HTTPException(status_code=404, detail="Blocked source not found.")

    db.delete(record)
    db.commit()

    logger.info(
        "source_unblocked",
        block_id=block_id,
        source_url=record.source_url,
        actor=auth.user_id,
    )
    return UnblockResponse(block_id=block_id, unblocked=True)
