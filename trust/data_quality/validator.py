"""Pillar 7 — Data Quality: Four-layer validation pipeline for all macroeconomic indicators.
Satisfies MiFID II Article 25 data quality requirements.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from src.database import GoldRecord


RANGE_CONSTRAINTS: dict[str, dict] = {
    "GDP_GROWTH":              {"min": -50.0,  "max": 50.0,   "max_qoq_change_pct": 50.0},
    "CPI_INFLATION":           {"min": -20.0,  "max": 200.0},
    "UNEMPLOYMENT_RATE":       {"min": 0.0,    "max": 100.0},
    "CURRENT_ACCOUNT_PCT_GDP": {"min": -100.0, "max": 100.0},
    "GOVT_DEBT_PCT_GDP":       {"min": 0.0,    "max": 500.0},
    "EXPORTS_PCT_GDP":         {"min": 0.0,    "max": 200.0},
    "IMPORTS_PCT_GDP":         {"min": 0.0,    "max": 200.0},
    "POPULATION":              {"min": 0.01,   "max": 20000.0},
    "GDP_CURRENT_USD":         {"min": 0.0,    "max": 50000.0},
}

EXPECTED_FREQUENCIES: dict[str, int] = {
    "GDP_CURRENT_USD":    365,
    "GDP_GROWTH":         365,
    "CPI_INFLATION":      30,
    "UNEMPLOYMENT_RATE":  30,
    "POPULATION":         365,
}
_DEFAULT_FREQUENCY_DAYS = 90


@dataclass
class ValidationFailure:
    layer: str
    field: str
    message: str
    severity: str  # "ERROR" | "WARNING"


@dataclass
class ValidationReport:
    score: float
    passed: bool
    failures: list[ValidationFailure]
    indicator_code: str
    country_code: str
    period: str


class DataQualityValidator:
    def __init__(self, db: Session) -> None:
        self._db = db

    def validate_schema(self, data: dict) -> list[ValidationFailure]:
        failures: list[ValidationFailure] = []
        required_fields = {
            "indicator_code": str,
            "country_code": str,
            "period": str,
            "value": (int, float),
            "standard_unit": str,
        }
        for fname, expected_type in required_fields.items():
            if fname not in data:
                failures.append(ValidationFailure(
                    layer="SCHEMA",
                    field=fname,
                    message=f"Required field '{fname}' is missing.",
                    severity="ERROR",
                ))
                continue
            val = data[fname]
            if val is None:
                failures.append(ValidationFailure(
                    layer="SCHEMA",
                    field=fname,
                    message=f"Field '{fname}' must not be null.",
                    severity="ERROR",
                ))
                continue
            if not isinstance(val, expected_type):
                failures.append(ValidationFailure(
                    layer="SCHEMA",
                    field=fname,
                    message=f"Field '{fname}' expected {expected_type}, got {type(val).__name__}.",
                    severity="ERROR",
                ))

        # country_code must be exactly 3 characters
        if "country_code" in data and isinstance(data.get("country_code"), str):
            if len(data["country_code"]) != 3:
                failures.append(ValidationFailure(
                    layer="SCHEMA",
                    field="country_code",
                    message=f"country_code must be exactly 3 characters, got '{data['country_code']}'.",
                    severity="ERROR",
                ))

        return failures

    def validate_range(self, indicator_code: str, value: float) -> list[ValidationFailure]:
        constraints = RANGE_CONSTRAINTS.get(indicator_code)
        if constraints is None:
            return []

        failures: list[ValidationFailure] = []
        min_val = constraints["min"]
        max_val = constraints["max"]
        if value < min_val or value > max_val:
            failures.append(ValidationFailure(
                layer="RANGE",
                field="value",
                message=(
                    f"{indicator_code} value {value} is outside acceptable range "
                    f"[{min_val}, {max_val}]."
                ),
                severity="ERROR",
            ))
        return failures

    def validate_consistency(
        self,
        indicator_code: str,
        country_code: str,
        period: str,
        value: float,
    ) -> list[ValidationFailure]:
        failures: list[ValidationFailure] = []
        constraints = RANGE_CONSTRAINTS.get(indicator_code, {})
        max_qoq = constraints.get("max_qoq_change_pct")
        if max_qoq is None:
            return failures

        # Fetch the most recent gold record for this indicator/country
        prev = (
            self._db.query(GoldRecord)
            .filter(
                GoldRecord.indicator_code == indicator_code,
                GoldRecord.country_code == country_code,
                GoldRecord.period != period,
            )
            .order_by(GoldRecord.period.desc())
            .first()
        )
        if prev is None or prev.value is None:
            return failures

        old_val = prev.value
        if old_val == 0:
            return failures

        change_pct = abs((value - old_val) / abs(old_val)) * 100
        if change_pct > max_qoq:
            failures.append(ValidationFailure(
                layer="CONSISTENCY",
                field="value",
                message=(
                    f"{indicator_code} change of {change_pct:.1f}% from previous period "
                    f"({old_val}) exceeds max allowed {max_qoq}%."
                ),
                severity="ERROR",
            ))
        return failures

    def validate_freshness(
        self,
        indicator_code: str,
        country_code: str,
        crawled_at: datetime,
    ) -> list[ValidationFailure]:
        failures: list[ValidationFailure] = []
        expected_days = EXPECTED_FREQUENCIES.get(indicator_code, _DEFAULT_FREQUENCY_DAYS)
        age_days = (datetime.utcnow() - crawled_at).days
        if age_days > 2 * expected_days:
            failures.append(ValidationFailure(
                layer="FRESHNESS",
                field="crawled_at",
                message=(
                    f"Data for {indicator_code}/{country_code} is {age_days} days old; "
                    f"expected refresh every {expected_days} days."
                ),
                severity="WARNING",
            ))
        return failures

    def validate(self, data: dict, crawled_at: Optional[datetime] = None) -> ValidationReport:
        failures: list[ValidationFailure] = []

        # Layer 1: Schema
        failures.extend(self.validate_schema(data))

        # Only proceed with deeper layers if we have the basic fields
        indicator_code = data.get("indicator_code", "")
        country_code = data.get("country_code", "")
        period = data.get("period", "")
        raw_value = data.get("value")

        if isinstance(raw_value, (int, float)) and not isinstance(raw_value, bool):
            value = float(raw_value)

            # Layer 2: Range
            failures.extend(self.validate_range(indicator_code, value))

            # Layer 3: Consistency
            failures.extend(self.validate_consistency(indicator_code, country_code, period, value))

            # Layer 4: Freshness
            if crawled_at is not None:
                failures.extend(self.validate_freshness(indicator_code, country_code, crawled_at))

        error_count = sum(1 for f in failures if f.severity == "ERROR")
        warning_count = sum(1 for f in failures if f.severity == "WARNING")
        score = max(0.0, 100.0 - error_count * 20.0 - warning_count * 5.0)
        passed = score >= 70.0 and not any(f.severity == "ERROR" for f in failures)

        return ValidationReport(
            score=score,
            passed=passed,
            failures=failures,
            indicator_code=indicator_code,
            country_code=country_code,
            period=period,
        )
