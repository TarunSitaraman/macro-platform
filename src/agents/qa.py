"""Data Quality scoring engine — produces DQ score and routes to gold / review / reject."""

import re
from datetime import datetime, timezone
from typing import Optional

FORECAST_KEYWORDS = {
    "forecast", "projection", "estimate", "expected", "outlook",
    "projected", "estimated", "anticipated", "predicted",
}

# Reasonable absolute value bounds per unit type
VALUE_BOUNDS: dict[str, tuple[Optional[float], Optional[float]]] = {
    "PCT": (-100.0, 100.0),
    "PCT_GDP": (-200.0, 200.0),
    "USD_BN": (0.0, None),
    "INDEX": (0.0, None),
}

# Sanity bounds per indicator (override unit defaults where needed)
INDICATOR_BOUNDS: dict[str, tuple[Optional[float], Optional[float]]] = {
    "GDP_GROWTH": (-30.0, 30.0),
    "CPI_INFLATION": (-20.0, 1000.0),
    "UNEMPLOYMENT_RATE": (0.0, 100.0),
    "CURRENT_ACCOUNT_PCT_GDP": (-50.0, 50.0),
    "GOVT_DEBT_PCT_GDP": (0.0, 500.0),
}


def score_accuracy(
    value: Optional[float],
    raw_value: str,
    indicator_code: str,
    standard_unit: str,
) -> tuple[float, list[str]]:
    """
    Accuracy sub-score (0-100): is the value parseable and within plausible bounds?
    Returns (score, failure_reasons).
    """
    failures: list[str] = []

    if value is None:
        return 0.0, ["value_not_parseable"]

    # Check indicator-specific bounds first, then unit bounds
    lo, hi = INDICATOR_BOUNDS.get(indicator_code, VALUE_BOUNDS.get(standard_unit, (None, None)))
    if lo is not None and value < lo:
        failures.append(f"value_{value}_below_min_{lo}")
    if hi is not None and value > hi:
        failures.append(f"value_{value}_above_max_{hi}")

    if failures:
        return 20.0, failures
    return 100.0, []


def score_completeness(
    indicator_code: Optional[str],
    country_code: Optional[str],
    period: Optional[str],
    source_url: Optional[str],
    raw_value: Optional[str],
) -> tuple[float, list[str]]:
    """
    Completeness sub-score (0-100): required fields present?
    """
    failures: list[str] = []
    required = {
        "indicator_code": indicator_code,
        "country_code": country_code,
        "period": period,
        "raw_value": raw_value,
    }
    for field, val in required.items():
        if not val or str(val).strip() in ("", "None", "null", "N/A", ".."):
            failures.append(f"missing_{field}")

    score = 100.0 - (len(failures) / len(required)) * 100
    return score, failures


def score_timeliness(crawled_at: datetime, period: str) -> tuple[float, list[str]]:
    """
    Timeliness sub-score (0-100): how recent is the data relative to the reported period?
    """
    now = datetime.now(timezone.utc)
    if crawled_at.tzinfo is None:
        crawled_at = crawled_at.replace(tzinfo=timezone.utc)

    age_days = (now - crawled_at).days

    # Extract year from period string
    year_match = re.search(r"(\d{4})", period or "")
    if not year_match:
        return 70.0, ["period_format_unrecognized"]

    data_year = int(year_match.group(1))
    current_year = now.year
    years_old = current_year - data_year

    # Penalise very stale data (>5 years) and very slow collection (>90 days)
    if years_old > 5:
        return 50.0, [f"data_{years_old}_years_old"]
    if age_days > 90:
        return 70.0, [f"crawled_{age_days}_days_ago"]
    return 100.0, []


def score_consistency(
    value: Optional[float],
    standard_unit: Optional[str],
    raw_unit: Optional[str],
) -> tuple[float, list[str]]:
    """
    Consistency sub-score (0-100): unit coherence and normalisation feasibility.
    """
    failures: list[str] = []

    if not standard_unit:
        failures.append("missing_standard_unit")
        return 50.0, failures

    if raw_unit and raw_unit.strip() not in ("", "None"):
        # Flag if units look incompatible (simple heuristic)
        pct_units = {"percent", "%", "pct", "percentage", "rate"}
        raw_lower = raw_unit.lower()
        if standard_unit == "PCT" and not any(u in raw_lower for u in pct_units | {"index"}):
            failures.append(f"unit_mismatch_raw={raw_unit}_expected_PCT")
            return 60.0, failures

    return 100.0, []


def compute_dq_score(
    value: Optional[float],
    raw_value: Optional[str],
    indicator_code: Optional[str],
    country_code: Optional[str],
    period: Optional[str],
    source_url: Optional[str],
    standard_unit: Optional[str],
    raw_unit: Optional[str],
    crawled_at: Optional[datetime],
) -> dict:
    """
    Compute full DQ score and return breakdown dict.
    Weights: accuracy=40, completeness=30, timeliness=20, consistency=10.
    """
    crawled_at = crawled_at or datetime.now(timezone.utc)

    acc_score, acc_failures = score_accuracy(
        value, raw_value or "", indicator_code or "", standard_unit or ""
    )
    comp_score, comp_failures = score_completeness(
        indicator_code, country_code, period, source_url, raw_value
    )
    time_score, time_failures = score_timeliness(crawled_at, period or "")
    cons_score, cons_failures = score_consistency(value, standard_unit, raw_unit)

    overall = (
        acc_score * 0.40
        + comp_score * 0.30
        + time_score * 0.20
        + cons_score * 0.10
    )

    all_failures = acc_failures + comp_failures + time_failures + cons_failures

    return {
        "dq_score": round(overall, 2),
        "dq_breakdown": {
            "accuracy": round(acc_score, 2),
            "completeness": round(comp_score, 2),
            "timeliness": round(time_score, 2),
            "consistency": round(cons_score, 2),
        },
        "failure_reasons": all_failures,
    }


def detect_forecast(raw_value: str, source_note: str = "") -> bool:
    """Return True if the value is flagged as a forecast/projection."""
    combined = (raw_value + " " + source_note).lower()
    return any(kw in combined for kw in FORECAST_KEYWORDS)


def parse_value(raw_value: str, standard_unit: str) -> Optional[float]:
    """
    Parse raw string value to float and apply unit normalisation.
    e.g. "$1.23 trillion" → 1230.0 (USD_BN)
    """
    if not raw_value:
        return None

    raw = raw_value.strip().lower()

    # Strip common non-numeric characters
    raw = re.sub(r"[,$€£¥%\s]", "", raw)

    # Handle multiplier suffixes
    multiplier = 1.0
    if raw.endswith("t") or "trillion" in raw_value.lower():
        multiplier = 1_000.0  # to billions
        raw = raw.rstrip("t")
    elif raw.endswith("b") or "billion" in raw_value.lower():
        multiplier = 1.0
        raw = raw.rstrip("b")
    elif raw.endswith("m") or "million" in raw_value.lower():
        multiplier = 0.001  # to billions
        raw = raw.rstrip("m")

    # Remove remaining non-numeric (except . and -)
    raw = re.sub(r"[^\d.\-]", "", raw)

    try:
        return float(raw) * multiplier
    except ValueError:
        return None
