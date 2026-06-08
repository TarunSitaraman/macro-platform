"""Unit tests for DQ scoring engine."""

from datetime import datetime, timezone

import pytest

from src.agents.qa import (
    compute_dq_score, detect_forecast, parse_value,
    score_accuracy, score_completeness,
)


def test_parse_value_plain_number():
    assert parse_value("2.5", "PCT") == pytest.approx(2.5)


def test_parse_value_trillion():
    assert parse_value("1.5T", "USD_BN") == pytest.approx(1500.0)


def test_parse_value_billion():
    assert parse_value("500B", "USD_BN") == pytest.approx(500.0)


def test_parse_value_million():
    assert parse_value("250000M", "USD_BN") == pytest.approx(250.0)


def test_parse_value_none_on_garbage():
    assert parse_value("N/A", "PCT") is None
    assert parse_value("", "PCT") is None


def test_detect_forecast_true():
    assert detect_forecast("GDP forecast for 2025") is True
    assert detect_forecast("projected to grow") is True
    assert detect_forecast("estimate: 3.2%") is True


def test_detect_forecast_false():
    assert detect_forecast("GDP grew 2.1% in 2023") is False


def test_score_accuracy_in_bounds():
    score, failures = score_accuracy(3.5, "3.5", "GDP_GROWTH", "PCT")
    assert score == 100.0
    assert failures == []


def test_score_accuracy_out_of_bounds():
    score, failures = score_accuracy(200.0, "200", "GDP_GROWTH", "PCT")
    assert score < 100.0
    assert any("above_max" in f for f in failures)


def test_score_accuracy_none_value():
    score, failures = score_accuracy(None, "N/A", "GDP_GROWTH", "PCT")
    assert score == 0.0
    assert "value_not_parseable" in failures


def test_score_completeness_all_present():
    score, failures = score_completeness(
        "GDP_GROWTH", "USA", "2023", "https://example.com", "3.5"
    )
    assert score == 100.0
    assert failures == []


def test_score_completeness_missing_fields():
    score, failures = score_completeness(None, "USA", None, None, "3.5")
    assert score < 100.0
    assert "missing_indicator_code" in failures
    assert "missing_period" in failures


def test_compute_dq_score_auto_promote():
    result = compute_dq_score(
        value=3.5,
        raw_value="3.5",
        indicator_code="GDP_GROWTH",
        country_code="USA",
        period="2023",
        source_url="https://api.worldbank.org",
        standard_unit="PCT",
        raw_unit="percent",
        crawled_at=datetime.now(timezone.utc),
    )
    assert result["dq_score"] >= 90.0
    assert result["failure_reasons"] == []


def test_compute_dq_score_rejected():
    result = compute_dq_score(
        value=None,
        raw_value="N/A",
        indicator_code=None,
        country_code=None,
        period=None,
        source_url=None,
        standard_unit="PCT",
        raw_unit=None,
        crawled_at=datetime.now(timezone.utc),
    )
    assert result["dq_score"] < 70.0
    assert len(result["failure_reasons"]) > 0
