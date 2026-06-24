"""Pillar 6 — Explainability: Human-readable anomaly explanations for flagged values.
Satisfies MiFID II research transparency."""

import statistics
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from src.database import GoldRecord, get_db

logger = structlog.get_logger().bind(pillar="explainability")


# ── Enums & Dataclasses ───────────────────────────────────────────────────────

class AnomalyType(str, Enum):
    OUT_OF_RANGE = "OUT_OF_RANGE"
    SOURCE_CONFLICT = "SOURCE_CONFLICT"
    REVISION = "REVISION"


@dataclass
class AnomalyExplanation:
    indicator_code: str
    anomaly_type: AnomalyType
    explanation: str
    severity: str  # "LOW" | "MEDIUM" | "HIGH"


# ── AnomalyExplainer ──────────────────────────────────────────────────────────

class AnomalyExplainer:
    def __init__(self, db: Session) -> None:
        self._db = db

    def explain_out_of_range(
        self,
        indicator_code: str,
        value: float,
        unit: str,
        country_code: str,
        period: str,
    ) -> AnomalyExplanation:
        historical = (
            self._db.query(GoldRecord)
            .filter(
                GoldRecord.indicator_code == indicator_code,
                GoldRecord.country_code == country_code,
                GoldRecord.period != period,
            )
            .order_by(GoldRecord.promoted_at.desc())
            .limit(10)
            .all()
        )

        values = [r.value for r in historical if r.value is not None]

        if len(values) >= 2:
            mean = statistics.mean(values)
            std = statistics.stdev(values)
            min_val = min(values)
            max_val = max(values)
        elif len(values) == 1:
            mean = values[0]
            std = 0.0
            min_val = values[0]
            max_val = values[0]
        else:
            mean = value
            std = 0.0
            min_val = value
            max_val = value

        n_stdevs = abs(value - mean) / std if std > 0 else 0.0
        explanation = (
            f"This value ({value} {unit}) is {n_stdevs:.1f} standard deviations from the "
            f"historical mean ({mean:.2f} {unit}). Typical range: {min_val:.2f}–{max_val:.2f}."
        )
        severity = "HIGH" if n_stdevs > 3 else "MEDIUM" if n_stdevs > 2 else "LOW"

        return AnomalyExplanation(
            indicator_code=indicator_code,
            anomaly_type=AnomalyType.OUT_OF_RANGE,
            explanation=explanation,
            severity=severity,
        )

    def explain_source_conflict(
        self,
        indicator_code: str,
        val_a: float,
        source_a: str,
        reliability_a: float,
        val_b: float,
        source_b: str,
    ) -> AnomalyExplanation:
        denom = (val_a + val_b) / 2
        variance_pct = abs(val_a - val_b) / denom * 100 if denom != 0 else 0.0
        explanation = (
            f"Source '{source_a}' reports {val_a}; Source '{source_b}' reports {val_b}. "
            f"Variance: {variance_pct:.1f}%. Using '{source_a}' "
            f"(reliability score: {reliability_a:.0f}/100)."
        )
        severity = "HIGH" if variance_pct > 5 else "MEDIUM" if variance_pct > 1 else "LOW"

        return AnomalyExplanation(
            indicator_code=indicator_code,
            anomaly_type=AnomalyType.SOURCE_CONFLICT,
            explanation=explanation,
            severity=severity,
        )

    def explain_revision(
        self,
        indicator_code: str,
        old_val: float,
        new_val: float,
        revision_date: datetime,
    ) -> AnomalyExplanation:
        delta = new_val - old_val
        pct_change = (delta / old_val * 100) if old_val != 0 else 0.0
        explanation = (
            f"This indicator was revised on {revision_date.strftime('%Y-%m-%d')}. "
            f"Original: {old_val}. Revised: {new_val}. "
            f"Change: {delta:+.3f} ({pct_change:+.1f}%)."
        )
        severity = (
            "HIGH" if abs(pct_change) > 10 else "MEDIUM" if abs(pct_change) > 5 else "LOW"
        )

        return AnomalyExplanation(
            indicator_code=indicator_code,
            anomaly_type=AnomalyType.REVISION,
            explanation=explanation,
            severity=severity,
        )

    def explain(
        self,
        indicator_id: str,
        country_code: Optional[str] = None,
        period: Optional[str] = None,
    ) -> Optional[AnomalyExplanation]:
        query = self._db.query(GoldRecord).filter(
            GoldRecord.indicator_code == indicator_id
        )
        if country_code:
            query = query.filter(GoldRecord.country_code == country_code)
        if period:
            query = query.filter(GoldRecord.period == period)

        record = query.order_by(GoldRecord.promoted_at.desc()).first()
        if record is None:
            return None

        if record.revision_flag and record.revision_delta is not None:
            old_val = record.value - record.revision_delta
            return self.explain_revision(
                indicator_code=indicator_id,
                old_val=old_val,
                new_val=record.value,
                revision_date=record.promoted_at or datetime.utcnow(),
            )

        return self.explain_out_of_range(
            indicator_code=indicator_id,
            value=record.value,
            unit=record.standard_unit or "",
            country_code=record.country_code,
            period=record.period,
        )


# ── FastAPI Router ────────────────────────────────────────────────────────────

router = APIRouter(prefix="/api/explainability", tags=["explainability"])


@router.get("/anomaly/{indicator_id}")
def get_anomaly_explanation(
    indicator_id: str,
    country: Optional[str] = Query(default=None),
    period: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
) -> dict:
    explainer = AnomalyExplainer(db)
    result = explainer.explain(
        indicator_id=indicator_id,
        country_code=country,
        period=period,
    )
    if result is None:
        return {"detail": "No anomaly data found for this indicator."}
    return {
        "indicator_code": result.indicator_code,
        "anomaly_type": result.anomaly_type,
        "explanation": result.explanation,
        "severity": result.severity,
    }
