"""Audit log and data lineage endpoints."""

from typing import Optional
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from src.database import AuditLog, DataLineage, get_db, GoldRecord, SilverRecord, BronzeRecord

router = APIRouter()


@router.get("/audit-log")
def get_audit_log(
    table: Optional[str] = Query(None),
    days: int = Query(30, le=365),
    limit: int = Query(200, le=1000),
    db: Session = Depends(get_db),
):
    since = datetime.now(timezone.utc) - timedelta(days=days)
    q = db.query(AuditLog).filter(AuditLog.timestamp >= since)
    if table:
        q = q.filter(AuditLog.table_name == table)
    rows = q.order_by(AuditLog.timestamp.desc()).limit(limit).all()
    return [
        {
            "log_id": str(r.log_id),
            "table_name": r.table_name,
            "record_id": str(r.record_id) if r.record_id else None,
            "action": r.action,
            "actor": r.actor,
            "timestamp": r.timestamp.isoformat(),
            "reason": r.reason,
        }
        for r in rows
    ]


@router.get("/lineage/{record_id}")
def get_lineage(record_id: str, db: Session = Depends(get_db)):
    # Try to resolve records to flat values for UI convenience
    gold_id = record_id
    silver_id = "N/A"
    bronze_id = "N/A"
    value = "N/A"
    standard_unit = ""
    dq_score = None
    source_code = "N/A"

    # Try checking if this is a Gold Record
    gold = db.query(GoldRecord).filter(GoldRecord.record_id == record_id).first()
    if gold:
        value = str(gold.value)
        standard_unit = gold.standard_unit or ""
        source_code = gold.source_code or "N/A"
        if gold.silver_id:
            silver_id = str(gold.silver_id)
            silver = db.query(SilverRecord).filter(SilverRecord.record_id == gold.silver_id).first()
            if silver:
                dq_score = silver.dq_score
                if silver.bronze_id:
                    bronze_id = str(silver.bronze_id)

    # Fallback/complementary: Upstream lineage (what fed into this record)
    upstream = (
        db.query(DataLineage)
        .filter(DataLineage.target_record_id == record_id)
        .all()
    )
    # Downstream lineage (what this record fed into)
    downstream = (
        db.query(DataLineage)
        .filter(DataLineage.source_record_id == record_id)
        .all()
    )

    def _fmt(row):
        return {
            "lineage_id": str(row.lineage_id),
            "source_record_id": str(row.source_record_id),
            "target_record_id": str(row.target_record_id),
            "transformation": row.transformation,
            "status": row.status,
            "started_at": row.started_at.isoformat(),
        }

    return {
        "record_id": record_id,
        "gold_id": gold_id,
        "silver_id": silver_id,
        "bronze_id": bronze_id,
        "value": value,
        "standard_unit": standard_unit,
        "dq_score": dq_score,
        "source_code": source_code,
        "upstream": [_fmt(r) for r in upstream],
        "downstream": [_fmt(r) for r in downstream],
    }
