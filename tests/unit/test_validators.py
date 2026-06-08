"""Unit tests for input validators."""

from src.utils.validators import (
    is_valid_country_code, is_valid_indicator, is_valid_period, validate_raw_record,
)


def test_valid_country_codes():
    assert is_valid_country_code("USA") is True
    assert is_valid_country_code("GBR") is True
    assert is_valid_country_code("CHN") is True


def test_invalid_country_codes():
    assert is_valid_country_code("US") is False
    assert is_valid_country_code("united states") is False
    assert is_valid_country_code(None) is False
    assert is_valid_country_code("") is False


def test_valid_periods():
    assert is_valid_period("2024") is True
    assert is_valid_period("2024-06") is True
    assert is_valid_period("2024-Q2") is True
    assert is_valid_period("2024-06-01") is True


def test_invalid_periods():
    assert is_valid_period("24") is False
    assert is_valid_period("Q2-2024") is False
    assert is_valid_period("latest") is False
    assert is_valid_period(None) is False


def test_valid_indicators():
    assert is_valid_indicator("GDP_GROWTH") is True
    assert is_valid_indicator("CPI_INFLATION") is True


def test_invalid_indicators():
    assert is_valid_indicator("MADE_UP") is False
    assert is_valid_indicator(None) is False


def test_validate_raw_record_clean():
    rec = {
        "indicator_code": "GDP_GROWTH",
        "country_code": "USA",
        "period": "2023",
        "raw_value": "3.5",
    }
    assert validate_raw_record(rec) == []


def test_validate_raw_record_multiple_errors():
    rec = {
        "indicator_code": "BOGUS",
        "country_code": "US",
        "period": "latest",
        "raw_value": None,
    }
    errors = validate_raw_record(rec)
    assert len(errors) == 4
