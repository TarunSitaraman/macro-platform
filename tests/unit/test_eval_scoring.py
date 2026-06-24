"""Unit tests for the ground-truth scoring helpers."""

from eval.scoring import (
    aggregate,
    extract_numbers,
    hit_at_k,
    numeric_match,
    recall_at_k,
    score_answer,
    text_contains,
)


def test_extract_numbers_handles_separators_and_signs():
    nums = extract_numbers("GDP was 1,234.5 and growth -0.8% with 2023 baseline")
    assert 1234.5 in nums
    assert -0.8 in nums
    assert 2023.0 in nums


def test_numeric_match_within_absolute_tolerance():
    assert numeric_match(2.5, "Inflation was about 2.5% last year")
    assert numeric_match(2.5, "roughly 2.55 percent", abs_tol=0.1)
    assert not numeric_match(2.5, "it reached 9.9%")


def test_numeric_match_within_relative_tolerance():
    assert numeric_match(1000.0, "around 1015 billion", rel_tol=0.02, abs_tol=0.1)
    assert not numeric_match(1000.0, "around 1500 billion", rel_tol=0.02, abs_tol=0.1)


def test_text_contains_is_case_insensitive():
    assert text_contains("World Bank", "Cited from the WORLD BANK dataset")
    assert not text_contains("IMF", "Source: World Bank")


def test_hit_and_recall_at_k():
    expected = ["a", "b"]
    retrieved = ["x", "a", "y", "z"]
    assert hit_at_k(expected, retrieved, k=2) is True   # "a" in top-2
    assert hit_at_k(expected, retrieved, k=1) is False  # only "x" in top-1
    assert recall_at_k(expected, retrieved, k=4) == 0.5


def test_score_answer_numeric():
    entry = {
        "id": "gt-1",
        "question_type": "point_lookup",
        "answer_kind": "numeric",
        "expected_value": 2.5,
        "expected_source": "World Bank",
    }
    cites = [{"source_name": "World Bank Open Data"}]
    res = score_answer(entry, "GDP grew 2.5% in 2023.", cites)
    assert res["correct"] is True
    assert res["value_correct"] is True
    assert res["source_cited"] is True


def test_score_answer_categorical():
    entry = {
        "id": "gt-2",
        "question_type": "comparison",
        "answer_kind": "categorical",
        "expected_answer": "Italy",
        "expected_source": "IMF",
    }
    res = score_answer(entry, "Italy had the higher debt ratio.", [])
    assert res["correct"] is True
    assert res["value_correct"] is None


def test_aggregate_rolls_up_by_type():
    results = [
        {"question_type": "point_lookup", "correct": True, "source_cited": True},
        {"question_type": "point_lookup", "correct": False, "source_cited": False},
        {"question_type": "comparison", "correct": True, "source_cited": True},
    ]
    agg = aggregate(results)
    assert agg["total"] == 3
    assert agg["accuracy"] == round(2 / 3, 4)
    assert agg["by_type"]["point_lookup"]["accuracy"] == 0.5
    assert agg["by_type"]["comparison"]["accuracy"] == 1.0
