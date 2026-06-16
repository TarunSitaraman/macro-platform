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
    "COUNTRY_SNAPSHOT": """You are a senior macroeconomist. Write a concise but analytical economic snapshot for {country}.
Use ONLY the data provided below. Cite key figures as [Source: <source_name>, <period>].
Use GitHub-flavored Markdown. Include:
- A markdown table of 4–6 key indicators (leave blank lines before/after).
- Where genuinely useful, one Mermaid diagram (```mermaid, graph TD only). MERMAID RULES: quote ALL node labels e.g. A["GDP 3.2 pct"]; no %, (), [], : inside labels; no [Source:] inside diagrams; simple A/B/C node IDs only.
- GitHub alerts (`> [!IMPORTANT]` or `> [!WARNING]`) for 1–2 critical risks.
Write for a financial professional. Be precise and analytical. Aim for 400–600 words.

Data:
{data_block}""",

    "INDICATOR_BRIEF": """You are a senior macroeconomist. Write a concise cross-country analysis of {indicator}.
Use ONLY the data provided. Cite key figures as [Source: <source_name>, <period>].
Use GitHub-flavored Markdown. Include:
- A markdown table comparing this indicator across countries or periods (blank lines before/after).
- Where genuinely useful, one Mermaid diagram (```mermaid, graph TD only). MERMAID RULES: quote ALL node labels e.g. A["High 8 pct"]; no %, (), [], : inside labels; no [Source:] inside diagrams; simple A/B/C node IDs only.
- GitHub alerts for 1–2 notable outliers or risks.
Identify outliers, trends, and structural shifts. Aim for 400–600 words.

Data:
{data_block}""",

    "SECTOR_ANALYSIS": """You are a senior macroeconomist. Write a concise cross-indicator analysis of {context}.
Synthesise relationships between GDP growth, inflation, unemployment, trade, and fiscal position.
Use ONLY the data provided. Cite key figures as [Source: <source_name>, <period>].
Use GitHub-flavored Markdown. Include:
- A markdown table of key macro linkages (blank lines before/after).
- Where genuinely useful, one Mermaid diagram (```mermaid, graph TD only). MERMAID RULES: quote ALL node labels e.g. A["Debt 60 pct GDP"]; no %, (), [], : inside labels; no [Source:] inside diagrams; simple A/B/C node IDs only.
- GitHub alerts for 1–2 policy risks or structural vulnerabilities.
Focus on macro linkages and policy implications. Aim for 400–600 words.

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

    _CACHE_TTL_HOURS = 24
    _RECORD_LIMIT = 50

    def _get_cached(self, country_code: str, summary_type: str) -> Optional[Summary]:
        """Return an existing summary if one was generated within the TTL window."""
        from datetime import timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(hours=self._CACHE_TTL_HOURS)
        row = (
            self.db.query(Summary)
            .filter(
                Summary.country_code == country_code,
                Summary.summary_type == summary_type,
                (Summary.tenant_id == None) | (Summary.tenant_id == self.tenant_id),
                Summary.generated_at >= cutoff,
            )
            .order_by(Summary.generated_at.desc())
            .first()
        )
        if row:
            self.db.expunge(row)
        return row

    async def generate_country_snapshot(
        self,
        country: str,
        year_from: int = 2018,
        indicators: Optional[list[str]] = None,
    ) -> Summary:
        cached = self._get_cached(country, "COUNTRY_SNAPSHOT")
        if cached:
            logger.info("Cache hit: COUNTRY_SNAPSHOT / %s", country)
            return cached
        query = self.db.query(GoldRecord).filter(
            GoldRecord.country_code == country,
            GoldRecord.period >= str(year_from),
            (GoldRecord.tenant_id == None) | (GoldRecord.tenant_id == self.tenant_id)
        )
        if indicators:
            query = query.filter(GoldRecord.indicator_code.in_(indicators))
        records = query.order_by(GoldRecord.period.desc()).limit(self._RECORD_LIMIT).all()
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
        cached = self._get_cached("MULTI", "INDICATOR_BRIEF")
        if cached:
            logger.info("Cache hit: INDICATOR_BRIEF / %s", indicator_code)
            return cached
        query = self.db.query(GoldRecord).filter(
            GoldRecord.indicator_code == indicator_code,
            GoldRecord.period >= str(year_from),
            (GoldRecord.tenant_id == None) | (GoldRecord.tenant_id == self.tenant_id)
        )
        if countries:
            query = query.filter(GoldRecord.country_code.in_(countries))
        records = query.order_by(GoldRecord.period.desc()).limit(self._RECORD_LIMIT).all()
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
        cached = self._get_cached(country, "SECTOR_ANALYSIS")
        if cached:
            logger.info("Cache hit: SECTOR_ANALYSIS / %s / %s", sector_theme, country)
            return cached
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
            .limit(self._RECORD_LIMIT)
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
