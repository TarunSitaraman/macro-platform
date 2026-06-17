"""Human-readable DQ trust explanations — how and why data is trustable."""

from typing import Any, Optional

from src.config import get_settings

settings = get_settings()

DQ_WEIGHTS = {
    "accuracy": 0.40,
    "completeness": 0.30,
    "timeliness": 0.20,
    "consistency": 0.10,
}

DIMENSION_META = {
    "accuracy": {
        "label": "Accuracy",
        "description": "Is the value parseable and within plausible bounds for this indicator?",
    },
    "completeness": {
        "label": "Completeness",
        "description": "Are required fields present (indicator, country, period, value)?",
    },
    "timeliness": {
        "label": "Timeliness",
        "description": "How recent is the data relative to its reporting period and crawl date?",
    },
    "consistency": {
        "label": "Consistency",
        "description": "Do units and normalisation look coherent across the pipeline?",
    },
}

FAILURE_REASON_LABELS = {
    "value_not_parseable": "The raw value could not be parsed as a number.",
    "missing_indicator_code": "Required field: indicator code was missing.",
    "missing_country_code": "Required field: country code was missing.",
    "missing_period": "Required field: reporting period was missing.",
    "missing_raw_value": "Required field: raw value was missing.",
    "missing_standard_unit": "Standard unit was not assigned during normalisation.",
    "period_format_unrecognized": "Reporting period format could not be interpreted for timeliness scoring.",
}


def _humanize_failure(reason: str) -> str:
    if reason in FAILURE_REASON_LABELS:
        return FAILURE_REASON_LABELS[reason]

    if reason.startswith("value_") and "_below_min_" in reason:
        parts = reason.replace("value_", "").split("_below_min_")
        if len(parts) == 2:
            return f"Value {parts[0]} is below the minimum plausible bound ({parts[1]})."
    if reason.startswith("value_") and "_above_max_" in reason:
        parts = reason.replace("value_", "").split("_above_max_")
        if len(parts) == 2:
            return f"Value {parts[0]} exceeds the maximum plausible bound ({parts[1]})."
    if reason.startswith("data_") and reason.endswith("_years_old"):
        years = reason.replace("data_", "").replace("_years_old", "")
        return f"Data is {years} years old relative to the current year."
    if reason.startswith("crawled_") and reason.endswith("_days_ago"):
        days = reason.replace("crawled_", "").replace("_days_ago", "")
        return f"Record was crawled {days} days ago (may affect freshness score)."
    if reason.startswith("unit_mismatch"):
        return f"Unit mismatch detected: {reason.replace('unit_mismatch_', '')}."

    return reason.replace("_", " ").capitalize()


def _trust_tier(score: Optional[float]) -> tuple[str, str]:
    promote = settings.dq_auto_promote_threshold
    review = settings.dq_review_threshold
    if score is None:
        return "unknown", "Quality score unavailable"
    if score >= promote:
        return "high", f"Auto-promoted (DQ ≥ {promote:.0f}%)"
    if score >= review:
        return "medium", f"Review band ({review:.0f}%–{promote:.0f}%)"
    return "low", f"Below review threshold (< {review:.0f}%)"


def _promotion_path(
    dq_status: Optional[str],
    approved_by: Optional[str],
    review_status: Optional[str],
) -> str:
    if review_status in ("APPROVED", "ADJUSTED"):
        return f"HUMAN_REVIEW_{review_status}"
    if dq_status:
        return str(dq_status)
    if approved_by and approved_by != "auto":
        return "HUMAN_APPROVED"
    return "AUTO_PROMOTED"


def _failure_dimension(reason: str) -> str:
    if reason.startswith("missing_"):
        return "completeness"
    if reason.startswith("value_") or reason == "value_not_parseable":
        return "accuracy"
    if "years_old" in reason or reason.startswith("crawled_") or "period_format" in reason:
        return "timeliness"
    if "unit" in reason:
        return "consistency"
    return "accuracy"


def build_trust_explanation(
    *,
    dq_score: Optional[float],
    dq_breakdown: Optional[dict[str, Any]] = None,
    failure_reasons: Optional[list[str]] = None,
    dq_status: Optional[str] = None,
    approved_by: Optional[str] = None,
    review_status: Optional[str] = None,
    reviewed_by: Optional[str] = None,
    review_notes: Optional[str] = None,
    source_name: Optional[str] = None,
    extraction_method: Optional[str] = None,
) -> dict[str, Any]:
    """
    Build a structured explanation of how and why a record is (or isn't) trustable.
    """
    breakdown = dq_breakdown or {}
    failures = failure_reasons or []
    tier, tier_label = _trust_tier(dq_score)
    promotion = _promotion_path(dq_status, approved_by, review_status)

    dimensions = []
    for key, weight in DQ_WEIGHTS.items():
        score = breakdown.get(key)
        if score is None and dq_score is not None:
            score = 0.0
        meta = DIMENSION_META[key]
        dim_issues = [
            _humanize_failure(f)
            for f in failures
            if _failure_dimension(f) == key
        ]
        dimensions.append({
            "name": key,
            "label": meta["label"],
            "score": score,
            "weight": weight,
            "weight_pct": int(weight * 100),
            "weighted_contribution": round((score or 0) * weight, 2) if score is not None else None,
            "description": meta["description"],
            "issues": dim_issues,
        })

    failure_human = [_humanize_failure(r) for r in failures]
    why_trustable: list[str] = []
    caveats: list[str] = []

    if dq_score is not None:
        why_trustable.append(
            f"Composite DQ score is {dq_score:.1f}% "
            f"(accuracy 40% + completeness 30% + timeliness 20% + consistency 10%)."
        )

    if tier == "high":
        why_trustable.append(
            f"Met the auto-promote threshold (≥ {settings.dq_auto_promote_threshold:.0f}%) "
            "and was published to the Gold layer without human review."
        )
    elif tier == "medium":
        caveats.append(
            f"Score is in the human-review band ({settings.dq_review_threshold:.0f}%–"
            f"{settings.dq_auto_promote_threshold:.0f}%). Verify before high-stakes use."
        )
    elif tier == "low":
        caveats.append("Score is below the review threshold and should not be treated as production-ready.")

    if promotion.startswith("HUMAN_REVIEW"):
        why_trustable.append(
            f"An analyst reviewed this record ({review_status})"
            + (f" — {reviewed_by}" if reviewed_by else "")
            + " before it reached Gold."
        )
    elif approved_by and approved_by != "auto":
        why_trustable.append(f"Approved for Gold by {approved_by}.")

    if source_name:
        why_trustable.append(f"Sourced from {source_name}.")
    if extraction_method:
        why_trustable.append(f"Ingested via {extraction_method.replace('_', ' ').lower()}.")

    for dim in dimensions:
        if dim["score"] is not None and dim["score"] >= 90:
            why_trustable.append(f"{dim['label']} scored {dim['score']:.0f}% — no material issues.")
        elif dim["score"] is not None and dim["score"] < 70:
            caveats.append(f"{dim['label']} scored only {dim['score']:.0f}%.")

    if failure_human:
        caveats.extend(failure_human)

    if review_notes:
        caveats.append(f"Reviewer notes: {review_notes}")

    summary_parts = []
    if dq_score is not None:
        summary_parts.append(f"DQ {dq_score:.1f}% ({tier_label.lower()}).")
    if promotion.startswith("HUMAN"):
        summary_parts.append("Human-reviewed before Gold.")
    else:
        summary_parts.append("Automated pipeline promotion.")
    if not caveats:
        summary_parts.append("No outstanding quality flags.")
    summary = " ".join(summary_parts)

    return {
        "dq_score": dq_score,
        "trust_tier": tier,
        "trust_label": tier_label,
        "promotion_path": promotion,
        "approved_by": approved_by,
        "summary": summary,
        "scoring_model": {
            "weights": {k: v for k, v in DQ_WEIGHTS.items()},
            "auto_promote_threshold": settings.dq_auto_promote_threshold,
            "review_threshold": settings.dq_review_threshold,
        },
        "dimensions": dimensions,
        "failure_reasons": failures,
        "failure_reasons_human": failure_human,
        "why_trustable": why_trustable,
        "caveats": caveats,
    }
