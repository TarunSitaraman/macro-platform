"""Data lineage explain tool — traces gold record provenance."""

from uuid import UUID

from sqlalchemy.orm import Session

from src.agents.runtime.types import ToolResult
from src.utils.dq_explain import build_trust_explanation
from src.database import (
    BronzeRecord,
    DataLineage,
    GoldRecord,
    ReviewQueue,
    SilverRecord,
)


def _tenant_ok(record_tenant_id, request_tenant_id: UUID) -> bool:
    return record_tenant_id is None or record_tenant_id == request_tenant_id


async def explain_data_lineage(
    db: Session,
    tenant_id: UUID,
    gold_record_id: str,
) -> ToolResult:
    """Trace a gold record back through silver, bronze, and review history."""
    gold = db.query(GoldRecord).filter(GoldRecord.record_id == gold_record_id).first()
    if not gold or not _tenant_ok(gold.tenant_id, tenant_id):
        return ToolResult(
            tool_name="explain_data_lineage",
            success=False,
            data=None,
            error="Gold record not found or access denied",
        )

    lineage_chain: list[dict] = []
    silver = None
    bronze = None
    review = None

    if gold.silver_id:
        silver = db.query(SilverRecord).filter(SilverRecord.record_id == gold.silver_id).first()
        if silver:
            review = (
                db.query(ReviewQueue)
                .filter(ReviewQueue.silver_id == silver.record_id)
                .first()
            )
            if silver.bronze_id:
                bronze = db.query(BronzeRecord).filter(
                    BronzeRecord.record_id == silver.bronze_id
                ).first()

    lineages = (
        db.query(DataLineage)
        .filter(
            (DataLineage.target_record_id == gold.record_id)
            | (DataLineage.source_record_id == gold.record_id),
            (DataLineage.tenant_id == None) | (DataLineage.tenant_id == tenant_id),
        )
        .all()
    )
    for ln in lineages:
        lineage_chain.append({
            "lineage_id": str(ln.lineage_id),
            "source_record_id": str(ln.source_record_id),
            "target_record_id": str(ln.target_record_id),
            "transformation": ln.transformation,
            "status": str(ln.status),
        })

    data = {
        "gold": {
            "record_id": str(gold.record_id),
            "indicator_code": gold.indicator_code,
            "country_code": gold.country_code,
            "period": gold.period,
            "value": gold.value,
            "standard_unit": gold.standard_unit,
            "source_name": gold.source_name,
            "dq_score": gold.dq_score,
            "approved_by": gold.approved_by,
            "promoted_at": gold.promoted_at.isoformat() if gold.promoted_at else None,
        },
        "silver": {
            "record_id": str(silver.record_id),
            "dq_score": silver.dq_score,
            "dq_status": str(silver.dq_status) if silver else None,
            "dq_breakdown": silver.dq_breakdown,
            "failure_reasons": silver.failure_reasons,
        } if silver else None,
        "bronze": {
            "record_id": str(bronze.record_id),
            "source_code": bronze.source_code,
            "extraction_method": str(bronze.extraction_method),
            "source_url": bronze.source_url,
            "crawled_at": bronze.crawled_at.isoformat() if bronze.crawled_at else None,
        } if bronze else None,
        "review": {
            "status": str(review.status),
            "reviewed_by": review.reviewed_by,
            "review_notes": review.review_notes,
            "failure_reasons": review.failure_reasons,
        } if review else None,
        "lineage_entries": lineage_chain,
        "trust": build_trust_explanation(
            dq_score=gold.dq_score or (silver.dq_score if silver else None),
            dq_breakdown=silver.dq_breakdown if silver else None,
            failure_reasons=(
                list(silver.failure_reasons or [])
                if silver
                else list(review.failure_reasons or []) if review else []
            ),
            dq_status=str(silver.dq_status) if silver and silver.dq_status else None,
            approved_by=gold.approved_by,
            review_status=str(review.status) if review else None,
            reviewed_by=review.reviewed_by if review else None,
            review_notes=review.review_notes if review else None,
            source_name=gold.source_name,
            extraction_method=str(bronze.extraction_method) if bronze else None,
        ),
    }

    return ToolResult(
        tool_name="explain_data_lineage",
        success=True,
        data=data,
        record_ids=[str(gold.record_id)],
        records=[{
            "record_id": str(gold.record_id),
            "type": "gold",
            "source_name": gold.source_name,
            "source_url": gold.source_url,
            "indicator_code": gold.indicator_code,
            "country_code": gold.country_code,
            "period": gold.period,
            "value": gold.value,
            "unit": gold.standard_unit,
            "dq_score": gold.dq_score,
        }],
    )


def build_lineage_explain_response(db: Session, tenant_id: UUID, gold_record_id: str) -> dict:
    """Synchronous helper for the REST explain endpoint."""
    import asyncio

    result = asyncio.run(explain_data_lineage(db, tenant_id, gold_record_id))
    if not result.success:
        return {"error": result.error}
    return result.data or {}
