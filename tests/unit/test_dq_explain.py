"""Unit tests for DQ trust explanations."""

from src.utils.dq_explain import build_trust_explanation, _humanize_failure


def test_build_trust_explanation_high_score():
    result = build_trust_explanation(
        dq_score=94.5,
        dq_breakdown={
            "accuracy": 100.0,
            "completeness": 100.0,
            "timeliness": 85.0,
            "consistency": 100.0,
        },
        failure_reasons=[],
        dq_status="AUTO_PROMOTED",
        approved_by="auto",
        source_name="World Bank",
        extraction_method="API",
    )
    assert result["trust_tier"] == "high"
    assert result["dq_score"] == 94.5
    assert len(result["dimensions"]) == 4
    assert result["dimensions"][0]["weight_pct"] == 40
    assert any("auto-promote" in w.lower() for w in result["why_trustable"])
    assert not result["caveats"]


def test_build_trust_explanation_with_failures():
    result = build_trust_explanation(
        dq_score=72.0,
        dq_breakdown={
            "accuracy": 100.0,
            "completeness": 75.0,
            "timeliness": 50.0,
            "consistency": 100.0,
        },
        failure_reasons=["data_6_years_old", "missing_raw_value"],
        dq_status="REVIEW",
        review_status="APPROVED",
        reviewed_by="analyst@example.com",
    )
    assert result["trust_tier"] == "medium"
    assert result["promotion_path"] == "HUMAN_REVIEW_APPROVED"
    assert len(result["failure_reasons_human"]) == 2
    assert any("analyst reviewed" in w.lower() for w in result["why_trustable"])


def test_humanize_failure_bounds():
    msg = _humanize_failure("value_150.0_above_max_100.0")
    assert "150" in msg
    assert "100" in msg
