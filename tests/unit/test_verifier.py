"""Unit tests for the response verifier."""

from src.agents.runtime.types import ToolResult
from src.agents.runtime.verifier import ResponseVerifier


def test_verifier_high_confidence_when_grounded():
    verifier = ResponseVerifier(tolerance=0.05)
    tool_results = [
        ToolResult(
            tool_name="search_gold_records",
            success=True,
            data={"records": [{"value": 2.5, "source_name": "FRED", "period": "2023"}]},
            record_ids=["abc"],
        )
    ]
    response = "Inflation was 2.5% [Source: FRED, 2023]."
    confidence, warnings, citations = verifier.verify(response, tool_results)
    assert confidence == "high"
    assert not warnings
    assert len(citations) == 1


def test_verifier_low_confidence_on_hallucination():
    verifier = ResponseVerifier(tolerance=0.01)
    tool_results = [
        ToolResult(
            tool_name="search_gold_records",
            success=True,
            data={"records": [{"value": 2.5}]},
            record_ids=["abc"],
        )
    ]
    response = "GDP growth reached 99.9% in 2023."
    confidence, warnings, citations = verifier.verify(response, tool_results)
    assert confidence in ("low", "medium")
    assert warnings


def test_verifier_no_data_no_numbers():
    verifier = ResponseVerifier()
    confidence, warnings, _ = verifier.verify(
        "Macro indicators are important for policy.",
        [],
    )
    assert confidence == "high"
