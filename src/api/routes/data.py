"""Data access endpoints — indicators, gold records, sources."""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from fastapi.responses import JSONResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from src.database import (
    GoldRecord, IndicatorDefinition, SourceConfig, User, get_db,
    BronzeRecord, ReviewQueue, AuditLog, SessionLocal
)
from src.utils.auth import get_current_user
from src.agents.forecaster import ForecasterAgent
from src.utils.anomaly_cache import AnomalyCacheManager

router = APIRouter()


@router.get("/indicators")
def list_indicators(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    rows = db.query(IndicatorDefinition).filter(
        IndicatorDefinition.deprecated_at.is_(None),
        (IndicatorDefinition.tenant_id == None) | (IndicatorDefinition.tenant_id == current_user.tenant_id)
    ).all()
    return [
        {
            "indicator_code": r.indicator_code,
            "indicator_name": r.indicator_name,
            "category": r.category,
            "standard_unit": r.standard_unit,
            "description": r.description,
            "frequency": r.frequency,
        }
        for r in rows
    ]


@router.get("/indicators/{code}")
def get_indicator(
    code: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    row = db.query(IndicatorDefinition).filter(
        IndicatorDefinition.indicator_code == code,
        (IndicatorDefinition.tenant_id == None) | (IndicatorDefinition.tenant_id == current_user.tenant_id)
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="Indicator not found")
    return {
        "indicator_code": row.indicator_code,
        "indicator_name": row.indicator_name,
        "category": row.category,
        "standard_unit": row.standard_unit,
        "description": row.description,
        "formula": row.formula,
        "frequency": row.frequency,
        "is_derived": row.is_derived,
        "is_leading": row.is_leading,
    }


@router.get("/gold-data")
def get_gold_data(
    indicator: Optional[str] = Query(None),
    country: Optional[str] = Query(None),
    year_from: Optional[int] = Query(None),
    year_to: Optional[int] = Query(None),
    actuals_only: bool = Query(False),
    limit: int = Query(500, le=5000),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    q = db.query(GoldRecord).filter(
        (GoldRecord.tenant_id == None) | (GoldRecord.tenant_id == current_user.tenant_id)
    )
    if indicator:
        q = q.filter(GoldRecord.indicator_code == indicator)
    if country:
        q = q.filter(GoldRecord.country_code == country)
    if year_from:
        q = q.filter(GoldRecord.period >= str(year_from))
    if year_to:
        q = q.filter(GoldRecord.period < str(year_to + 1))
    if actuals_only:
        q = q.filter(GoldRecord.is_forecast == False)
    rows = q.order_by(GoldRecord.period.asc()).limit(limit).all()
    return [
        {
            "record_id": str(r.record_id),
            "indicator_code": r.indicator_code,
            "country_code": r.country_code,
            "period": r.period,
            "value": r.value,
            "standard_unit": r.standard_unit,
            "is_forecast": r.is_forecast,
            "source_name": r.source_name,
            "source_url": r.source_url,
            "dq_score": r.dq_score,
            "revision_flag": r.revision_flag,
            "promoted_at": r.promoted_at.isoformat() if r.promoted_at else None,
        }
        for r in rows
    ]


@router.get("/gold-data/{record_id}/trust")
def get_gold_trust(
    record_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Explain how and why a gold record's DQ score makes it trustable."""
    from src.agents.tools.lineage import build_lineage_explain_response

    result = build_lineage_explain_response(db, current_user.tenant_id, record_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return {
        "record_id": record_id,
        "gold": result.get("gold"),
        "trust": result.get("trust"),
    }


@router.get("/gold-data/{record_id}/explain")
def explain_gold_record(
    record_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Explain provenance of a gold record through the medallion pipeline."""
    from src.agents.tools.lineage import build_lineage_explain_response

    result = build_lineage_explain_response(db, current_user.tenant_id, record_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.get("/gold-data/{record_id}")
def get_gold_record(
    record_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    row = db.query(GoldRecord).filter(
        GoldRecord.record_id == record_id,
        (GoldRecord.tenant_id == None) | (GoldRecord.tenant_id == current_user.tenant_id)
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="Record not found")
    return {
        "record_id": str(row.record_id),
        "silver_id": str(row.silver_id),
        "indicator_code": row.indicator_code,
        "country_code": row.country_code,
        "period": row.period,
        "value": row.value,
        "standard_unit": row.standard_unit,
        "is_forecast": row.is_forecast,
        "source_name": row.source_name,
        "source_url": row.source_url,
        "source_code": row.source_code,
        "crawled_at": row.crawled_at.isoformat() if row.crawled_at else None,
        "revision_flag": row.revision_flag,
        "revision_delta": row.revision_delta,
        "dq_score": row.dq_score,
        "approved_by": row.approved_by,
        "promoted_at": row.promoted_at.isoformat() if row.promoted_at else None,
    }


@router.get("/sources")
def list_sources(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    rows = db.query(SourceConfig).filter(
        (SourceConfig.tenant_id == None) | (SourceConfig.tenant_id == current_user.tenant_id)
    ).all()
    return [
        {
            "source_code": r.source_code,
            "source_name": r.source_name,
            "source_type": r.source_type,
            "frequency": r.frequency,
            "is_active": r.is_active,
            "last_run_at": r.last_run_at.isoformat() if r.last_run_at else None,
            "reputation_score": r.reputation_score,
        }
        for r in rows
    ]


@router.get("/sources/{code}/status")
def get_source_status(
    code: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    row = db.query(SourceConfig).filter(
        SourceConfig.source_code == code,
        (SourceConfig.tenant_id == None) | (SourceConfig.tenant_id == current_user.tenant_id)
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="Source not found")
    return {
        "source_code": row.source_code,
        "source_name": row.source_name,
        "is_active": row.is_active,
        "last_run_at": row.last_run_at.isoformat() if row.last_run_at else None,
        "last_error_at": row.last_error_at.isoformat() if row.last_error_at else None,
        "error_message": row.error_message,
        "retry_count": row.retry_count,
    }


@router.get("/overview-stats")
def get_overview_stats(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    tenant_id = current_user.tenant_id
    
    n_gold = (
        db.query(func.count(GoldRecord.record_id))
        .filter((GoldRecord.tenant_id == None) | (GoldRecord.tenant_id == tenant_id))
        .scalar() or 0
    )
    n_bronze = (
        db.query(func.count(BronzeRecord.record_id))
        .filter((BronzeRecord.tenant_id == None) | (BronzeRecord.tenant_id == tenant_id))
        .scalar() or 0
    )
    n_pending = (
        db.query(func.count(ReviewQueue.queue_id))
        .filter(ReviewQueue.status == "PENDING")
        .filter(ReviewQueue.tenant_id == tenant_id)
        .scalar() or 0
    )
    n_sources = (
        db.query(func.count(SourceConfig.source_id))
        .filter(SourceConfig.is_active == True)
        .filter((SourceConfig.tenant_id == None) | (SourceConfig.tenant_id == tenant_id))
        .scalar() or 0
    )
    avg_dq = (
        db.query(func.avg(GoldRecord.dq_score))
        .filter((GoldRecord.tenant_id == None) | (GoldRecord.tenant_id == tenant_id))
        .scalar()
    )
    
    return {
        "gold_records": n_gold,
        "total_ingested": n_bronze,
        "pending_review": n_pending,
        "active_sources": n_sources,
        "avg_dq_score": round(float(avg_dq), 1) if avg_dq is not None else 0.0,
    }


# ── Anomaly & Alert endpoints ──

@router.get("/anomalies/alerts")
def get_anomaly_alerts(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Retrieve recent active macro signals/alerts from the pipeline."""
    from sqlalchemy import desc
    recent_alerts = (
        db.query(AuditLog)
        .filter(
            AuditLog.actor == "AlertAgent",
            (AuditLog.tenant_id == None) | (AuditLog.tenant_id == current_user.tenant_id)
        )
        .order_by(desc(AuditLog.timestamp))
        .limit(20)
        .all()
    )
    return [
        {
            "log_id": str(a.log_id),
            "timestamp": a.timestamp.isoformat(),
            "reason": a.reason,
            "type": a.new_values.get("type") if a.new_values else "WARNING",
        }
        for a in recent_alerts
    ]


@router.get("/anomalies/detect")
def detect_macro_anomalies(
    background_tasks: BackgroundTasks,
    force: bool = Query(False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Serve Prophet anomalies from cache, running updates in the background."""
    tenant_id = current_user.tenant_id
    status = AnomalyCacheManager.get_status(tenant_id)
    
    # Decide if background recalculation should run
    should_calculate = force or (not status["is_calculating"] and (status["last_calculated_at"] is None))
    if not should_calculate and not status["is_calculating"]:
        try:
            last_calc = datetime.fromisoformat(status["last_calculated_at"])
            age = (datetime.now(timezone.utc) - last_calc.astimezone(timezone.utc)).total_seconds()
            if age > 1800:  # 30 minutes
                should_calculate = True
        except Exception:
            should_calculate = True

    if should_calculate and not status["is_calculating"]:
        # Always schedule background refresh — never block the request on Prophet.
        background_tasks.add_task(AnomalyCacheManager.calculate_and_cache, SessionLocal, tenant_id)
        is_calculating = True
    else:
        is_calculating = status["is_calculating"]

    anomalies = AnomalyCacheManager.get_anomalies(tenant_id)
    # Fall back to global cache when tenant cache is still empty.
    if not anomalies:
        anomalies = AnomalyCacheManager.get_anomalies(None)
    headers = {
        "X-Is-Calculating": "true" if is_calculating else "false",
        "X-Last-Calculated": AnomalyCacheManager.get_status(tenant_id).get("last_calculated_at") or ""
    }
    return JSONResponse(content=anomalies, headers=headers)
