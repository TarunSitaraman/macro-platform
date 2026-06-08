"""Pipeline management endpoints — trigger ingestion, check status."""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.agents.pipeline import Pipeline
from src.agents.static import FREDAgent, IMFAgent, WorldBankAgent
from src.agents.crawler import DynamicCrawlerAgent
from src.config import INDICATOR_CATALOGUE, get_settings
from src.database import SourceConfig, get_db

router = APIRouter()
logger = logging.getLogger(__name__)
settings = get_settings()


class IngestResult(BaseModel):
    source_code: str
    records_fetched: int
    records_promoted: int
    records_queued: int
    records_rejected: int
    started_at: str
    completed_at: str


async def _run_static_source(source_code: str, db: Session) -> IngestResult:
    started = datetime.now(timezone.utc)

    if source_code == "WORLD_BANK":
        raw_records = await WorldBankAgent().run_all()
    elif source_code == "IMF_WEO":
        raw_records = await IMFAgent().run_all()
    elif source_code == "FRED":
        raw_records = await FREDAgent().run_all()
    else:
        raise ValueError(f"Unknown static source: {source_code}")

    source = db.query(SourceConfig).filter(SourceConfig.source_code == source_code).first()
    source_name = source.source_name if source else source_code
    source_url = source.source_url if source else ""

    pipeline = Pipeline(db)
    promoted = queued = rejected = 0

    for rec in raw_records:
        ind_code = rec["indicator_code"]
        unit = INDICATOR_CATALOGUE.get(ind_code, {}).get("standard_unit", "")
        try:
            result = await pipeline.run(
                source_code=source_code,
                indicator_code=ind_code,
                country_code=rec["country_code"],
                period=rec["period"],
                raw_value=rec["raw_value"],
                raw_unit=rec.get("raw_unit", unit),
                source_url=rec.get("source_url", source_url),
                extraction_method="API",
                raw_json=rec.get("raw_json", {}),
                standard_unit=unit,
                source_name=source_name,
            )
            if result["status"] == "promoted":
                promoted += 1
            elif result["status"] == "review":
                queued += 1
            else:
                rejected += 1
        except Exception as exc:
            logger.error("Pipeline error for %s: %s", rec, exc)
            rejected += 1

    # Update source last_run_at
    if source:
        source.last_run_at = datetime.now(timezone.utc)
        source.error_message = None
        db.commit()

    return IngestResult(
        source_code=source_code,
        records_fetched=len(raw_records),
        records_promoted=promoted,
        records_queued=queued,
        records_rejected=rejected,
        started_at=started.isoformat(),
        completed_at=datetime.now(timezone.utc).isoformat(),
    )


@router.post("/pipelines/static/{source_code}/run")
async def run_static_pipeline(
    source_code: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    valid_sources = {"WORLD_BANK", "IMF_WEO", "FRED"}
    if source_code not in valid_sources:
        raise HTTPException(status_code=400, detail=f"Valid sources: {valid_sources}")

    result = await _run_static_source(source_code, db)
    return result


@router.post("/pipelines/dynamic/{source_code}/run")
async def run_dynamic_pipeline(
    source_code: str,
    db: Session = Depends(get_db),
):
    source = db.query(SourceConfig).filter(SourceConfig.source_code == source_code).first()
    if not source or source.source_type not in ("HTML", "PDF"):
        raise HTTPException(status_code=400, detail="Source not found or not a crawlable source")

    started = datetime.now(timezone.utc)
    crawler = DynamicCrawlerAgent()
    raw_records = await crawler.crawl_and_extract(
        url=source.source_url,
        extraction_prompt=source.extraction_prompt,
    )

    pipeline = Pipeline(db)
    promoted = queued = rejected = 0

    for rec in raw_records:
        ind_code = rec.get("indicator_code", "")
        if ind_code not in INDICATOR_CATALOGUE:
            rejected += 1
            continue
        unit = INDICATOR_CATALOGUE[ind_code]["standard_unit"]
        try:
            result = await pipeline.run(
                source_code=source_code,
                indicator_code=ind_code,
                country_code=rec.get("country_code", ""),
                period=rec.get("period", ""),
                raw_value=str(rec.get("raw_value", "")),
                raw_unit=rec.get("raw_unit", unit),
                source_url=source.source_url,
                extraction_method="HTML_LLM",
                raw_json=rec,
                standard_unit=unit,
                source_name=source.source_name,
            )
            if result["status"] == "promoted":
                promoted += 1
            elif result["status"] == "review":
                queued += 1
            else:
                rejected += 1
        except Exception as exc:
            logger.error("Crawler pipeline error: %s", exc)
            rejected += 1

    source.last_run_at = datetime.now(timezone.utc)
    db.commit()

    return {
        "source_code": source_code,
        "records_fetched": len(raw_records),
        "records_promoted": promoted,
        "records_queued": queued,
        "records_rejected": rejected,
        "started_at": started.isoformat(),
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
