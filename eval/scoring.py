"""Pure scoring helpers for the ground-truth evaluation harness.

Kept free of DB/LLM/IO so they can be unit-tested in isolation. The scorer
(score_rag.py) wires these against live retrieval and chatbot responses.
"""

import re
from typing import Any, Optional

# Grouped numbers with thousands separators (require >=1 comma group) first,
# then plain integers/decimals of any length. Ordering matters: a plain "2023"
# must not be partially consumed by the grouped alternative.
_NUMBER_RE = re.compile(r"-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+(?:\.\d+)?")


def extract_numbers(text: str) -> list[float]:
    """Pull all numeric literals from free text (handles thousands separators)."""
    out: list[float] = []
    for m in _NUMBER_RE.finditer(text or ""):
        try:
            out.append(float(m.group().replace(",", "")))
        except ValueError:
            continue
    return out


def numeric_match(
    expected: float,
    text: str,
    rel_tol: float = 0.02,
    abs_tol: float = 0.1,
) -> bool:
    """True if `expected` appears in `text` within relative or absolute tolerance.

    A number counts as a match if it is within rel_tol (fraction of the expected
    magnitude) OR within abs_tol of the expected value. The absolute floor lets
    small values (e.g. 2.1%) match despite rounding in the prose.
    """
    for num in extract_numbers(text):
        if abs(num - expected) <= abs_tol:
            return True
        denom = max(abs(expected), 1e-9)
        if abs(num - expected) / denom <= rel_tol:
            return True
    return False


def text_contains(needle: str, haystack: str) -> bool:
    """Case-insensitive substring check, tolerant of None."""
    if not needle or not haystack:
        return False
    return needle.lower() in haystack.lower()


def hit_at_k(expected_ids: list[str], retrieved_ids: list[str], k: int) -> bool:
    """True if any expected record id is within the top-k retrieved ids."""
    top = set(str(r) for r in retrieved_ids[:k])
    return any(str(e) in top for e in expected_ids)


def recall_at_k(expected_ids: list[str], retrieved_ids: list[str], k: int) -> float:
    """Fraction of expected record ids found within the top-k retrieved ids."""
    if not expected_ids:
        return 0.0
    top = set(str(r) for r in retrieved_ids[:k])
    found = sum(1 for e in expected_ids if str(e) in top)
    return found / len(expected_ids)


def score_answer(
    entry: dict[str, Any],
    response: str,
    citations: Optional[list[dict[str, Any]]] = None,
    rel_tol: float = 0.02,
    abs_tol: float = 0.1,
) -> dict[str, Any]:
    """Grade a single chatbot response against one ground-truth entry.

    Returns a dict of booleans/metrics. `correct` is the headline pass/fail:
    numeric answers must contain the expected value; categorical/direction
    answers must mention the expected answer string.
    """
    citations = citations or []
    kind = entry.get("answer_kind", "numeric")

    if kind == "numeric":
        value_correct = numeric_match(
            entry["expected_value"], response, rel_tol=rel_tol, abs_tol=abs_tol
        )
        correct = value_correct
    else:
        value_correct = None
        correct = text_contains(entry.get("expected_answer", ""), response)

    cited_sources = " ".join(str(c.get("source_name", "")) for c in citations)
    source_cited = text_contains(entry.get("expected_source", ""), cited_sources) \
        or text_contains(entry.get("expected_source", ""), response)

    return {
        "id": entry.get("id"),
        "question_type": entry.get("question_type"),
        "correct": bool(correct),
        "value_correct": value_correct,
        "source_cited": bool(source_cited),
    }


def aggregate(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Roll per-item results up into overall and per-type accuracy."""
    total = len(results)
    if total == 0:
        return {"total": 0}

    correct = sum(1 for r in results if r.get("correct"))
    sourced = sum(1 for r in results if r.get("source_cited"))

    by_type: dict[str, dict[str, int]] = {}
    for r in results:
        t = r.get("question_type", "unknown")
        bucket = by_type.setdefault(t, {"total": 0, "correct": 0})
        bucket["total"] += 1
        bucket["correct"] += 1 if r.get("correct") else 0

    return {
        "total": total,
        "accuracy": round(correct / total, 4),
        "source_cited_rate": round(sourced / total, 4),
        "by_type": {
            t: {**b, "accuracy": round(b["correct"] / b["total"], 4)}
            for t, b in sorted(by_type.items())
        },
    }
