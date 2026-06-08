"""Bronze → Silver → Gold pipeline orchestration."""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from src.agents.embeddings import embed_batch, build_gold_record_text
from src.agents.qa import compute_dq_score, detect_forecast, parse_value
from src.config import get_settings
from src.database import (
    AuditLog, BronzeRecord, DataLineage, GoldRecord, ReviewQueue, SilverRecord,
)

logger = logging.getLogger(__name__)
settings = get_settings()

BATCH_SIZE = 100  # DB flush every N records


class Pipeline:
    """Orchestrates the Bronze → Silver → Gold medallion flow."""

    def __init__(self, db: Session):
        self.db = db

    # ── Bronze write ───────────────────────────────────────────────────────────

    def write_bronze(
        self,
        source_code: str,
        indicator_code: str,
        country_code: str,
        period: str,
        raw_value: str,
        raw_unit: str,
        source_url: str,
        extraction_method: str,
        raw_json: dict,
        request_id: Optional[str] = None,
    ) -> BronzeRecord:
        record = BronzeRecord(
            source_code=source_code,
            indicator_code=indicator_code,
            country_code=country_code,
            period=period,
            raw_value=raw_value,
            raw_unit=raw_unit,
            source_url=source_url,
            extraction_method=extraction_method,
            crawled_at=datetime.now(timezone.utc),
            raw_json=raw_json,
            request_id=request_id,
        )
        self.db.add(record)
        return record

    # ── Silver processing ──────────────────────────────────────────────────────

    def process_silver(self, bronze: BronzeRecord, standard_unit: str) -> SilverRecord:
        normalisation_log: list[str] = []

        value = parse_value(bronze.raw_value or "", standard_unit)
        if value is not None and bronze.raw_unit and bronze.raw_unit != standard_unit:
            normalisation_log.append(f"unit_converted:{bronze.raw_unit}->{standard_unit}")

        is_forecast = detect_forecast(bronze.raw_value or "")

        dq = compute_dq_score(
            value=value,
            raw_value=bronze.raw_value,
            indicator_code=bronze.indicator_code,
            country_code=bronze.country_code,
            period=bronze.period,
            source_url=bronze.source_url,
            standard_unit=standard_unit,
            raw_unit=bronze.raw_unit,
            crawled_at=bronze.crawled_at,
        )

        dq_score = dq["dq_score"]
        if dq_score >= settings.dq_auto_promote_threshold:
            dq_status = "AUTO_PROMOTED"
        elif dq_score >= settings.dq_review_threshold:
            dq_status = "REVIEW"
        else:
            dq_status = "REJECTED"

        silver = SilverRecord(
            bronze_id=bronze.record_id,
            source_code=bronze.source_code,
            indicator_code=bronze.indicator_code,
            country_code=bronze.country_code,
            period=bronze.period,
            value=value,
            standard_unit=standard_unit,
            is_forecast=is_forecast,
            dq_score=dq_score,
            dq_breakdown=dq["dq_breakdown"],
            dq_status=dq_status,
            failure_reasons=dq["failure_reasons"] or None,
            normalisation_applied="; ".join(normalisation_log) or None,
        )
        self.db.add(silver)
        return silver

    # ── Gold promotion (no embedding — deferred to batch step) ────────────────

    def promote_to_gold_sync(
        self,
        silver: SilverRecord,
        source_name: str,
        source_url: str,
        crawled_at: datetime,
        approved_by: str = "auto",
    ) -> GoldRecord:
        """Insert gold record without embedding. Call generate_embeddings() after bulk load."""
        existing = (
            self.db.query(GoldRecord)
            .filter(
                GoldRecord.indicator_code == silver.indicator_code,
                GoldRecord.country_code == silver.country_code,
                GoldRecord.period == silver.period,
            )
            .order_by(GoldRecord.promoted_at.desc())
            .first()
        )
        revision_flag = existing is not None
        revision_delta = (
            silver.value - existing.value
            if existing and silver.value is not None and existing.value is not None
            else None
        )

        gold = GoldRecord(
            silver_id=silver.record_id,
            indicator_code=silver.indicator_code,
            country_code=silver.country_code,
            period=silver.period,
            value=silver.value,
            standard_unit=silver.standard_unit,
            is_forecast=silver.is_forecast,
            source_name=source_name,
            source_url=source_url,
            source_code=silver.source_code,
            crawled_at=crawled_at,
            revision_flag=revision_flag,
            revision_delta=revision_delta,
            dq_score=silver.dq_score,
            approved_by=approved_by,
            promoted_at=datetime.now(timezone.utc),
            embedding=None,  # filled by generate_embeddings()
        )
        self.db.add(gold)
        return gold

    # ── Async gold promotion (single record, e.g. from review queue) ──────────

    async def promote_to_gold(
        self,
        silver: SilverRecord,
        source_name: str,
        source_url: str,
        crawled_at: datetime,
        approved_by: str = "auto",
    ) -> GoldRecord:
        """Promote with immediate embedding (used for review-queue approvals)."""
        gold = self.promote_to_gold_sync(silver, source_name, source_url, crawled_at, approved_by)
        self.db.flush()

        text_to_embed = build_gold_record_text(gold)
        try:
            vecs = await embed_batch([text_to_embed])
            gold.embedding = vecs[0]
            gold.embedding_model = settings.jina_embedding_model
            gold.embedding_generated_at = datetime.now(timezone.utc)
        except Exception as exc:
            logger.warning("Embedding failed for single record: %s", exc)

        self._lineage(silver.record_id, gold.record_id, "silver_to_gold")
        self._audit("gold_records", gold.record_id, "INSERT", new_values={"approved_by": approved_by})
        return gold

    # ── Batch embedding generation ─────────────────────────────────────────────

    async def generate_embeddings(self, batch_size: int = 200) -> int:
        """
        Generate embeddings for all gold records that don't have one yet.
        Calls Jina in batches of `batch_size` to minimise API round-trips.
        Returns the count of records updated.
        """
        records = (
            self.db.query(GoldRecord)
            .filter(GoldRecord.embedding.is_(None))
            .all()
        )
        if not records:
            return 0

        updated = 0
        for i in range(0, len(records), batch_size):
            chunk = records[i: i + batch_size]
            texts = [build_gold_record_text(r) for r in chunk]
            try:
                vecs = await embed_batch(texts)
                for rec, vec in zip(chunk, vecs):
                    rec.embedding = vec
                    rec.embedding_model = settings.jina_embedding_model
                    rec.embedding_generated_at = datetime.now(timezone.utc)
                self.db.commit()
                updated += len(chunk)
                logger.info("Embeddings generated: %d/%d", updated, len(records))
            except Exception as exc:
                logger.error("Embedding batch %d failed: %s", i, exc)
                self.db.rollback()

        return updated

    # ── Review queue ───────────────────────────────────────────────────────────

    def queue_for_review(self, silver: SilverRecord, source_url: str) -> ReviewQueue:
        sla = datetime.now(timezone.utc) + timedelta(hours=settings.review_sla_hours)
        item = ReviewQueue(
            silver_id=silver.record_id,
            indicator_code=silver.indicator_code,
            country_code=silver.country_code,
            period=silver.period,
            extracted_value=str(silver.value) if silver.value is not None else "N/A",
            dq_score=silver.dq_score,
            dq_breakdown=silver.dq_breakdown,
            failure_reasons=silver.failure_reasons,
            source_url=source_url,
            sla_deadline=sla,
        )
        self.db.add(item)
        return item

    # ── Bulk ingestion (main entry point for static/crawler agents) ────────────

    def run_bulk_sync(
        self,
        raw_records: list[dict],
        source_code: str,
        source_name: str,
        source_url: str,
        extraction_method: str,
        standard_unit_map: dict,
    ) -> dict:
        """
        Process a list of raw records synchronously through Bronze→Silver→Gold.
        No embeddings generated here — call generate_embeddings() afterwards.
        Returns summary counts.
        """
        promoted = queued = rejected = 0
        request_id = str(uuid.uuid4())

        for i, rec in enumerate(raw_records):
            ind_code = rec.get("indicator_code", "")
            standard_unit = standard_unit_map.get(ind_code, "")

            try:
                bronze = self.write_bronze(
                    source_code=source_code,
                    indicator_code=ind_code,
                    country_code=rec.get("country_code", ""),
                    period=rec.get("period", ""),
                    raw_value=rec.get("raw_value", ""),
                    raw_unit=rec.get("raw_unit", standard_unit),
                    source_url=rec.get("source_url", source_url),
                    extraction_method=extraction_method,
                    raw_json=rec.get("raw_json", rec),
                    request_id=request_id,
                )
                self.db.flush()

                silver = self.process_silver(bronze, standard_unit)
                self.db.flush()

                if silver.dq_status == "AUTO_PROMOTED":
                    self.promote_to_gold_sync(
                        silver=silver,
                        source_name=source_name,
                        source_url=rec.get("source_url", source_url),
                        crawled_at=bronze.crawled_at,
                    )
                    promoted += 1
                elif silver.dq_status == "REVIEW":
                    self.queue_for_review(silver, rec.get("source_url", source_url))
                    queued += 1
                else:
                    rejected += 1

            except Exception as exc:
                logger.error("Record %d failed: %s", i, exc)
                self.db.rollback()
                rejected += 1
                continue

            # Batch commit every BATCH_SIZE records
            if (i + 1) % BATCH_SIZE == 0:
                self.db.commit()
                logger.info("Committed batch %d/%d", i + 1, len(raw_records))

        self.db.commit()
        return {"promoted": promoted, "queued": queued, "rejected": rejected}

    # ── Single-record async run (kept for crawler / small batches) ────────────

    async def run(
        self,
        source_code: str,
        indicator_code: str,
        country_code: str,
        period: str,
        raw_value: str,
        raw_unit: str,
        source_url: str,
        extraction_method: str,
        raw_json: dict,
        standard_unit: str,
        source_name: str,
        request_id: Optional[str] = None,
    ) -> dict:
        request_id = request_id or str(uuid.uuid4())

        bronze = self.write_bronze(
            source_code=source_code, indicator_code=indicator_code,
            country_code=country_code, period=period, raw_value=raw_value,
            raw_unit=raw_unit, source_url=source_url,
            extraction_method=extraction_method, raw_json=raw_json,
            request_id=request_id,
        )
        self.db.flush()

        silver = self.process_silver(bronze, standard_unit)
        self.db.flush()

        result: dict = {
            "bronze_id": str(bronze.record_id),
            "silver_id": str(silver.record_id),
            "dq_score": silver.dq_score,
            "dq_status": silver.dq_status,
        }

        if silver.dq_status == "AUTO_PROMOTED":
            gold = await self.promote_to_gold(
                silver=silver, source_name=source_name,
                source_url=source_url, crawled_at=bronze.crawled_at,
            )
            result["gold_id"] = str(gold.record_id)
            result["status"] = "promoted"
        elif silver.dq_status == "REVIEW":
            queue_item = self.queue_for_review(silver, source_url)
            result["queue_id"] = str(queue_item.queue_id)
            result["status"] = "review"
        else:
            result["status"] = "rejected"
            result["failure_reasons"] = silver.failure_reasons

        self.db.commit()
        return result

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _audit(self, table, record_id, action, new_values=None, old_values=None):
        self.db.add(AuditLog(
            table_name=table, record_id=record_id, action=action,
            new_values=new_values, old_values=old_values,
            actor="system", actor_role="pipeline",
        ))

    def _lineage(self, source_id, target_id, transformation):
        self.db.add(DataLineage(
            source_record_id=source_id, target_record_id=target_id,
            transformation=transformation, transform_version="1.0.0",
            started_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
            status="SUCCESS",
        ))
