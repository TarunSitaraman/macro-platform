"""Generate the ground-truth answer key from the verified gold data layer.

Every entry's answer is read (or computed) directly from gold_records, so the
answer key reflects the platform's verified reality and is fully reproducible.
Question types:

  point_lookup      single verified value for one indicator/country/period
  comparison        which of two countries is higher for an indicator/period
  superlative_high  which country is highest across all available, given period
  superlative_low   which country is lowest
  trend             did an indicator rise or fall between two years

Usage (from repo root):

    python -m eval.generate_ground_truth                 # default set
    python -m eval.generate_ground_truth --seed 7 --min-year 2005 --max-year 2024
    python -m eval.generate_ground_truth --out eval/ground_truth.json
"""

import argparse
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Optional

from dotenv import load_dotenv

load_dotenv()

from src.config import INDICATOR_CATALOGUE  # noqa: E402
from src.database import GoldRecord, SessionLocal  # noqa: E402
from eval.countries import country_name, unit_phrase  # noqa: E402

_YEAR_RE = re.compile(r"^\d{4}$")

# Target counts per question type (the generator caps at what the data supports).
DEFAULT_TARGETS = {
    "point_lookup": 120,
    "comparison": 40,
    "superlative_high": 20,
    "superlative_low": 20,
    "trend": 40,
}
TREND_GAP_YEARS = 5


def _indicator_name(code: str) -> str:
    meta = INDICATOR_CATALOGUE.get(code)
    return meta["name"] if meta else code


def _fmt_value(value: float, unit: Optional[str]) -> str:
    """Render a value + unit the way a correct answer would read."""
    num = f"{value:,.2f}".rstrip("0").rstrip(".")
    phrase = unit_phrase(unit)
    if phrase == "%":
        return f"{num}%"
    return f"{num} {phrase}".strip()


def load_authoritative(
    db, min_year: int, max_year: int
) -> dict[tuple[str, str, str], dict[str, Any]]:
    """Build the single authoritative record per (indicator, country, period).

    When multiple gold rows share a key (different sources / revisions), the one
    with the highest dq_score wins, tie-broken by most recent promotion.
    """
    rows = (
        db.query(GoldRecord)
        .filter(GoldRecord.value != None)  # noqa: E711
        .all()
    )
    best: dict[tuple[str, str, str], GoldRecord] = {}
    for r in rows:
        period = str(r.period)
        if not _YEAR_RE.match(period):
            continue
        year = int(period)
        if year < min_year or year > max_year:
            continue
        key = (r.indicator_code, r.country_code, period)
        cur = best.get(key)
        if cur is None or _is_better(r, cur):
            best[key] = r

    return {
        key: {
            "record_id": str(r.record_id),
            "indicator_code": r.indicator_code,
            "country_code": r.country_code,
            "period": str(r.period),
            "value": r.value,
            "unit": r.standard_unit,
            "source_name": r.source_name or "",
            "dq_score": r.dq_score,
        }
        for key, r in best.items()
    }


def _is_better(candidate: GoldRecord, current: GoldRecord) -> bool:
    cand_dq = candidate.dq_score if candidate.dq_score is not None else -1.0
    cur_dq = current.dq_score if current.dq_score is not None else -1.0
    if cand_dq != cur_dq:
        return cand_dq > cur_dq
    cand_t = candidate.promoted_at or datetime.min
    cur_t = current.promoted_at or datetime.min
    return cand_t > cur_t


def _by_indicator_period(auth: dict) -> dict[tuple[str, str], dict[str, dict]]:
    """index[(indicator, period)] = {country_code: record}"""
    idx: dict[tuple[str, str], dict[str, dict]] = defaultdict(dict)
    for (ind, country, period), rec in auth.items():
        idx[(ind, period)][country] = rec
    return idx


def _by_indicator_country(auth: dict) -> dict[tuple[str, str], dict[str, dict]]:
    """index[(indicator, country)] = {period: record}"""
    idx: dict[tuple[str, str], dict[str, dict]] = defaultdict(dict)
    for (ind, country, period), rec in auth.items():
        idx[(ind, country)][period] = rec
    return idx


def build_point_lookups(auth: dict, rng, target: int) -> list[dict]:
    """Stratify across indicators so no single indicator dominates the set."""
    by_indicator: dict[str, list] = defaultdict(list)
    for key, rec in auth.items():
        by_indicator[rec["indicator_code"]].append(rec)

    indicators = sorted(by_indicator)
    per_ind = max(1, target // max(1, len(indicators)))
    items: list[dict] = []
    for ind in indicators:
        recs = by_indicator[ind]
        rng.shuffle(recs)
        for rec in recs[:per_ind]:
            cc, period = rec["country_code"], rec["period"]
            q = (
                f"What was the {_indicator_name(ind)} of {country_name(cc)} "
                f"in {period}?"
            )
            items.append({
                "question_type": "point_lookup",
                "question": q,
                "answer_kind": "numeric",
                "expected_value": rec["value"],
                "unit": rec["unit"],
                "expected_answer": _fmt_value(rec["value"], rec["unit"]),
                "indicator_code": ind,
                "indicator_name": _indicator_name(ind),
                "country_code": cc,
                "period": period,
                "expected_source": rec["source_name"],
                "gold_record_ids": [rec["record_id"]],
            })
    return items[:target]


def build_comparisons(idx_ip: dict, rng, target: int) -> list[dict]:
    keys = [k for k, v in idx_ip.items() if len(v) >= 2]
    rng.shuffle(keys)
    items: list[dict] = []
    for ind, period in keys:
        if len(items) >= target:
            break
        countries = list(idx_ip[(ind, period)].items())
        rng.shuffle(countries)
        (c1, r1), (c2, r2) = countries[0], countries[1]
        if r1["value"] == r2["value"]:
            continue
        winner = c1 if r1["value"] > r2["value"] else c2
        items.append({
            "question_type": "comparison",
            "question": (
                f"Which had a higher {_indicator_name(ind)} in {period}, "
                f"{country_name(c1)} or {country_name(c2)}?"
            ),
            "answer_kind": "categorical",
            "expected_answer": country_name(winner),
            "expected_country": winner,
            "indicator_code": ind,
            "indicator_name": _indicator_name(ind),
            "countries": [c1, c2],
            "period": period,
            "expected_source": r1["source_name"],
            "supporting_values": {c1: r1["value"], c2: r2["value"]},
            "gold_record_ids": [r1["record_id"], r2["record_id"]],
        })
    return items


def build_superlatives(idx_ip: dict, rng, target: int, highest: bool) -> list[dict]:
    keys = [k for k, v in idx_ip.items() if len(v) >= 3]
    rng.shuffle(keys)
    qtype = "superlative_high" if highest else "superlative_low"
    word = "highest" if highest else "lowest"
    items: list[dict] = []
    for ind, period in keys:
        if len(items) >= target:
            break
        recs = idx_ip[(ind, period)]
        extreme_cc = (max if highest else min)(recs, key=lambda c: recs[c]["value"])
        extreme = recs[extreme_cc]
        items.append({
            "question_type": qtype,
            "question": (
                f"Among the tracked countries, which had the {word} "
                f"{_indicator_name(ind)} in {period}?"
            ),
            "answer_kind": "categorical",
            "expected_answer": country_name(extreme_cc),
            "expected_country": extreme_cc,
            "indicator_code": ind,
            "indicator_name": _indicator_name(ind),
            "countries": sorted(recs),
            "period": period,
            "expected_source": extreme["source_name"],
            "supporting_values": {c: recs[c]["value"] for c in recs},
            "gold_record_ids": [extreme["record_id"]],
        })
    return items


def build_trends(idx_ic: dict, rng, target: int, gap: int) -> list[dict]:
    keys = list(idx_ic.keys())
    rng.shuffle(keys)
    items: list[dict] = []
    for ind, country in keys:
        if len(items) >= target:
            break
        periods = sorted(idx_ic[(ind, country)])
        years = [int(p) for p in periods]
        pair = _find_year_pair(years, gap)
        if not pair:
            continue
        y1, y2 = str(pair[0]), str(pair[1])
        r1, r2 = idx_ic[(ind, country)][y1], idx_ic[(ind, country)][y2]
        if r1["value"] == r2["value"]:
            continue
        direction = "increased" if r2["value"] > r1["value"] else "decreased"
        items.append({
            "question_type": "trend",
            "question": (
                f"Did the {_indicator_name(ind)} of {country_name(country)} "
                f"increase or decrease between {y1} and {y2}?"
            ),
            "answer_kind": "direction",
            "expected_answer": direction,
            "indicator_code": ind,
            "indicator_name": _indicator_name(ind),
            "country_code": country,
            "periods": [y1, y2],
            "expected_source": r2["source_name"],
            "supporting_values": {y1: r1["value"], y2: r2["value"]},
            "gold_record_ids": [r1["record_id"], r2["record_id"]],
        })
    return items


def _find_year_pair(years: list[int], gap: int) -> Optional[tuple[int, int]]:
    """Pick two present years roughly `gap` apart (closest available)."""
    years = sorted(years)
    for y in years:
        target = y + gap
        if target in years:
            return (y, target)
    if len(years) >= 2:
        return (years[0], years[-1])
    return None


def generate(
    seed: int, min_year: int, max_year: int, targets: dict[str, int]
) -> dict[str, Any]:
    import random

    rng = random.Random(seed)
    db = SessionLocal()
    try:
        auth = load_authoritative(db, min_year, max_year)
    finally:
        db.close()

    idx_ip = _by_indicator_period(auth)
    idx_ic = _by_indicator_country(auth)

    items: list[dict] = []
    items += build_point_lookups(auth, rng, targets["point_lookup"])
    items += build_comparisons(idx_ip, rng, targets["comparison"])
    items += build_superlatives(idx_ip, rng, targets["superlative_high"], True)
    items += build_superlatives(idx_ip, rng, targets["superlative_low"], False)
    items += build_trends(idx_ic, rng, targets["trend"], TREND_GAP_YEARS)

    for i, item in enumerate(items, start=1):
        item["id"] = f"gt-{i:05d}"
    # Move id to the front for readability.
    items = [{"id": it.pop("id"), **it} for it in items]

    counts: dict[str, int] = defaultdict(int)
    for it in items:
        counts[it["question_type"]] += 1

    return {
        "metadata": {
            "description": (
                "Ground-truth answer key for the Macro Intelligence Platform "
                "RAG chatbot. Answers are derived from the verified gold data "
                "layer (gold_records)."
            ),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source_layer": "gold_records",
            "seed": seed,
            "min_year": min_year,
            "max_year": max_year,
            "authoritative_keys": len(auth),
            "counts": dict(counts),
            "total": len(items),
        },
        "items": items,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--min-year", type=int, default=2000)
    parser.add_argument("--max-year", type=int, default=2024)
    parser.add_argument("--out", default="eval/ground_truth.json")
    args = parser.parse_args()

    dataset = generate(args.seed, args.min_year, args.max_year, DEFAULT_TARGETS)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(dataset, f, indent=2, ensure_ascii=False)

    meta = dataset["metadata"]
    print(f"Wrote {meta['total']} ground-truth items to {args.out}")
    print(f"  authoritative keys: {meta['authoritative_keys']}")
    for qtype, n in sorted(meta["counts"].items()):
        print(f"  {qtype:18s} {n}")


if __name__ == "__main__":
    main()
