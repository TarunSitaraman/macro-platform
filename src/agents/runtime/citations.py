"""Build structured citations and context records from retrieved tool data.

Citations are derived from the structured records attached to each ToolResult,
not parsed back out of the LLM's generated answer. This means a citation survives
any formatting drift in the prose (e.g. the model omitting or rewording the
inline `[Source: ...]` marker) — the source list reflects what was actually
retrieved, not what the model happened to type.
"""

from typing import Any


def _composite_key(r: dict[str, Any]) -> tuple:
    """Fallback identity for records lacking a record_id (news, forecasts)."""
    return (
        r.get("type"),
        r.get("source_name"),
        r.get("indicator_code"),
        r.get("country_code"),
        r.get("period"),
        r.get("title"),
    )


def dedupe_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate retrieved records, preserving first-seen order.

    Keyed by record_id when present, otherwise by a composite of identifying
    metadata so rows without an id (news, forecasts) still collapse cleanly.
    """
    seen: set = set()
    unique: list[dict[str, Any]] = []
    for r in records:
        key = r.get("record_id") or _composite_key(r)
        if key in seen:
            continue
        seen.add(key)
        unique.append(r)
    return unique


def build_citations(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build a deduplicated citation list from structured context records.

    One citation per unique (source, indicator, country, period, title) so the
    answer carries a clean bibliography regardless of the model's text. Records
    without a source name are skipped (nothing to attribute).
    """
    seen: set = set()
    citations: list[dict[str, Any]] = []
    for r in records:
        source_name = r.get("source_name")
        if not source_name:
            continue
        key = (
            source_name,
            r.get("indicator_code"),
            r.get("country_code"),
            r.get("period"),
            r.get("title"),
        )
        if key in seen:
            continue
        seen.add(key)
        citations.append({
            "record_id": r.get("record_id"),
            "type": r.get("type", "gold"),
            "source_name": source_name,
            "source_url": r.get("source_url"),
            "indicator_code": r.get("indicator_code"),
            "country_code": r.get("country_code"),
            "period": r.get("period"),
            "value": r.get("value"),
            "unit": r.get("unit"),
            "title": r.get("title"),
        })
    return citations
