"""Dagster assets for the Medallion architecture."""

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from dagster import AssetExecutionContext, Config, asset
from src.agents.pipeline import Pipeline
from src.agents.static import IMFAgent, WorldBankAgent, FREDAgent
from src.config import INDICATOR_CATALOGUE
from src.orchestration.resources import DatabaseResource

class TenantConfig(Config):
    tenant_id: Optional[str] = None # UUID as string

# ── Bronze Assets ─────────────────────────────────────────────────────────────

@asset(group_name="bronze")
def world_bank_bronze(config: TenantConfig, db_resource: DatabaseResource) -> int:
    """Fetch raw records from World Bank and write to Bronze layer."""
    tenant_id = uuid.UUID(config.tenant_id) if config.tenant_id else None
    raw_records = asyncio.run(WorldBankAgent().run_all())
    
    with db_resource.get_db() as db:
        pipeline = Pipeline(db, tenant_id=tenant_id)
        for rec in raw_records:
            pipeline.write_bronze(
                source_code="WORLD_BANK",
                indicator_code=rec["indicator_code"],
                country_code=rec["country_code"],
                period=rec["period"],
                raw_value=rec["raw_value"],
                raw_unit=rec.get("raw_unit", ""),
                source_url=rec.get("source_url", ""),
                extraction_method="API",
                raw_json=rec.get("raw_json", {}),
            )
        db.commit()
    return len(raw_records)

@asset(group_name="bronze")
def imf_bronze(config: TenantConfig, db_resource: DatabaseResource) -> int:
    """Fetch raw records from IMF and write to Bronze layer."""
    tenant_id = uuid.UUID(config.tenant_id) if config.tenant_id else None
    raw_records = asyncio.run(IMFAgent().run_all())
    
    with db_resource.get_db() as db:
        pipeline = Pipeline(db, tenant_id=tenant_id)
        for rec in raw_records:
            pipeline.write_bronze(
                source_code="IMF_WEO",
                indicator_code=rec["indicator_code"],
                country_code=rec["country_code"],
                period=rec["period"],
                raw_value=rec["raw_value"],
                raw_unit=rec.get("raw_unit", ""),
                source_url=rec.get("source_url", ""),
                extraction_method="API",
                raw_json=rec.get("raw_json", {}),
            )
        db.commit()
    return len(raw_records)

@asset(group_name="bronze")
def fred_bronze(config: TenantConfig, db_resource: DatabaseResource) -> int:
    """Fetch raw records from FRED and write to Bronze layer."""
    tenant_id = uuid.UUID(config.tenant_id) if config.tenant_id else None
    raw_records = asyncio.run(FREDAgent().run_all())
    
    with db_resource.get_db() as db:
        pipeline = Pipeline(db, tenant_id=tenant_id)
        for rec in raw_records:
            pipeline.write_bronze(
                source_code="FRED",
                indicator_code=rec["indicator_code"],
                country_code=rec["country_code"],
                period=rec["period"],
                raw_value=rec["raw_value"],
                raw_unit=rec.get("raw_unit", ""),
                source_url=rec.get("source_url", ""),
                extraction_method="API",
                raw_json=rec.get("raw_json", {}),
            )
        db.commit()
    return len(raw_records)

# ── Silver Assets ─────────────────────────────────────────────────────────────

@asset(
    deps=[world_bank_bronze, imf_bronze, fred_bronze],
    group_name="silver"
)
def silver_records(config: TenantConfig, db_resource: DatabaseResource) -> int:
    """Process Bronze records into Silver layer with DQ scoring."""
    tenant_id = uuid.UUID(config.tenant_id) if config.tenant_id else None
    processed_count = 0
    
    with db_resource.get_db() as db:
        from src.database import BronzeRecord, SilverRecord
        # Find unprocessed bronze records for this tenant
        # (For simplicity, we check for records that don't have a corresponding silver_id yet)
        unprocessed = db.query(BronzeRecord).filter(
            BronzeRecord.tenant_id == tenant_id,
            ~db.query(SilverRecord).filter(SilverRecord.bronze_id == BronzeRecord.record_id).exists()
        ).all()
        
        pipeline = Pipeline(db, tenant_id=tenant_id)
        for bronze in unprocessed:
            unit = INDICATOR_CATALOGUE.get(bronze.indicator_code, {}).get("standard_unit", "")
            pipeline.process_silver(bronze, standard_unit=unit)
            processed_count += 1
            if processed_count % 100 == 0:
                db.flush()
        
        db.commit()
    return processed_count

from src.agents.news import NewsAgent

# ── News Assets ───────────────────────────────────────────────────────────────

@asset(group_name="news")
def macro_news(config: TenantConfig, db_resource: DatabaseResource) -> int:
    """Ingest recent macroeconomic news and analyze sentiment."""
    tenant_id = uuid.UUID(config.tenant_id) if config.tenant_id else None
    
    # For demo: ingest a known macro news source
    urls = [
        "https://www.imf.org/en/Blogs/Articles/2024/11/20/global-economy-navigates-a-soft-landing",
    ]
    
    ingested_count = 0
    with db_resource.get_db() as db:
        agent = NewsAgent(db, tenant_id=tenant_id)
        for url in urls:
            try:
                rec = asyncio.run(agent.ingest_from_url(url, "IMF Blog"))
                if rec:
                    ingested_count += 1
            except Exception as e:
                logger.error("Failed to ingest news from %s: %s", url, e)
        db.commit()
    return ingested_count

from src.agents.forecaster import ForecasterAgent

# ── Gold Assets ───────────────────────────────────────────────────────────────

@asset(
    deps=[silver_records],
    group_name="gold"
)
def gold_records(config: TenantConfig, db_resource: DatabaseResource) -> int:
    """Promote Silver records to Gold layer and generate embeddings."""
    tenant_id = uuid.UUID(config.tenant_id) if config.tenant_id else None
    promoted_count = 0
    
    with db_resource.get_db() as db:
        from src.database import SilverRecord, GoldRecord, SourceConfig
        
        # Find AUTO_PROMOTED silver records not yet in gold
        to_promote = db.query(SilverRecord).filter(
            SilverRecord.tenant_id == tenant_id,
            SilverRecord.dq_status == "AUTO_PROMOTED",
            ~db.query(GoldRecord).filter(GoldRecord.silver_id == SilverRecord.record_id).exists()
        ).all()
        
        # Cache source configs to avoid N+1 queries
        sources = db.query(SourceConfig).filter(
            (SourceConfig.tenant_id == None) | (SourceConfig.tenant_id == tenant_id)
        ).all()
        source_map = {s.source_code: s for s in sources}
        
        pipeline = Pipeline(db, tenant_id=tenant_id)
        for silver in to_promote:
            source = source_map.get(silver.source_code)
            pipeline.promote_to_gold_sync(
                silver=silver,
                source_name=source.source_name if source else silver.source_code,
                source_url=source.source_url if source else "",
                crawled_at=silver.processed_at or datetime.now(timezone.utc),
            )
            promoted_count += 1
            if promoted_count % 500 == 0:
                db.flush()
        
        db.commit()
        
        # Generate embeddings for new gold records
        if promoted_count > 0:
            try:
                asyncio.run(pipeline.generate_embeddings())
            except Exception as e:
                logger.error("Embedding generation failed during orchestration: %s", e)
            
    return promoted_count

from src.agents.alerts import AlertAgent

# ── Analytics Assets ──────────────────────────────────────────────────────────

@asset(
    deps=[gold_records],
    group_name="analytics"
)
def macro_alerts(config: TenantConfig, db_resource: DatabaseResource) -> int:
    """Monitor Gold records for critical threshold breaches."""
    tenant_id = uuid.UUID(config.tenant_id) if config.tenant_id else None
    
    with db_resource.get_db() as db:
        agent = AlertAgent(db, tenant_id=tenant_id)
        signals = agent.check_thresholds()
        return len(signals)

@asset(
    deps=[gold_records],
    group_name="analytics"
)
def macro_forecasts(config: TenantConfig, db_resource: DatabaseResource) -> int:
    """Generate time-series forecasts for all indicator/country pairs."""
    tenant_id = uuid.UUID(config.tenant_id) if config.tenant_id else None
    forecast_count = 0
    
    with db_resource.get_db() as db:
        # Find unique pairs of (indicator, country) in gold records
        from src.database import GoldRecord, SilverRecord
        from sqlalchemy import distinct
        
        pairs = db.query(GoldRecord.indicator_code, GoldRecord.country_code).filter(
            (GoldRecord.tenant_id == None) | (GoldRecord.tenant_id == tenant_id)
        ).distinct().all()
        
        agent = ForecasterAgent(db, tenant_id=tenant_id)
        
        for ind_code, country_code in pairs:
            # Delete existing forecasts for this pair to avoid duplicates
            db.query(GoldRecord).filter(
                GoldRecord.indicator_code == ind_code,
                GoldRecord.country_code == country_code,
                GoldRecord.tenant_id == tenant_id,
                GoldRecord.is_forecast == True
            ).delete()
            
            predictions = agent.run_forecast(ind_code, country_code, periods=4)
            
            for p in predictions:
                # Create a pseudo-silver record reference or handle null
                # (For forecasts, we insert directly into Gold for now)
                forecast_rec = GoldRecord(
                    tenant_id=tenant_id,
                    silver_id=uuid.uuid4(), # Pseudo ID
                    indicator_code=ind_code,
                    country_code=country_code,
                    period=p["period"],
                    value=p["value"],
                    standard_unit=INDICATOR_CATALOGUE.get(ind_code, {}).get("standard_unit", ""),
                    is_forecast=True,
                    source_name="AI Forecaster (Prophet)",
                    dq_score=95.0,
                    approved_by="system",
                    promoted_at=datetime.now(timezone.utc),
                )
                db.add(forecast_rec)
                forecast_count += 1
                
        db.commit()
    return forecast_count
