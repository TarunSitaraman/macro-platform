"""Pillar 9 — Fairness: Country coverage tracking and disclosure for equitable data access.
Satisfies INTERNAL fairness policy.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session
from starlette.middleware.base import BaseHTTPMiddleware

from src.config import PHASE1_COUNTRIES
from src.database import GoldRecord, get_db

logger = structlog.get_logger().bind(pillar="fairness")

COUNTRY_NAMES: dict[str, str] = {
    "USA": "United States",
    "GBR": "United Kingdom",
    "DEU": "Germany",
    "FRA": "France",
    "JPN": "Japan",
    "CHN": "China",
    "IND": "India",
    "BRA": "Brazil",
    "CAN": "Canada",
    "AUS": "Australia",
    "KOR": "South Korea",
    "MEX": "Mexico",
    "ITA": "Italy",
    "ESP": "Spain",
    "NLD": "Netherlands",
    "SAU": "Saudi Arabia",
    "ZAF": "South Africa",
    "ARG": "Argentina",
    "IDN": "Indonesia",
    "TUR": "Turkey",
}

TOTAL_INDICATORS = 11  # INDICATOR_CATALOGUE count from config.py


@dataclass
class CoverageEntry:
    country_code:       str
    country_name:       str
    indicators_covered: int
    total_indicators:   int
    coverage_score:     float
    last_updated:       Optional[datetime]


class CoverageMapper:
    def __init__(self, db: Session) -> None:
        self._db = db

    def get_coverage_entry(self, country_code: str) -> CoverageEntry:
        row = (
            self._db.query(
                func.count(GoldRecord.indicator_code.distinct()).label("indicator_count"),
                func.max(GoldRecord.crawled_at).label("last_updated"),
            )
            .filter(GoldRecord.country_code == country_code)
            .first()
        )
        indicators_covered = int(row.indicator_count) if row and row.indicator_count else 0
        last_updated       = row.last_updated if row else None
        coverage_score     = (indicators_covered / TOTAL_INDICATORS) * 100

        return CoverageEntry(
            country_code=country_code,
            country_name=COUNTRY_NAMES.get(country_code, country_code),
            indicators_covered=indicators_covered,
            total_indicators=TOTAL_INDICATORS,
            coverage_score=coverage_score,
            last_updated=last_updated,
        )

    def get_all_coverage(self) -> list[CoverageEntry]:
        # Countries that have Gold data
        rows = (
            self._db.query(
                GoldRecord.country_code,
                func.count(GoldRecord.indicator_code.distinct()).label("indicator_count"),
                func.max(GoldRecord.crawled_at).label("last_updated"),
            )
            .group_by(GoldRecord.country_code)
            .all()
        )
        covered: dict[str, CoverageEntry] = {}
        for row in rows:
            cc = row.country_code
            indicators_covered = int(row.indicator_count)
            coverage_score     = (indicators_covered / TOTAL_INDICATORS) * 100
            covered[cc] = CoverageEntry(
                country_code=cc,
                country_name=COUNTRY_NAMES.get(cc, cc),
                indicators_covered=indicators_covered,
                total_indicators=TOTAL_INDICATORS,
                coverage_score=coverage_score,
                last_updated=row.last_updated,
            )

        # Fill in phase-1 countries with zero coverage
        for cc in PHASE1_COUNTRIES:
            if cc not in covered:
                covered[cc] = CoverageEntry(
                    country_code=cc,
                    country_name=COUNTRY_NAMES.get(cc, cc),
                    indicators_covered=0,
                    total_indicators=TOTAL_INDICATORS,
                    coverage_score=0.0,
                    last_updated=None,
                )

        return sorted(covered.values(), key=lambda e: e.coverage_score, reverse=True)

    def format_coverage_notice(self, country_code: str, coverage_score: float) -> str:
        name = COUNTRY_NAMES.get(country_code, country_code)
        return (
            f"Note: Data coverage for {name} is currently {coverage_score:.0f}%. "
            "Some indicators may be unavailable or sourced from limited providers."
        )


class CoverageDisclosureMiddleware(BaseHTTPMiddleware):
    """
    Starlette middleware stub for coverage disclosure.

    Actual coverage injection is done at the chatbot route level because
    determining which country is mentioned requires parsing the response body,
    which is expensive to do in middleware. This middleware sets the
    `coverage_check_enabled` flag and returns the response unmodified.
    """

    coverage_check_enabled: bool = True

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        return response


# ── FastAPI router ─────────────────────────────────────────────────────────────

router = APIRouter(prefix="/api/coverage", tags=["coverage"])


def _entry_to_dict(e: CoverageEntry) -> dict:
    return {
        "country_code":       e.country_code,
        "country_name":       e.country_name,
        "indicators_covered": e.indicators_covered,
        "total_indicators":   e.total_indicators,
        "coverage_score":     e.coverage_score,
        "last_updated":       e.last_updated.isoformat() if e.last_updated else None,
    }


@router.get("/")
def get_all_coverage(db: Session = Depends(get_db)):
    mapper = CoverageMapper(db)
    return [_entry_to_dict(e) for e in mapper.get_all_coverage()]


@router.get("/{country_code}")
def get_country_coverage(country_code: str, db: Session = Depends(get_db)):
    mapper = CoverageMapper(db)
    entry  = mapper.get_coverage_entry(country_code.upper())
    return _entry_to_dict(entry)
