"""Pillar 8 — Transparency: Citation formatting and validation for chatbot responses.
Satisfies MiFID II research disclosure requirements.
"""

import re
from typing import Optional

CITATION_PATTERN_TEMPLATE = "{indicator_name}: {value} {unit} [{source_name}, {publication_date}]"
LINEAGE_FOOTER_TEMPLATE = (
    "\n\n**Data lineage:** Full citation trail available at "
    "/api/citation/{indicator_id}/{period}"
)

CITATION_REGEX_PATTERNS: list[re.Pattern] = [
    # [Source: ...] or [Sources: ...]
    re.compile(r"\[Sources?:[^\]]+\]", re.IGNORECASE),
    # [World Bank, YYYY] style
    re.compile(r"\[(World Bank|IMF|OECD|FRED|Eurostat)[^\]]*\]", re.IGNORECASE),
    # Inline citation: "2.5 PCT [World Bank, 2024]"
    re.compile(r"\d[\d.,]*\s+\w+\s+\[([\w\s,]+)\]"),
    # HTTP/HTTPS URL
    re.compile(r"https?://\S+"),
    # "Source:" label anywhere in the response
    re.compile(r"\bSource(?:s)?:", re.IGNORECASE),
    # Explicit named sources referenced inline
    re.compile(r"\b(World Bank|IMF|OECD|FRED|Eurostat|United Nations|CIA World Factbook)\b", re.IGNORECASE),
]


class CitationFormatter:
    def format_single(
        self,
        indicator_name: str,
        value: float,
        unit: str,
        source_name: str,
        publication_date: str,
    ) -> str:
        return f"{indicator_name}: {value} {unit} [{source_name}, {publication_date}]"

    def format_multi(self, primary_source: str, corroborating_sources: list[str]) -> str:
        if corroborating_sources:
            return (
                f"[Sources: {primary_source} (primary), "
                f"{', '.join(corroborating_sources)} (corroborating)]"
            )
        return f"[Sources: {primary_source} (primary)]"

    def append_lineage_footer(self, response: str, indicator_id: str, period: str) -> str:
        return response + LINEAGE_FOOTER_TEMPLATE.format(
            indicator_id=indicator_id,
            period=period,
        )

    def format_response_with_citations(
        self,
        response: str,
        citations: list[dict],
    ) -> str:
        if not citations:
            return response

        primary = citations[0]
        result = response

        # Append lineage footer for the primary indicator
        result = self.append_lineage_footer(
            result,
            indicator_id=primary.get("indicator_id", ""),
            period=primary.get("period", ""),
        )
        return result


class CitationValidator:
    def has_citation(self, response: str) -> bool:
        for pattern in CITATION_REGEX_PATTERNS:
            if pattern.search(response):
                return True
        return False

    def validate(self, response: str) -> tuple[bool, str]:
        if self.has_citation(response):
            return (True, "")
        return (
            False,
            "Response missing source citation — will request LLM to add citations",
        )
