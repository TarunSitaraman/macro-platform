"""Post-generation response verifier — checks grounding against tool results."""

import re
from typing import Any

from src.agents.runtime.types import ToolResult

_CITATION_RE = re.compile(
    r"\[Source:\s*([^,\]]+),\s*([^\]]+)\]",
    re.IGNORECASE,
)
_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")


class ResponseVerifier:
    """Verify that numeric claims in a response are grounded in tool data."""

    def __init__(self, tolerance: float = 0.05) -> None:
        self.tolerance = tolerance

    def verify(
        self,
        response: str,
        tool_results: list[ToolResult],
    ) -> tuple[str, list[str], list[dict[str, Any]]]:
        """
        Returns (confidence, grounding_warnings, citations).
        confidence is one of: high, medium, low.
        """
        grounded_values = self._extract_grounded_values(tool_results)
        citations = self._parse_citations(response)
        warnings: list[str] = []

        if not grounded_values and self._has_numeric_claims(response):
            warnings.append("Response contains numeric claims but no grounded data was retrieved.")
            return "low", warnings, citations

        if not grounded_values:
            return "high", warnings, citations

        response_numbers = [
            float(m.group())
            for m in _NUMBER_RE.finditer(response)
            if self._is_significant_number(m.group())
        ]

        if not response_numbers:
            return "high", warnings, citations

        unmatched = 0
        for num in response_numbers:
            if not self._matches_grounded(num, grounded_values):
                unmatched += 1

        if unmatched == 0:
            return "high", warnings, citations

        ratio = unmatched / len(response_numbers)
        warnings.append(
            f"{unmatched} of {len(response_numbers)} numeric values could not be verified against retrieved data."
        )
        if ratio >= 0.5:
            return "low", warnings, citations
        return "medium", warnings, citations

    def _extract_grounded_values(self, tool_results: list[ToolResult]) -> list[float]:
        values: list[float] = []
        for result in tool_results:
            if not result.success:
                continue
            self._collect_numbers(result.data, values)
        return values

    def _collect_numbers(self, obj: Any, out: list[float]) -> None:
        if isinstance(obj, (int, float)):
            out.append(float(obj))
        elif isinstance(obj, dict):
            for key in ("value", "forecast_value", "yhat", "actual", "expected"):
                if key in obj and isinstance(obj[key], (int, float)):
                    out.append(float(obj[key]))
            for v in obj.values():
                self._collect_numbers(v, out)
        elif isinstance(obj, list):
            for item in obj:
                self._collect_numbers(item, out)

    def _parse_citations(self, response: str) -> list[dict[str, str]]:
        return [
            {"source_name": m.group(1).strip(), "period": m.group(2).strip()}
            for m in _CITATION_RE.finditer(response)
        ]

    def _has_numeric_claims(self, response: str) -> bool:
        return any(self._is_significant_number(m.group()) for m in _NUMBER_RE.finditer(response))

    def _is_significant_number(self, s: str) -> bool:
        try:
            v = float(s)
        except ValueError:
            return False
        # Skip years and small step indices
        if 1900 <= v <= 2100 and "." not in s:
            return False
        return abs(v) >= 0.01 or "." in s

    def _matches_grounded(self, num: float, grounded: list[float]) -> bool:
        for g in grounded:
            if g == 0:
                if abs(num) < self.tolerance:
                    return True
            elif abs(num - g) / max(abs(g), 1e-9) <= self.tolerance:
                return True
            elif abs(num - g) <= self.tolerance:
                return True
        return False
