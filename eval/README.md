# Ground Truth & Evaluation Harness

**Ground truth** is the verified answer key for the platform's AI. In AI, ground
truth is the real-world, verified data used to validate and test a model — the
"answer key" the system is measured against to confirm it reflects reality
instead of guessing.

For this platform, ground truth is derived from the **gold (production) data
layer** (`gold_records`), which is the verified output of the Bronze → Silver →
Gold medallion pipeline. Each entry pairs a natural-language question with:

- the **verified correct answer** (read or computed from gold data), and
- the **specific gold record(s)** that answer must come from.

This lets us measure two things about the RAG chatbot: does it **retrieve** the
right record, and does it **answer** correctly and cite the right source.

## Files

| File | Purpose |
|------|---------|
| `ground_truth.json` | The generated answer key (the dataset). |
| `generate_ground_truth.py` | Builds `ground_truth.json` from the live gold DB. |
| `score_rag.py` | Runs the system against the answer key and reports metrics. |
| `scoring.py` | Pure scoring helpers (unit-tested in `tests/unit/test_eval_scoring.py`). |
| `countries.py` | ISO3 → display-name and unit phrasing maps. |

## Question types

| Type | Example | Answer |
|------|---------|--------|
| `point_lookup` | "What was the CPI Inflation Rate of Germany in 2015?" | numeric value |
| `comparison` | "Which had a higher Government Debt (% GDP) in 2006, Saudi Arabia or Italy?" | country |
| `superlative_high` / `superlative_low` | "Which country had the highest GDP Growth Rate in 2000?" | country |
| `trend` | "Did the Exports (% GDP) of the Netherlands increase or decrease between 2000 and 2005?" | direction |

## Entry schema

```json
{
  "id": "gt-00001",
  "question_type": "point_lookup",
  "question": "What was the CPI Inflation Rate of Germany in 2015?",
  "answer_kind": "numeric",
  "expected_value": 0.51,
  "unit": "PCT",
  "expected_answer": "0.51%",
  "indicator_code": "CPI_INFLATION",
  "indicator_name": "CPI Inflation Rate",
  "country_code": "DEU",
  "period": "2015",
  "expected_source": "World Bank Open Data",
  "gold_record_ids": ["2715c9be-3b83-420f-981f-f39240d7d2ae"]
}
```

Comparison/superlative/trend entries additionally carry `countries`,
`expected_country`, `periods`, and `supporting_values`.

## Regenerate the answer key

Run from the repo root (requires the database to be reachable):

```bash
python -m eval.generate_ground_truth                       # defaults
python -m eval.generate_ground_truth --seed 7 --min-year 2005 --max-year 2024
```

Generation is **reproducible** (seeded) and never invents numbers — answers are
read or computed from gold records. When several gold rows share a key, the one
with the highest `dq_score` (then most recently promoted) is treated as
authoritative. Historical years only (`--max-year`, default 2024) so the key
reflects actuals rather than projections.

## Score the system

```bash
# Retrieval only — cheap, embeddings only, no LLM calls (safe default)
python -m eval.score_rag --mode retrieval --k 6

# Full answer evaluation — runs the real chatbot (calls the LLM)
python -m eval.score_rag --mode both --limit 25 --report eval/report.json
```

Eval runs are **non-destructive**: agent-run audit writes happen inside one
transaction that is rolled back at the end.

### Metrics

- **retrieval** — `hit_rate` (expected gold record in top-k) and `mean_recall`.
- **answer** — `accuracy` (numeric value present / correct entity named),
  `source_cited_rate`, and `retrieval_hit_rate` (did the agent actually use the
  right record), broken down per question type.

Numeric answers are matched within tolerance (`--rel-tol`, `--abs-tol`) so
sensible rounding in the prose still counts as correct.

## Tests

```bash
python -m pytest tests/unit/test_eval_scoring.py -q
```
