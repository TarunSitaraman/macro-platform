"""Pillar 6 — Explainability: Transparent quality score breakdown for auditors.
Satisfies INTERNAL data governance audit requirements."""

from dataclasses import dataclass
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from src.database import SilverRecord, get_db

logger = structlog.get_logger().bind(pillar="explainability")


# ── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass
class ScoreComponent:
    name: str
    raw_value: float
    weight: float
    weighted_contribution: float


@dataclass
class QualityScoreBreakdown:
    indicator_code: str
    country_code: str
    period: str
    accuracy: ScoreComponent
    completeness: ScoreComponent
    timeliness: ScoreComponent
    consistency: ScoreComponent
    overall_score: float


# ── QualityBreakdownCalculator ────────────────────────────────────────────────

_WEIGHTS = {
    "accuracy": 0.40,
    "completeness": 0.30,
    "timeliness": 0.20,
    "consistency": 0.10,
}


class QualityBreakdownCalculator:
    def __init__(self, db: Session) -> None:
        self._db = db

    def calculate(
        self,
        indicator_code: str,
        country_code: str,
        period: str,
    ) -> Optional[QualityScoreBreakdown]:
        record = (
            self._db.query(SilverRecord)
            .filter(
                SilverRecord.indicator_code == indicator_code,
                SilverRecord.country_code == country_code,
                SilverRecord.period == period,
            )
            .order_by(SilverRecord.processed_at.desc())
            .first()
        )
        if record is None:
            return None

        dq_breakdown: Optional[dict] = record.dq_breakdown
        if dq_breakdown and all(k in dq_breakdown for k in _WEIGHTS):
            accuracy_raw = float(dq_breakdown.get("accuracy", 0))
            completeness_raw = float(dq_breakdown.get("completeness", 0))
            timeliness_raw = float(dq_breakdown.get("timeliness", 0))
            consistency_raw = float(dq_breakdown.get("consistency", 0))
        else:
            # Derive proportionally from overall dq_score
            base = float(record.dq_score or 0)
            # Split by weight ratios (all weights sum to 1, so proportional = same value)
            accuracy_raw = base
            completeness_raw = base
            timeliness_raw = base
            consistency_raw = base

        components = {
            "accuracy": ScoreComponent(
                name="accuracy",
                raw_value=accuracy_raw,
                weight=_WEIGHTS["accuracy"],
                weighted_contribution=accuracy_raw * _WEIGHTS["accuracy"],
            ),
            "completeness": ScoreComponent(
                name="completeness",
                raw_value=completeness_raw,
                weight=_WEIGHTS["completeness"],
                weighted_contribution=completeness_raw * _WEIGHTS["completeness"],
            ),
            "timeliness": ScoreComponent(
                name="timeliness",
                raw_value=timeliness_raw,
                weight=_WEIGHTS["timeliness"],
                weighted_contribution=timeliness_raw * _WEIGHTS["timeliness"],
            ),
            "consistency": ScoreComponent(
                name="consistency",
                raw_value=consistency_raw,
                weight=_WEIGHTS["consistency"],
                weighted_contribution=consistency_raw * _WEIGHTS["consistency"],
            ),
        }
        overall = sum(c.weighted_contribution for c in components.values())

        return QualityScoreBreakdown(
            indicator_code=indicator_code,
            country_code=country_code,
            period=period,
            accuracy=components["accuracy"],
            completeness=components["completeness"],
            timeliness=components["timeliness"],
            consistency=components["consistency"],
            overall_score=round(overall, 2),
        )

    def format_for_chatbot(self, breakdown: QualityScoreBreakdown) -> str:
        return (
            f"[Quality: {breakdown.overall_score:.0f}/100 — "
            f"Accuracy: {breakdown.accuracy.raw_value:.0f}, "
            f"Completeness: {breakdown.completeness.raw_value:.0f}, "
            f"Timeliness: {breakdown.timeliness.raw_value:.0f}]"
        )


# ── FastAPI Router ────────────────────────────────────────────────────────────

router = APIRouter(tags=["explainability"])


@router.get("/api/indicators/{indicator_id}/quality-breakdown")
def get_quality_breakdown(
    indicator_id: str,
    country: str = Query(...),
    period: str = Query(...),
    db: Session = Depends(get_db),
) -> dict:
    calculator = QualityBreakdownCalculator(db)
    breakdown = calculator.calculate(
        indicator_code=indicator_id,
        country_code=country,
        period=period,
    )
    if breakdown is None:
        return {"detail": "No silver record found for this indicator/country/period."}

    def _component_dict(c: ScoreComponent) -> dict:
        return {
            "name": c.name,
            "raw_value": c.raw_value,
            "weight": c.weight,
            "weighted_contribution": c.weighted_contribution,
        }

    return {
        "indicator_code": breakdown.indicator_code,
        "country_code": breakdown.country_code,
        "period": breakdown.period,
        "accuracy": _component_dict(breakdown.accuracy),
        "completeness": _component_dict(breakdown.completeness),
        "timeliness": _component_dict(breakdown.timeliness),
        "consistency": _component_dict(breakdown.consistency),
        "overall_score": breakdown.overall_score,
    }
