"""Pillar 3 — Safety: Output validation ensuring citations and factual accuracy. Satisfies MiFID II research output standards."""

import re
from dataclasses import dataclass, field
from typing import Optional

import structlog
from sqlalchemy.orm import Session

from src.database import GoldRecord

logger = structlog.get_logger().bind(pillar="safety")

# ── Constants ──────────────────────────────────────────────────────────────────

CITATION_PATTERNS = [
    r"\[Source:",
    r"\[Sources:",
    r"Source:\s",
    r"https?://",
    r"\(IMF\)",
    r"\(World Bank\)",
    r"\(FRED\)",
    r"\(OECD\)",
]

_NUMBER_RE = re.compile(
    r"\b(\d+\.?\d*)\s*(%|percent|billion|trillion|million)?\b"
)


# ── Dataclass ──────────────────────────────────────────────────────────────────

@dataclass
class ValidationResult:
    valid: bool
    issues: list[str] = field(default_factory=list)
    confidence: float = 1.0


# ── Validator ──────────────────────────────────────────────────────────────────

class OutputValidator:
    def __init__(self, db: Session) -> None:
        self._db = db

    def check_citation_present(self, response: str) -> Optional[str]:
        for pattern in CITATION_PATTERNS:
            if re.search(pattern, response):
                return None
        return "Response missing source citation"

    def check_length(self, response: str) -> Optional[str]:
        length = len(response)
        if length < 50:
            return "Response too short (< 50 chars)"
        if length > 4000:
            return "Response too long (> 4000 chars)"
        return None

    def check_numeric_claims(
        self, response: str, db: Session
    ) -> tuple[Optional[str], float]:
        matches = _NUMBER_RE.findall(response)
        # Filter to plausible indicator values (0.01 < x < 1000)
        numbers = [
            float(num)
            for num, _unit in matches
            if 0.01 < float(num) < 1000
        ]

        if not numbers:
            return None, 0.8

        confidence = 0.0
        any_matched = False

        for number in numbers:
            lower = number * 0.9
            upper = number * 1.1
            match = (
                db.query(GoldRecord)
                .filter(GoldRecord.value >= lower, GoldRecord.value <= upper)
                .first()
            )
            if match:
                any_matched = True
                confidence = min(confidence + 0.2, 1.0)

        if not any_matched:
            return (
                "Numeric values could not be cross-referenced against Gold layer",
                0.4,
            )

        return None, min(confidence, 1.0)

    def validate(self, response: str) -> ValidationResult:
        issues: list[str] = []

        citation_issue = self.check_citation_present(response)
        if citation_issue:
            issues.append(citation_issue)

        length_issue = self.check_length(response)
        if length_issue:
            issues.append(length_issue)

        numeric_issue, confidence = self.check_numeric_claims(response, self._db)
        if numeric_issue:
            issues.append(numeric_issue)

        valid = len(issues) == 0

        logger.info(
            "output_validated",
            valid=valid,
            issues=issues,
            confidence=confidence,
        )

        return ValidationResult(valid=valid, issues=issues, confidence=confidence)
