"""Score the RAG system against the ground-truth answer key.

Two complementary metrics:

  retrieval  Does vector search surface the gold record that holds the answer?
             (top-k hit / recall@k). Cheap — embeddings only, no LLM calls.

  answer     Does the full chatbot produce the correct answer and cite the right
             source? Runs the real AgentOrchestrator — this DOES call the LLM.

Eval runs are non-destructive: all DB writes (agent run audit rows) happen in a
single transaction that is rolled back at the end.

Usage (from repo root):

    python -m eval.score_rag                              # retrieval, all items
    python -m eval.score_rag --mode retrieval --k 6
    python -m eval.score_rag --mode both --limit 25       # includes LLM answers
    python -m eval.score_rag --mode answer --limit 25 --report eval/report.json
"""

import argparse
import asyncio
import json
from typing import Any, Optional
from uuid import UUID

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import text  # noqa: E402

from src.agents.runtime.orchestrator import AgentOrchestrator  # noqa: E402
from src.agents.tools.chat_registry import build_chat_tool_registry  # noqa: E402
from src.agents.tools.gold import search_gold_records  # noqa: E402
from src.database import SessionLocal  # noqa: E402
from eval.scoring import aggregate, hit_at_k, recall_at_k, score_answer  # noqa: E402


def _load(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)["items"]


def _first_tenant_user(db) -> tuple[UUID, Optional[UUID]]:
    tenant = db.execute(text("SELECT tenant_id FROM tenants LIMIT 1")).first()
    if not tenant:
        raise SystemExit("No tenant found — run db_init.py and register an admin first.")
    user = db.execute(text("SELECT user_id FROM users LIMIT 1")).first()
    return tenant[0], (user[0] if user else None)


async def score_retrieval(items: list[dict], k: int) -> dict[str, Any]:
    db = SessionLocal()
    tenant_id, _ = _first_tenant_user(db)
    hits = 0
    recall_sum = 0.0
    per_type: dict[str, dict[str, float]] = {}
    try:
        for it in items:
            result = await search_gold_records(db, tenant_id, it["question"], limit=k)
            retrieved = [str(r) for r in result.record_ids]
            expected = it["gold_record_ids"]
            hit = hit_at_k(expected, retrieved, k)
            rec = recall_at_k(expected, retrieved, k)
            hits += 1 if hit else 0
            recall_sum += rec
            bucket = per_type.setdefault(it["question_type"], {"total": 0, "hits": 0})
            bucket["total"] += 1
            bucket["hits"] += 1 if hit else 0
    finally:
        db.rollback()
        db.close()

    total = len(items)
    return {
        "metric": f"retrieval@{k}",
        "total": total,
        "hit_rate": round(hits / total, 4) if total else 0.0,
        "mean_recall": round(recall_sum / total, 4) if total else 0.0,
        "by_type": {
            t: {**b, "hit_rate": round(b["hits"] / b["total"], 4)}
            for t, b in sorted(per_type.items())
        },
    }


async def score_answers(items: list[dict], k: int, rel_tol: float, abs_tol: float) -> dict[str, Any]:
    db = SessionLocal()
    tenant_id, user_id = _first_tenant_user(db)
    registry = build_chat_tool_registry()
    results: list[dict] = []
    answer_hits = 0
    try:
        for it in items:
            orchestrator = AgentOrchestrator(
                db, tenant_id, registry, user_id=user_id, agent_name="EvalAgent"
            )
            run = await orchestrator.run(it["question"])
            scored = score_answer(
                it, run.response, run.citations, rel_tol=rel_tol, abs_tol=abs_tol
            )
            scored["retrieval_hit"] = hit_at_k(
                it["gold_record_ids"], run.context_record_ids, k
            )
            scored["confidence"] = run.confidence
            answer_hits += 1 if scored["retrieval_hit"] else 0
            results.append(scored)
    finally:
        db.rollback()
        db.close()

    summary = aggregate(results)
    summary["metric"] = "answer"
    summary["retrieval_hit_rate"] = (
        round(answer_hits / len(results), 4) if results else 0.0
    )
    summary["items"] = results
    return summary


def _print_retrieval(r: dict) -> None:
    print(f"\n== Retrieval ({r['metric']}) over {r['total']} items ==")
    print(f"  hit_rate     : {r['hit_rate']:.1%}")
    print(f"  mean_recall  : {r['mean_recall']:.1%}")
    for t, b in r["by_type"].items():
        print(f"    {t:18s} hit {b['hit_rate']:.1%}  ({b['hits']}/{b['total']})")


def _print_answers(a: dict) -> None:
    print(f"\n== Answer eval over {a['total']} items ==")
    print(f"  accuracy           : {a.get('accuracy', 0):.1%}")
    print(f"  source_cited_rate  : {a.get('source_cited_rate', 0):.1%}")
    print(f"  retrieval_hit_rate : {a.get('retrieval_hit_rate', 0):.1%}")
    for t, b in a.get("by_type", {}).items():
        print(f"    {t:18s} acc {b['accuracy']:.1%}  ({b['correct']}/{b['total']})")


async def _main_async(args) -> dict[str, Any]:
    items = _load(args.input)
    if args.limit and args.limit > 0:
        items = items[: args.limit]

    report: dict[str, Any] = {"input": args.input, "evaluated": len(items)}

    if args.mode in ("retrieval", "both"):
        report["retrieval"] = await score_retrieval(items, args.k)
        _print_retrieval(report["retrieval"])

    if args.mode in ("answer", "both"):
        print(f"\nRunning answer eval on {len(items)} items (this calls the LLM)...")
        report["answer"] = await score_answers(items, args.k, args.rel_tol, args.abs_tol)
        _print_answers(report["answer"])

    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["retrieval", "answer", "both"], default="retrieval")
    parser.add_argument("--input", default="eval/ground_truth.json")
    parser.add_argument("--limit", type=int, default=0, help="0 = all items")
    parser.add_argument("--k", type=int, default=6, help="retrieval depth")
    parser.add_argument("--rel-tol", type=float, default=0.02)
    parser.add_argument("--abs-tol", type=float, default=0.1)
    parser.add_argument("--report", default=None, help="optional path to write JSON report")
    args = parser.parse_args()

    report = asyncio.run(_main_async(args))

    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        print(f"\nReport written to {args.report}")


if __name__ == "__main__":
    main()
