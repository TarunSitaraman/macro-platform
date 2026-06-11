"""AI summary generation agent — country snapshots, indicator briefs, sector analysis."""

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from src.agents.llm_client import get_llm_client
from src.database import GoldRecord, Summary

logger = logging.getLogger(__name__)

SUMMARY_PROMPTS = {
    "COUNTRY_SNAPSHOT": """You are a senior macroeconomist. Write a 3-4 paragraph economic snapshot for {country}.
Use ONLY the data provided below. Cite every numeric value as [Source: <source_name>, <period>].
Highlight key trends, notable changes, and forward-looking commentary where data supports it.
Write for a financial professional audience. Be precise and objective.

Data:
{data_block}""",

    "INDICATOR_BRIEF": """You are a senior macroeconomist. Write a focused 2-3 paragraph analysis of {indicator} across countries or time periods.
Use ONLY the data provided. Cite every number as [Source: <source_name>, <period>].
Compare across countries where relevant. Identify outliers and trends.

Data:
{data_block}""",

    "SECTOR_ANALYSIS": """You are a senior macroeconomist. Write a 3-4 paragraph cross-indicator analysis examining {context}.
Synthesise the relationships between GDP growth, inflation, unemployment, trade, and fiscal position.
Use ONLY the data provided. Cite every number as [Source: <source_name>, <period>].
Focus on macro linkages and policy implications.

Data:
{data_block}""",
}


class SummarizerAgent:
    """Generates AI summaries from gold records using the complex LLM tier."""

    def __init__(self, db: Session, tenant_id: Optional[uuid.UUID] = None):
        self.db = db
        self.tenant_id = tenant_id

    def _build_data_block(self, records: list[GoldRecord]) -> tuple[str, list]:
        """Convert gold records to a formatted text block for LLM context."""
        lines = []
        ids = []
        for r in sorted(records, key=lambda x: (x.indicator_code, x.period)):
            forecast = " [FORECAST]" if r.is_forecast else ""
            lines.append(
                f"- {r.indicator_code}: {r.value} {r.standard_unit} "
                f"({r.country_code}, {r.period}){forecast} "
                f"[Source: {r.source_name}]"
            )
            ids.append(str(r.record_id))
        return "\n".join(lines), ids

    SECTOR_INDICATORS = {
        "Monetary Conditions":   ["CPI_INFLATION", "GDP_GROWTH"],
        "Fiscal Position":       ["GOVT_DEBT_PCT_GDP", "GDP_CURRENT_USD"],
        "External Balance":      ["CURRENT_ACCOUNT_PCT_GDP", "GDP_GROWTH"],
        "Labour Market":         ["UNEMPLOYMENT_RATE", "GDP_GROWTH"],
        "Full Macro Overview":   ["GDP_GROWTH", "CPI_INFLATION", "UNEMPLOYMENT_RATE",
                                  "CURRENT_ACCOUNT_PCT_GDP", "GOVT_DEBT_PCT_GDP"],
    }

    async def generate_country_snapshot(
        self,
        country: str,
        year_from: int = 2018,
        indicators: Optional[list[str]] = None,
    ) -> Summary:
        query = self.db.query(GoldRecord).filter(
            GoldRecord.country_code == country,
            GoldRecord.period >= str(year_from),
            (GoldRecord.tenant_id == None) | (GoldRecord.tenant_id == self.tenant_id)
        )
        if indicators:
            query = query.filter(GoldRecord.indicator_code.in_(indicators))
        records = query.order_by(GoldRecord.period.desc()).limit(120).all()
        return await self._generate(
            country_code=country,
            summary_type="COUNTRY_SNAPSHOT",
            records=records,
            context_label=country,
        )

    async def generate_indicator_brief(
        self,
        indicator_code: str,
        countries: Optional[list[str]] = None,
        year_from: int = 2018,
    ) -> Summary:
        query = self.db.query(GoldRecord).filter(
            GoldRecord.indicator_code == indicator_code,
            GoldRecord.period >= str(year_from),
            (GoldRecord.tenant_id == None) | (GoldRecord.tenant_id == self.tenant_id)
        )
        if countries:
            query = query.filter(GoldRecord.country_code.in_(countries))
        records = query.order_by(GoldRecord.period.desc()).limit(120).all()
        return await self._generate(
            country_code="MULTI",
            summary_type="INDICATOR_BRIEF",
            records=records,
            context_label=indicator_code,
        )

    async def generate_sector_analysis(
        self,
        country: str,
        sector_theme: str = "Full Macro Overview",
    ) -> Summary:
        ind_codes = self.SECTOR_INDICATORS.get(
            sector_theme, self.SECTOR_INDICATORS["Full Macro Overview"]
        )
        records = (
            self.db.query(GoldRecord)
            .filter(
                GoldRecord.country_code == country,
                GoldRecord.indicator_code.in_(ind_codes),
                (GoldRecord.tenant_id == None) | (GoldRecord.tenant_id == self.tenant_id)
            )
            .order_by(GoldRecord.period.desc())
            .limit(120)
            .all()
        )
        label = f"{sector_theme} — {country}"
        return await self._generate(
            country_code=country,
            summary_type="SECTOR_ANALYSIS",
            records=records,
            context_label=label,
        )

    async def _generate(
        self,
        country_code: str,
        summary_type: str,
        records: list[GoldRecord],
        context_label: str,
    ) -> Summary:
        data_block, used_ids = self._build_data_block(records)

        if not data_block:
            data_block = "No data available in the database for this selection."

        prompt_template = SUMMARY_PROMPTS[summary_type]
        prompt = prompt_template.format(
            country=context_label,
            indicator=context_label,
            context=context_label,
            data_block=data_block,
        )

        client = get_llm_client()
        content, model_used = await client.chat(
            messages=[{"role": "user", "content": prompt}],
            tier="complex",
        )

        summary = Summary(
            tenant_id=self.tenant_id,
            country_code=country_code,
            summary_type=summary_type,
            content=content,
            indicators_used=used_ids or None,
            model_used=model_used,
            template_version="1.0",
        )
        self.db.add(summary)
        self.db.commit()
        self.db.refresh(summary)
        # Detach cleanly so attributes stay readable after session closes
        self.db.expunge(summary)
        logger.info("Summary generated: %s / %s / model=%s", summary_type, country_code, model_used)
        return summary

    def list_summaries(
        self,
        country_code: Optional[str] = None,
        summary_type: Optional[str] = None,
        limit: int = 20,
    ) -> list[Summary]:
        query = self.db.query(Summary).filter(Summary.tenant_id == self.tenant_id)
        if country_code:
            query = query.filter(Summary.country_code == country_code)
        if summary_type:
            query = query.filter(Summary.summary_type == summary_type)
        return query.order_by(Summary.generated_at.desc()).limit(limit).all()
