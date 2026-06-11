"""Pipeline management endpoints — trigger ingestion, check status."""

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.agents.pipeline import Pipeline
from src.agents.static import FREDAgent, IMFAgent, WorldBankAgent
from src.agents.crawler import DynamicCrawlerAgent
from src.config import INDICATOR_CATALOGUE, get_settings
from src.database import SourceConfig, User, get_db
from src.utils.auth import get_current_user, check_role

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


async def _run_static_source(source_code: str, db: Session, tenant_id: uuid.UUID) -> IngestResult:
    started = datetime.now(timezone.utc)

    if source_code == "WORLD_BANK":
        raw_records = await WorldBankAgent().run_all()
    elif source_code == "IMF_WEO":
        raw_records = await IMFAgent().run_all()
    elif source_code == "FRED":
        raw_records = await FREDAgent().run_all()
    else:
        raise ValueError(f"Unknown static source: {source_code}")

    source = db.query(SourceConfig).filter(
        SourceConfig.source_code == source_code,
        (SourceConfig.tenant_id == None) | (SourceConfig.tenant_id == tenant_id)
    ).first()
    source_name = source.source_name if source else source_code
    source_url = source.source_url if source else ""

    pipeline = Pipeline(db, tenant_id=tenant_id)
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
    db: Session = Depends(get_db),
    current_user: User = Depends(check_role(["admin", "analyst"]))
):
    valid_sources = {"WORLD_BANK", "IMF_WEO", "FRED"}
    if source_code not in valid_sources:
        raise HTTPException(status_code=400, detail=f"Valid sources: {valid_sources}")

    result = await _run_static_source(source_code, db, tenant_id=current_user.tenant_id)
    return result


@router.post("/pipelines/orchestrate/run")
async def run_dagster_orchestration(
    current_user: User = Depends(check_role(["admin", "analyst"]))
):
    """Trigger the full Medallion orchestration via Dagster."""
    try:
        from src.orchestration.jobs import defs
        
        # Configuration for the run (including tenant_id)
        run_config = {
            "ops": {
                "world_bank_bronze": {"config": {"tenant_id": str(current_user.tenant_id)}},
                "imf_bronze": {"config": {"tenant_id": str(current_user.tenant_id)}},
                "fred_bronze": {"config": {"tenant_id": str(current_user.tenant_id)}},
                "silver_records": {"config": {"tenant_id": str(current_user.tenant_id)}},
                "gold_records": {"config": {"tenant_id": str(current_user.tenant_id)}},
                "macro_news": {"config": {"tenant_id": str(current_user.tenant_id)}},
                "macro_alerts": {"config": {"tenant_id": str(current_user.tenant_id)}},
                "macro_forecasts": {"config": {"tenant_id": str(current_user.tenant_id)}},
            }
        }
        
        # Retrieve the fully resolved job definition
        job_def = defs.get_job_def("full_ingestion_job")
        
        # Execute the job synchronously in the current process 
        # (For a true background run in a real cluster, we would use a GraphQL client to submit to the daemon)
        # But this works perfectly for local demo/API triggering without needing external repository origins.
        result = job_def.execute_in_process(run_config=run_config)
        
        if result.success:
            return {
                "message": "Orchestration job completed successfully",
                "run_id": result.run_id,
                "status": "SUCCESS"
            }
        else:
            raise HTTPException(status_code=500, detail="Orchestration job failed")
            
    except Exception as e:
        logger.error("Failed to launch Dagster job: %s", e)
        raise HTTPException(
            status_code=503,
            detail=f"Orchestration service (Dagster) failed: {str(e)}"
        )
