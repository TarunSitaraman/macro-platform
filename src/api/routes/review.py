"""Review queue endpoints — human-in-the-loop approval workflow."""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.agents.pipeline import Pipeline
from src.database import GoldRecord, ReviewQueue, SilverRecord, SourceConfig, User, get_db
from src.utils.auth import get_current_user, check_role

router = APIRouter()


class ApproveRequest(BaseModel):
    adjusted_value: Optional[float] = None
    review_notes: Optional[str] = None


class RejectRequest(BaseModel):
    review_notes: str


@router.get("/review-queue")
def list_review_queue(
    status: str = "PENDING",
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user: User = Depends(check_role(["admin", "analyst"]))
):
    rows = (
        db.query(ReviewQueue)
        .filter(ReviewQueue.status == status)
        .filter(ReviewQueue.tenant_id == current_user.tenant_id)
        .order_by(ReviewQueue.sla_deadline)
        .limit(limit)
        .all()
    )
    return [
        {
            "queue_id": str(r.queue_id),
            "indicator_code": r.indicator_code,
            "country_code": r.country_code,
            "period": r.period,
            "extracted_value": r.extracted_value,
            "dq_score": r.dq_score,
            "dq_breakdown": r.dq_breakdown,
            "failure_reasons": r.failure_reasons,
            "source_url": r.source_url,
            "status": r.status,
            "created_at": r.created_at.isoformat(),
            "sla_deadline": r.sla_deadline.isoformat(),
            "sla_breached": r.sla_deadline < datetime.now(timezone.utc),
        }
        for r in rows
    ]


@router.post("/review-queue/{queue_id}/approve")
async def approve_queue_item(
    queue_id: str,
    body: ApproveRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(check_role(["admin", "analyst"]))
):
    item = db.query(ReviewQueue).filter(
        ReviewQueue.queue_id == queue_id,
        ReviewQueue.tenant_id == current_user.tenant_id
    ).first()
    if not item:
        raise HTTPException(status_code=404, detail="Queue item not found")
    if item.status != "PENDING":
        raise HTTPException(status_code=400, detail=f"Item already in status: {item.status}")

    silver = db.query(SilverRecord).filter(SilverRecord.record_id == item.silver_id).first()
    if not silver:
        raise HTTPException(status_code=404, detail="Silver record missing")

    # Apply analyst adjustment if provided
    if body.adjusted_value is not None:
        silver.value = body.adjusted_value
        item.adjusted_value = body.adjusted_value
        item.status = "ADJUSTED"
    else:
        item.status = "APPROVED"

    item.reviewed_by = current_user.email
    item.reviewed_at = datetime.now(timezone.utc)
    item.review_notes = body.review_notes
    db.flush()

    # Promote to gold
    source = db.query(SourceConfig).filter(SourceConfig.source_code == silver.source_code).first()
    pipeline = Pipeline(db, tenant_id=current_user.tenant_id)
    gold = await pipeline.promote_to_gold(
        silver=silver,
        source_name=source.source_name if source else silver.source_code,
        source_url=item.source_url or "",
        crawled_at=silver.processed_at or datetime.now(timezone.utc),
        approved_by=current_user.email,
    )
    db.commit()

    return {
        "queue_id": queue_id,
        "status": item.status,
        "gold_id": str(gold.record_id),
        "reviewed_by": current_user.email,
    }


@router.post("/review-queue/{queue_id}/reject")
def reject_queue_item(
    queue_id: str,
    body: RejectRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(check_role(["admin", "analyst"]))
):
    item = db.query(ReviewQueue).filter(
        ReviewQueue.queue_id == queue_id,
        ReviewQueue.tenant_id == current_user.tenant_id
    ).first()
    if not item:
        raise HTTPException(status_code=404, detail="Queue item not found")
    if item.status != "PENDING":
        raise HTTPException(status_code=400, detail=f"Item already in status: {item.status}")

    item.status = "REJECTED"
    item.reviewed_by = current_user.email
    item.reviewed_at = datetime.now(timezone.utc)
    item.review_notes = body.review_notes
    db.commit()

    return {"queue_id": queue_id, "status": "REJECTED"}
