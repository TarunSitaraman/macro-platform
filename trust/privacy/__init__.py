"""Pillar 4 — Privacy: PII scanning, data retention, and consent management."""

from trust.privacy.consent_manager import ConsentManager, ConsentRecord, ConsentType
from trust.privacy.data_retention import RetentionEnforcer, RetentionPolicy, run_retention_enforcement
from trust.privacy.pii_scanner import PIIDetectionEvent, PIIScanner, QuerySanitizer

__all__ = [
    "ConsentManager",
    "ConsentRecord",
    "ConsentType",
    "PIIDetectionEvent",
    "PIIScanner",
    "QuerySanitizer",
    "RetentionEnforcer",
    "RetentionPolicy",
    "run_retention_enforcement",
]
