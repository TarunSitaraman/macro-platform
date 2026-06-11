"""Senior Research Agent — combines web search with internal data for deep reports."""

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional, List, Dict

from duckduckgo_search import DDGS
from sqlalchemy.orm import Session

from src.agents.llm_client import get_llm_client
from src.database import GoldRecord, NewsRecord
from src.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

RESEARCH_PROMPT = """You are a Lead Macroeconomic Researcher. 
Your task is to compile a comprehensive investment-grade research report on the topic requested.

### Data Inputs:
1. **Internal Gold Data**: {internal_data}
2. **Recent News**: {news_data}
3. **Web Context**: {web_data}

### Guidelines:
- Use a professional, objective, and analytical tone.
- Cite your sources clearly using [Source Name, Year] format.
- Structure the report with: Executive Summary, Key Indicators, Recent Developments, Risks & Outlook.
- If data is conflicting, highlight the discrepancy.
- End with a clear "Analyst Perspective".

Topic: {topic}
"""

class ResearcherAgent:
    """Agent that performs multi-source research for report generation."""

    def __init__(self, db: Session, tenant_id: Optional[uuid.UUID] = None):
        self.db = db
        self.tenant_id = tenant_id

    async def _search_web(self, query: str, max_results: int = 5) -> str:
        """Free web search via DuckDuckGo."""
        try:
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=max_results))
                lines = [f"- {r['title']}: {r['body']} (Link: {r['href']})" for r in results]
                return "\n".join(lines)
        except Exception as e:
            logger.warning("Web search failed: %s", e)
            return "No web context available."

    def _get_internal_data(self, topic: str) -> str:
        """Retrieve relevant indicators from the Gold layer."""
        # Simple keyword match for indicators for now
        from src.config import INDICATOR_CATALOGUE
        keywords = topic.upper().split()
        relevant_codes = [k for k, v in INDICATOR_CATALOGUE.items() if any(kw in v['name'].upper() or kw in k for kw in keywords)]
        
        if not relevant_codes:
            relevant_codes = ["GDP_GROWTH", "CPI_INFLATION"] # Default defaults

        records = (
            self.db.query(GoldRecord)
            .filter(
                GoldRecord.indicator_code.in_(relevant_codes),
                (GoldRecord.tenant_id == None) | (GoldRecord.tenant_id == self.tenant_id)
            )
            .order_by(GoldRecord.period.desc())
            .limit(20)
            .all()
        )
        
        lines = [f"- {r.indicator_code} ({r.country_code}, {r.period}): {r.value} {r.standard_unit}" for r in records]
        return "\n".join(lines) if lines else "No internal data found."

    async def compile_report(self, topic: str) -> Dict[str, str]:
        """Orchestrate the research process and generate a full report."""
        logger.info("Starting research on: %s", topic)
        
        # 1. Gather Data
        web_task = self._search_web(f"macroeconomic outlook {topic}")
        internal_data = self._get_internal_data(topic)
        
        # Pull recent news from our own DB
        news_recs = (
            self.db.query(NewsRecord)
            .filter((NewsRecord.tenant_id == None) | (NewsRecord.tenant_id == self.tenant_id))
            .order_by(NewsRecord.published_at.desc())
            .limit(5)
            .all()
        )
        news_data = "\n".join([f"- {n.title} ({n.source_name}): {n.sentiment_label}" for n in news_recs])
        
        web_data = await web_task
        
        # 2. Synthesize with LLM
        prompt = RESEARCH_PROMPT.format(
            topic=topic,
            internal_data=internal_data,
            news_data=news_data or "No internal news records.",
            web_data=web_data
        )
        
        client = get_llm_client()
        # Using complex tier (Gemini Flash or Llama 3 70B) for synthesis
        content, model_used = await client.chat(
            messages=[{"role": "user", "content": prompt}],
            tier="complex"
        )
        
        return {
            "topic": topic,
            "content": content,
            "model": model_used,
            "generated_at": datetime.now(timezone.utc).isoformat()
        }
