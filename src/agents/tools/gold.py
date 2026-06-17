"""Gold-layer data tools — search, timeseries, compare."""

import logging
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from src.agents.embeddings import EmbeddingError, embed_text
from src.agents.runtime.types import ToolResult
from src.database import GoldRecord

logger = logging.getLogger(__name__)

G7_COUNTRIES = {"USA", "CAN", "GBR", "DEU", "FRA", "ITA", "JPN"}


def _tenant_filter(tenant_id: UUID):
    return (GoldRecord.tenant_id == None) | (GoldRecord.tenant_id == tenant_id)


def _record_to_dict(r: GoldRecord) -> dict[str, Any]:
    return {
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
    }


async def search_gold_records(
    db: Session,
    tenant_id: UUID,
    query: str,
    limit: int = 6,
) -> ToolResult:
    """Vector similarity search over gold records."""
    try:
        query_embedding = await embed_text(query)
        embedding_str = "[" + ",".join(str(x) for x in query_embedding) + "]"

        raw = db.execute(
            text(
                "SELECT record_id FROM gold_records "
                "WHERE embedding IS NOT NULL "
                "AND (tenant_id IS NULL OR tenant_id = :tenant_id) "
                "ORDER BY embedding <-> CAST(:emb AS vector) "
                "LIMIT :lim"
            ),
            {"emb": embedding_str, "lim": limit, "tenant_id": tenant_id},
        ).fetchall()
        ids = [row[0] for row in raw]
    except EmbeddingError as exc:
        return ToolResult(
            tool_name="search_gold_records",
            success=False,
            data=None,
            error=f"Embedding unavailable: {exc}",
        )
    except Exception as exc:
        logger.warning("Vector search failed: %s", exc)
        db.rollback()
        records = (
            db.query(GoldRecord)
            .filter(_tenant_filter(tenant_id))
            .order_by(GoldRecord.promoted_at.desc())
            .limit(limit)
            .all()
        )
        data = [_record_to_dict(r) for r in records]
        return ToolResult(
            tool_name="search_gold_records",
            success=True,
            data={"records": data, "fallback": True},
            record_ids=[str(r.record_id) for r in records],
        )

    if not ids:
        records = (
            db.query(GoldRecord)
            .filter(_tenant_filter(tenant_id))
            .order_by(GoldRecord.promoted_at.desc())
            .limit(limit)
            .all()
        )
    else:
        records = db.query(GoldRecord).filter(GoldRecord.record_id.in_(ids)).all()

    data = [_record_to_dict(r) for r in records]
    return ToolResult(
        tool_name="search_gold_records",
        success=True,
        data={"records": data},
        record_ids=[str(r.record_id) for r in records],
    )


async def get_indicator_timeseries(
    db: Session,
    tenant_id: UUID,
    indicator_code: str,
    country_code: str,
    year_from: Optional[int] = None,
    year_to: Optional[int] = None,
    limit: int = 50,
) -> ToolResult:
    """Fetch time series for an indicator and country."""
    q = (
        db.query(GoldRecord)
        .filter(
            _tenant_filter(tenant_id),
            GoldRecord.indicator_code == indicator_code.upper(),
            GoldRecord.country_code == country_code.upper(),
            GoldRecord.is_forecast == False,
        )
    )
    if year_from:
        q = q.filter(GoldRecord.period >= str(year_from))
    if year_to:
        q = q.filter(GoldRecord.period < str(year_to + 1))

    records = q.order_by(GoldRecord.period.asc()).limit(limit).all()
    data = [_record_to_dict(r) for r in records]
    return ToolResult(
        tool_name="get_indicator_timeseries",
        success=True,
        data={"records": data, "count": len(data)},
        record_ids=[str(r.record_id) for r in records],
    )


async def compare_countries(
    db: Session,
    tenant_id: UUID,
    indicator_code: str,
    countries: Optional[list[str]] = None,
    period: Optional[str] = None,
    g7: bool = False,
) -> ToolResult:
    """Compare an indicator across multiple countries."""
    country_list = list(G7_COUNTRIES) if g7 else (countries or [])
    if not country_list:
        return ToolResult(
            tool_name="compare_countries",
            success=False,
            data=None,
            error="Provide countries list or set g7=true",
        )

    q = (
        db.query(GoldRecord)
        .filter(
            _tenant_filter(tenant_id),
            GoldRecord.indicator_code == indicator_code.upper(),
            GoldRecord.country_code.in_([c.upper() for c in country_list]),
            GoldRecord.is_forecast == False,
        )
    )
    if period:
        records = q.filter(GoldRecord.period == period).all()
    else:
        all_records = q.order_by(GoldRecord.period.desc()).all()
        seen: set[str] = set()
        records = []
        for r in all_records:
            if r.country_code not in seen:
                seen.add(r.country_code)
                records.append(r)

    data = [_record_to_dict(r) for r in records]
    return ToolResult(
        tool_name="compare_countries",
        success=True,
        data={"comparisons": data},
        record_ids=[str(r.record_id) for r in records],
    )
