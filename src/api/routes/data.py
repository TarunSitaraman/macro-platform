"""Data access endpoints — indicators, gold records, sources."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from src.database import GoldRecord, IndicatorDefinition, SourceConfig, get_db

router = APIRouter()


@router.get("/indicators")
def list_indicators(db: Session = Depends(get_db)):
    rows = db.query(IndicatorDefinition).filter(
        IndicatorDefinition.deprecated_at.is_(None)
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
def get_indicator(code: str, db: Session = Depends(get_db)):
    row = db.query(IndicatorDefinition).get(code)
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
    limit: int = Query(500, le=5000),
    db: Session = Depends(get_db),
):
    q = db.query(GoldRecord)
    if indicator:
        q = q.filter(GoldRecord.indicator_code == indicator)
    if country:
        q = q.filter(GoldRecord.country_code == country)
    if year_from:
        q = q.filter(GoldRecord.period >= str(year_from))
    if year_to:
        q = q.filter(GoldRecord.period <= str(year_to))
    rows = q.order_by(GoldRecord.period.desc()).limit(limit).all()
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


@router.get("/gold-data/{record_id}")
def get_gold_record(record_id: str, db: Session = Depends(get_db)):
    row = db.query(GoldRecord).filter(GoldRecord.record_id == record_id).first()
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
def list_sources(db: Session = Depends(get_db)):
    rows = db.query(SourceConfig).all()
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
def get_source_status(code: str, db: Session = Depends(get_db)):
    row = db.query(SourceConfig).filter(SourceConfig.source_code == code).first()
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
