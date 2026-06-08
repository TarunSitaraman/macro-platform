"""Input validation helpers used across agents and API routes."""

import re
from typing import Optional

ISO3_PATTERN = re.compile(r"^[A-Z]{3}$")
PERIOD_PATTERNS = [
    re.compile(r"^\d{4}$"),               # 2024
    re.compile(r"^\d{4}-\d{2}$"),         # 2024-06
    re.compile(r"^\d{4}-Q[1-4]$"),        # 2024-Q2
    re.compile(r"^\d{4}-\d{2}-\d{2}$"),   # 2024-06-01
]
VALID_INDICATOR_CODES = {
    "GDP_CURRENT_USD", "GDP_GROWTH", "CPI_INFLATION",
    "UNEMPLOYMENT_RATE", "CURRENT_ACCOUNT_PCT_GDP", "GOVT_DEBT_PCT_GDP",
}


def is_valid_country_code(code: Optional[str]) -> bool:
    return bool(code and ISO3_PATTERN.match(code))


def is_valid_period(period: Optional[str]) -> bool:
    return any(p.match(period or "") for p in PERIOD_PATTERNS)


def is_valid_indicator(code: Optional[str]) -> bool:
    return code in VALID_INDICATOR_CODES


def validate_raw_record(rec: dict) -> list[str]:
    """Return list of validation errors for a raw extracted record."""
    errors: list[str] = []
    if not is_valid_country_code(rec.get("country_code")):
        errors.append(f"invalid_country_code: {rec.get('country_code')}")
    if not is_valid_period(str(rec.get("period", ""))):
        errors.append(f"invalid_period: {rec.get('period')}")
    if not is_valid_indicator(rec.get("indicator_code")):
        errors.append(f"unknown_indicator: {rec.get('indicator_code')}")
    if rec.get("raw_value") in (None, "", ".", "N/A", "null"):
        errors.append("missing_raw_value")
    return errors
