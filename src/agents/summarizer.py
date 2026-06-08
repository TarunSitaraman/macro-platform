"""AI summary generation agent — country snapshots, indicator briefs, sector analysis."""

import logging
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

    def __init__(self, db: Session):
        self.db = db

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

    async def generate_country_snapshot(
        self, country_code: str, year_from: int = 2018
    ) -> Summary:
        records = (
            self.db.query(GoldRecord)
            .filter(
                GoldRecord.country_code == country_code,
                GoldRecord.period >= str(year_from),
            )
            .order_by(GoldRecord.period.desc())
            .limit(60)
            .all()
        )
        return await self._generate(
            country_code=country_code,
            summary_type="COUNTRY_SNAPSHOT",
            records=records,
            context_label=country_code,
        )

    async def generate_indicator_brief(
        self, indicator_code: str, countries: Optional[list[str]] = None
    ) -> Summary:
        query = self.db.query(GoldRecord).filter(
            GoldRecord.indicator_code == indicator_code,
        )
        if countries:
            query = query.filter(GoldRecord.country_code.in_(countries))
        records = query.order_by(GoldRecord.period.desc()).limit(60).all()

        return await self._generate(
            country_code="MULTI",
            summary_type="INDICATOR_BRIEF",
            records=records,
            context_label=indicator_code,
        )

    async def generate_sector_analysis(
        self, country_code: str, context_label: str = "Monetary Conditions"
    ) -> Summary:
        records = (
            self.db.query(GoldRecord)
            .filter(
                GoldRecord.country_code == country_code,
                GoldRecord.indicator_code.in_([
                    "GDP_GROWTH", "CPI_INFLATION", "UNEMPLOYMENT_RATE",
                    "CURRENT_ACCOUNT_PCT_GDP", "GOVT_DEBT_PCT_GDP",
                ]),
            )
            .order_by(GoldRecord.period.desc())
            .limit(60)
            .all()
        )
        return await self._generate(
            country_code=country_code,
            summary_type="SECTOR_ANALYSIS",
            records=records,
            context_label=context_label,
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
            country_code=country_code,
            summary_type=summary_type,
            content=content,
            indicators_used=used_ids or None,
            model_used=model_used,
            template_version="1.0",
        )
        self.db.add(summary)
        self.db.commit()
        logger.info("Summary generated: %s / %s / model=%s", summary_type, country_code, model_used)
        return summary

    def list_summaries(
        self,
        country_code: Optional[str] = None,
        summary_type: Optional[str] = None,
        limit: int = 20,
    ) -> list[Summary]:
        query = self.db.query(Summary)
        if country_code:
            query = query.filter(Summary.country_code == country_code)
        if summary_type:
            query = query.filter(Summary.summary_type == summary_type)
        return query.order_by(Summary.generated_at.desc()).limit(limit).all()
