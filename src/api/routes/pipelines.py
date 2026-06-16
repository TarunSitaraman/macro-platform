"""Pipeline management endpoints — trigger ingestion, check status."""

import asyncio
import ipaddress
import logging
import socket
import uuid
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.agents.pipeline import Pipeline
from src.agents.static import FREDAgent, IMFAgent, WorldBankAgent
from src.agents.crawler import DynamicCrawlerAgent
from src.config import INDICATOR_CATALOGUE, SOURCE_VALUE_MULTIPLIERS, get_settings
from src.database import GoldRecord, SourceConfig, User, get_db, SessionLocal
from src.utils.auth import get_current_user, check_role
from src.utils.anomaly_cache import AnomalyCacheManager

router = APIRouter()
logger = logging.getLogger(__name__)
settings = get_settings()

_BLOCKED_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),   # link-local / cloud metadata
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]


def _assert_safe_url(url: str) -> None:
    """Raise HTTP 400 if url points to a private/reserved address (SSRF guard).

    Checks every address returned by getaddrinfo (IPv4 + IPv6) so that
    multi-record DNS responses and IPv6-mapped addresses cannot bypass the
    blocklist via a single-record gethostbyname call.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(status_code=400, detail="URL must use http or https scheme")
    hostname = parsed.hostname
    if not hostname:
        raise HTTPException(status_code=400, detail="Invalid URL: missing hostname")
    # Normalise: strip trailing dot and lowercase
    hostname = hostname.lower().rstrip(".")
    try:
        results = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        raise HTTPException(status_code=400, detail="Could not resolve hostname")
    if not results:
        raise HTTPException(status_code=400, detail="Could not resolve hostname")
    for (_fam, _type, _proto, _canon, sockaddr) in results:
        try:
            ip = ipaddress.ip_address(sockaddr[0])
        except ValueError:
            continue
        for network in _BLOCKED_NETWORKS:
            if ip in network:
                raise HTTPException(
                    status_code=400,
                    detail="URL resolves to a private or reserved address",
                )


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
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(check_role(["admin", "analyst"]))
):
    valid_sources = {"WORLD_BANK", "IMF_WEO", "FRED"}
    if source_code not in valid_sources:
        raise HTTPException(status_code=400, detail=f"Valid sources: {valid_sources}")

    result = await _run_static_source(source_code, db, tenant_id=current_user.tenant_id)
    # Trigger background anomalies refresh
    background_tasks.add_task(AnomalyCacheManager.calculate_and_cache, SessionLocal, current_user.tenant_id)
    return result


@router.post("/pipelines/orchestrate/run")
async def run_dagster_orchestration(
    background_tasks: BackgroundTasks,
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
            # Trigger background anomalies refresh after successful ingestion
            background_tasks.add_task(AnomalyCacheManager.calculate_and_cache, SessionLocal, current_user.tenant_id)
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


@router.post("/pipelines/embeddings/run")
async def run_embeddings_refresher(
    db: Session = Depends(get_db),
    current_user: User = Depends(check_role(["admin", "analyst"]))
):
    """Trigger the Gemini embeddings generator for unindexed gold records."""
    pipeline = Pipeline(db, tenant_id=current_user.tenant_id)
    try:
        updated = await pipeline.generate_embeddings()
        return {
            "status": "SUCCESS",
            "message": f"Successfully generated embeddings for {updated} gold records."
        }
    except Exception as e:
        logger.error("Failed to run embedding generation: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ── Crawler endpoints ──

class DiscoverRequest(BaseModel):
    source_url: str
    source_code: str


class CrawlRequest(BaseModel):
    url: str
    extraction_prompt: Optional[str] = None


class PushRecord(BaseModel):
    indicator_code: str
    country_code: str
    period: str
    raw_value: str
    raw_unit: Optional[str] = None
    source_url: str
    source_code: str


class PushRequest(BaseModel):
    records: list[PushRecord]


@router.get("/crawler/sources")
def list_crawler_sources(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """List HTML/PDF crawl sources."""
    sources = (
        db.query(SourceConfig)
        .filter(SourceConfig.source_type.in_(["HTML", "PDF"]))
        .filter((SourceConfig.tenant_id == None) | (SourceConfig.tenant_id == current_user.tenant_id))
        .all()
    )
    return [
        {
            "source_code": s.source_code,
            "source_name": s.source_name,
            "source_url": s.source_url,
            "source_type": s.source_type,
            "frequency": s.frequency,
            "reputation_score": s.reputation_score,
            "extraction_prompt": s.extraction_prompt,
            "is_active": s.is_active,
            "last_run_at": s.last_run_at.isoformat() if s.last_run_at else None,
            "error_message": s.error_message,
        }
        for s in sources
    ]


@router.post("/crawler/discover")
async def discover_crawler_articles(
    body: DiscoverRequest,
    current_user: User = Depends(check_role(["admin", "analyst"]))
):
    """Scan the source index page for article links."""
    _assert_safe_url(body.source_url)
    try:
        crawler = DynamicCrawlerAgent()
        articles = await crawler.discover_articles(body.source_url, body.source_code)
        return {"articles": articles}
    except Exception as e:
        logger.error("Failed to discover articles: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/crawler/crawl")
async def crawl_and_extract_url(
    body: CrawlRequest,
    current_user: User = Depends(check_role(["admin", "analyst"]))
):
    """Crawl a URL and extract macroeconomic indicators using LLM."""
    _assert_safe_url(body.url)
    try:
        crawler = DynamicCrawlerAgent()
        extracted = await crawler.crawl_and_extract(
            url=body.url,
            extraction_prompt=body.extraction_prompt,
        )
        return {"extracted": extracted}
    except Exception as e:
        logger.error("Failed to crawl and extract: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/crawler/push")
async def push_extracted_records(
    body: PushRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(check_role(["admin", "analyst"]))
):
    """Push extracted records through the medallion pipeline."""
    pipeline = Pipeline(db, tenant_id=current_user.tenant_id)
    promoted = queued = rejected = 0
    
    # Pre-fetch tenant-scoped source configurations
    sources = db.query(SourceConfig).filter(
        (SourceConfig.tenant_id == None) | (SourceConfig.tenant_id == current_user.tenant_id)
    ).all()
    source_map = {s.source_code: s for s in sources}

    for rec in body.records:
        ind_code = rec.indicator_code
        if ind_code not in INDICATOR_CATALOGUE:
            rejected += 1
            continue
        unit = INDICATOR_CATALOGUE[ind_code]["standard_unit"]
        
        # Get source metadata
        source = source_map.get(rec.source_code)
        source_name = source.source_name if source else rec.source_code
        
        try:
            result = await pipeline.run(
                source_code=rec.source_code,
                indicator_code=ind_code,
                country_code=rec.country_code,
                period=rec.period,
                raw_value=rec.raw_value,
                raw_unit=rec.raw_unit or unit,
                source_url=rec.source_url,
                extraction_method="HTML_LLM",
                raw_json={"indicator_code": ind_code, "country_code": rec.country_code, "period": rec.period, "raw_value": rec.raw_value},
                standard_unit=unit,
                source_name=source_name,
            )
            if result["status"] == "promoted":
                promoted += 1
            elif result["status"] == "review":
                queued += 1
            else:
                rejected += 1
        except Exception as e:
            logger.error("Failed to push record through pipeline: %s", e)
            rejected += 1
            
    # Update last run timestamp for the source of the first record (if any)
    if body.records:
        first_src = source_map.get(body.records[0].source_code)
        if first_src:
            first_src.last_run_at = datetime.now(timezone.utc)
            db.commit()

    if promoted > 0:
        # Trigger background anomalies refresh on data change
        background_tasks.add_task(AnomalyCacheManager.calculate_and_cache, SessionLocal, current_user.tenant_id)

    return {"promoted": promoted, "queued": queued, "rejected": rejected}


@router.post("/pipelines/repair-unit-scale")
def repair_unit_scale(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    One-time repair: fix gold records that were ingested with the wrong scale
    (e.g. World Bank GDP stored as raw absolute USD instead of billions).

    Detects violations by comparing stored value against the standard unit's
    expected magnitude and applies the correct divisor in-place.
    """
    # Map each standard_unit to the magnitude threshold above which the value
    # must be in the base unit (not the target unit), and the corrective divisor.
    REPAIR_RULES: list[tuple[str, float, float]] = [
        # (standard_unit, threshold_value_meaning_wrong_scale, divisor_to_apply)
        ("USD_BN",   1e9,  1e9),   # raw absolute USD → divide by 1 billion
        ("MILLIONS", 1e6,  1e6),   # raw count      → divide by 1 million
    ]

    fixed = 0
    skipped = 0
    for standard_unit, threshold, divisor in REPAIR_RULES:
        bad_records = (
            db.query(GoldRecord)
            .filter(
                GoldRecord.standard_unit == standard_unit,
                GoldRecord.value >= threshold,
                (GoldRecord.tenant_id == None) | (GoldRecord.tenant_id == current_user.tenant_id),
            )
            .all()
        )
        for rec in bad_records:
            rec.value = rec.value / divisor
            fixed += 1

    if fixed:
        db.commit()
        logger.info("repair-unit-scale: corrected %d gold records", fixed)

    return {"fixed": fixed, "skipped": skipped}
