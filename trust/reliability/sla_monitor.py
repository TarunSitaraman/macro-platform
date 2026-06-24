"""Pillar 1 — Reliability: SLA freshness monitoring for data tiers.

Satisfies MiFID II data quality obligations.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional

import structlog
from fastapi import APIRouter, Depends
from sqlalchemy import Column, DateTime, Float, Integer, String, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Session

from src.database import Base, get_db

logger = structlog.get_logger().bind(pillar="reliability")

router = APIRouter(prefix="/api/sla", tags=["sla"])


# ── Tiers and windows ─────────────────────────────────────────────────────────

class SLATier(str, Enum):
    TIER1 = "TIER1"
    TIER2 = "TIER2"
    TIER3 = "TIER3"


# Maximum permitted data age per tier, in minutes
SLA_WINDOWS: dict[SLATier, int] = {
    SLATier.TIER1: 30,
    SLATier.TIER2: 240,
    SLATier.TIER3: 480,
}

# Canonical indicator codes per tier (prefix-based matching)
TIER_INDICATORS: dict[SLATier, List[str]] = {
    SLATier.TIER1: [
        "GDP_GROWTH",
        "CPI_INFLATION",
        "UNEMPLOYMENT_RATE",
    ],
    SLATier.TIER2: [
        "CURRENT_ACCOUNT_PCT_GDP",
        "GOVT_DEBT_PCT_GDP",
        "EXPORTS_PCT_GDP",
        "IMPORTS_PCT_GDP",
    ],
    SLATier.TIER3: [
        "POPULATION",
        "GOVT_REVENUE_PCT_GDP",
        "GOVT_EXPENDITURE_PCT_GDP",
    ],
}

# Flat lookup: indicator_code → SLATier
_CODE_TO_TIER: dict[str, SLATier] = {
    code: tier
    for tier, codes in TIER_INDICATORS.items()
    for code in codes
}


# ── SQLAlchemy model ──────────────────────────────────────────────────────────

class SLAViolation(Base):
    __tablename__ = "sla_violations"

    violation_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    indicator_code = Column(String(100), nullable=False, index=True)
    country_code = Column(String(3), nullable=True)
    tier = Column(String(10), nullable=False)
    expected_window_minutes = Column(Integer, nullable=False)
    actual_age_minutes = Column(Float, nullable=False)
    violated_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_crawled_at = Column(DateTime, nullable=True)
    compliance_context = Column(
        String(200),
        default="MiFID II Article 25 - Data Quality",
        nullable=False,
    )


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class SLAStatus:
    indicator_code: str
    tier: SLATier
    compliant: bool
    age_minutes: float
    window_minutes: int
    last_crawled_at: Optional[datetime]


# ── Monitor class ─────────────────────────────────────────────────────────────

class SLAMonitor:
    def __init__(self, db: Session) -> None:
        self._db = db

    def get_tier(self, indicator_code: str) -> SLATier:
        """Return the SLATier for a given indicator_code.

        Checks exact match against TIER_INDICATORS lists; if not found,
        checks whether the code starts with any listed prefix.
        Falls back to TIER3.
        """
        if indicator_code in _CODE_TO_TIER:
            return _CODE_TO_TIER[indicator_code]
        # Prefix-based fallback
        for tier, codes in TIER_INDICATORS.items():
            for prefix in codes:
                if indicator_code.startswith(prefix):
                    return tier
        return SLATier.TIER3

    def check_freshness(self, indicator_code: str) -> SLAStatus:
        """Check whether the given indicator's gold data meets its SLA window.

        Queries gold_records for the most-recent crawled_at timestamp, computes
        age, and writes a SLAViolation row if the SLA is breached.
        """
        tier = self.get_tier(indicator_code)
        window_minutes = SLA_WINDOWS[tier]

        # Query the latest crawled_at for this indicator across all countries
        row = (
            self._db.execute(
                text(
                    "SELECT MAX(crawled_at) AS last_crawled, "
                    "       (SELECT country_code FROM gold_records "
                    "        WHERE indicator_code = :code "
                    "        ORDER BY crawled_at DESC LIMIT 1) AS country "
                    "FROM gold_records WHERE indicator_code = :code"
                ),
                {"code": indicator_code},
            ).fetchone()
        )

        last_crawled_at: Optional[datetime] = row[0] if row else None
        country_code: Optional[str] = row[1] if row else None

        now = datetime.now(timezone.utc)

        if last_crawled_at is None:
            # No data at all — treat as infinitely stale
            age_minutes = float("inf")
            compliant = False
        else:
            # Normalise to UTC-aware if the stored value is naive
            if last_crawled_at.tzinfo is None:
                last_crawled_at = last_crawled_at.replace(tzinfo=timezone.utc)
            age_minutes = (now - last_crawled_at).total_seconds() / 60.0
            compliant = age_minutes <= window_minutes

        if not compliant:
            violation = SLAViolation(
                indicator_code=indicator_code,
                country_code=country_code,
                tier=tier.value,
                expected_window_minutes=window_minutes,
                actual_age_minutes=age_minutes if age_minutes != float("inf") else -1.0,
                violated_at=now.replace(tzinfo=None),
                last_crawled_at=last_crawled_at.replace(tzinfo=None) if last_crawled_at else None,
            )
            self._db.add(violation)
            self._db.commit()

            logger.warning(
                "sla_violation",
                indicator_code=indicator_code,
                tier=tier.value,
                age_minutes=age_minutes,
                window_minutes=window_minutes,
            )

        return SLAStatus(
            indicator_code=indicator_code,
            tier=tier,
            compliant=compliant,
            age_minutes=age_minutes,
            window_minutes=window_minutes,
            last_crawled_at=last_crawled_at,
        )

    def check_all(self) -> List[SLAStatus]:
        """Check every indicator listed in TIER_INDICATORS."""
        results: List[SLAStatus] = []
        for codes in TIER_INDICATORS.values():
            for code in codes:
                try:
                    results.append(self.check_freshness(code))
                except Exception as exc:
                    logger.error(
                        "sla_check_error",
                        indicator_code=code,
                        error=str(exc),
                    )
        return results


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/status")
def sla_status(db: Session = Depends(get_db)):
    """Return per-indicator SLA status and per-tier compliance percentage."""
    monitor = SLAMonitor(db)
    statuses = monitor.check_all()

    # Build per-tier aggregation
    tier_totals: dict[str, int] = {t.value: 0 for t in SLATier}
    tier_compliant: dict[str, int] = {t.value: 0 for t in SLATier}

    items = []
    for s in statuses:
        tier_totals[s.tier.value] += 1
        if s.compliant:
            tier_compliant[s.tier.value] += 1
        items.append({
            "indicator_code": s.indicator_code,
            "tier": s.tier.value,
            "compliant": s.compliant,
            "age_minutes": round(s.age_minutes, 1) if s.age_minutes != float("inf") else None,
            "window_minutes": s.window_minutes,
            "last_crawled_at": s.last_crawled_at.isoformat() if s.last_crawled_at else None,
        })

    tier_compliance_pct = {
        tier: (
            round(tier_compliant[tier] / tier_totals[tier] * 100, 1)
            if tier_totals[tier] > 0
            else None
        )
        for tier in tier_totals
    }

    total = len(statuses)
    total_compliant = sum(1 for s in statuses if s.compliant)
    overall_pct = round(total_compliant / total * 100, 1) if total > 0 else None

    return {
        "overall_compliance_pct": overall_pct,
        "tier_compliance_pct": tier_compliance_pct,
        "sla_windows_minutes": {t.value: SLA_WINDOWS[t] for t in SLATier},
        "indicators": items,
        "compliance_context": "MiFID II Article 25 - Data Quality",
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
