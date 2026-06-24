"""Pillar 4 — Privacy: PII detection and redaction in user queries. Satisfies GDPR Article 25 - Privacy by Design."""

import hashlib
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import structlog
from sqlalchemy import Column, DateTime, String
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Session

from src.database import Base

logger = structlog.get_logger().bind(pillar="privacy")

# ── PII patterns ───────────────────────────────────────────────────────────────

PII_PATTERNS: dict[str, str] = {
    "EMAIL": r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",
    "PHONE": r"\b(\+\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b",
    "NATIONAL_ID": r"\b\d{3}[-]?\d{2}[-]?\d{4}\b",
    "CREDIT_CARD": r"\b(?:\d{4}[-\s]?){3}\d{4}\b",
    "IP_ADDRESS": r"\b(?:\d{1,3}\.){3}\d{1,3}\b",
}


# ── Dataclass — NEVER stored in DB ────────────────────────────────────────────

@dataclass
class PIIMatch:
    pii_type: str
    start: int
    end: int
    matched_text: str  # NEVER stored in DB


# ── SQLAlchemy model ───────────────────────────────────────────────────────────

class PIIDetectionEvent(Base):
    __tablename__ = "privacy_audit_log"

    event_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # sha256 of the ORIGINAL query — raw text is never stored
    query_hash = Column(String(64), nullable=False)
    pii_types_found = Column(ARRAY(String), nullable=False)
    redacted_at = Column(DateTime, default=datetime.utcnow)
    compliance_context = Column(String, default="GDPR Article 17 - Right to Erasure")


# ── Scanner ────────────────────────────────────────────────────────────────────

class PIIScanner:
    def scan(self, text: str) -> list[PIIMatch]:
        matches: list[PIIMatch] = []
        for pii_type, pattern in PII_PATTERNS.items():
            for m in re.finditer(pattern, text):
                matches.append(
                    PIIMatch(
                        pii_type=pii_type,
                        start=m.start(),
                        end=m.end(),
                        matched_text=m.group(),
                    )
                )
        matches.sort(key=lambda m: m.start)
        return matches

    def redact_pii(self, text: str) -> tuple[str, list[PIIMatch]]:
        matches = self.scan(text)
        # Replace in reverse order to preserve character positions
        redacted = text
        for match in reversed(matches):
            placeholder = f"[REDACTED_{match.pii_type}]"
            redacted = redacted[: match.start] + placeholder + redacted[match.end :]
        return redacted, matches


# ── Sanitizer ─────────────────────────────────────────────────────────────────

class QuerySanitizer:
    def __init__(self, db: Session) -> None:
        self._db = db

    def sanitize(self, query: str) -> str:
        scanner = PIIScanner()
        redacted, matches = scanner.redact_pii(query)

        if matches:
            query_hash = hashlib.sha256(query.encode()).hexdigest()
            pii_types = [m.pii_type for m in matches]

            event = PIIDetectionEvent(
                query_hash=query_hash,
                pii_types_found=pii_types,
            )
            self._db.add(event)
            self._db.commit()

            logger.warning(
                "pii_detected_and_redacted",
                pii_types=pii_types,
                # Never log matched_text or raw query
            )

        return redacted
