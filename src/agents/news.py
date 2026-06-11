"""Agent for fetching macroeconomic news and performing sentiment analysis."""

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from src.agents.crawler import DynamicCrawlerAgent
from src.agents.llm_client import get_llm_client
from src.database import NewsRecord
from src.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

NEWS_ANALYSIS_PROMPT = """You are a senior macroeconomist and financial analyst.
Analyze the following news headline and content.
1. Assign a sentiment score from -1 (Extremely Negative) to 1 (Extremely Positive).
2. Provide a sentiment label (Positive, Neutral, Negative).
3. Identify the primary category (Fiscal Policy, Monetary Policy, Trade, GDP Growth, Inflation, Labour Market, Other).
4. Identify which macroeconomic indicators this news might impact (use standard codes like GDP_GROWTH, CPI_INFLATION, etc.).
5. Identify the country or region affected (ISO3 code if possible).

Format your response as a JSON object:
{{
    "sentiment_score": float,
    "sentiment_label": "Positive" | "Neutral" | "Negative",
    "category": string,
    "impact_indicators": ["CODE1", "CODE2"],
    "country_code": "ISO3"
}}

News:
Title: {title}
Content: {content}
"""

class NewsAgent:
    """Handles news ingestion, sentiment analysis, and embedding."""

    def __init__(self, db: Session, tenant_id: Optional[uuid.UUID] = None):
        self.db = db
        self.tenant_id = tenant_id

    async def analyze_sentiment(self, title: str, content: str) -> dict:
        """Use LLM to analyze news sentiment and impact."""
        prompt = NEWS_ANALYSIS_PROMPT.format(title=title, content=content)
        client = get_llm_client()
        
        # Using simple tier for analysis to save tokens/cost
        response, _ = await client.chat(
            messages=[{"role": "user", "content": prompt}],
            system="Return only valid JSON.",
            tier="simple"
        )
        
        import json
        import re
        
        # Clean JSON response
        match = re.search(r"\{.*\}", response, re.DOTALL)
        if match:
            return json.loads(match.group())
        return {}

    async def ingest_from_url(self, url: str, source_name: str):
        """Crawl a news URL, analyze it, and save to DB."""
        crawler = DynamicCrawlerAgent()
        
        # Just getting the text for now
        results = await crawler.crawl_and_extract(
            url=url,
            extraction_prompt="Extract the main article title and full content text."
        )
        
        if not results:
            logger.warning("No content extracted from %s", url)
            return

        # Assuming the first result is the main article
        article = results[0]
        title = article.get("title", "No Title")
        content = article.get("content", str(article))
        
        analysis = await self.analyze_sentiment(title, content)
        
        news_rec = NewsRecord(
            tenant_id=self.tenant_id,
            source_name=source_name,
            title=title,
            content=content,
            url=url,
            published_at=datetime.now(timezone.utc), # Fallback to now
            category=analysis.get("category"),
            sentiment_score=analysis.get("sentiment_score"),
            sentiment_label=analysis.get("sentiment_label"),
            impact_indicators=analysis.get("impact_indicators"),
            country_code=analysis.get("country_code"),
        )
        
        self.db.add(news_rec)
        self.db.flush()
        
        # Generate embedding
        from src.agents.embeddings import embed_batch
        text_to_embed = f"Title: {title}\nCategory: {news_rec.category}\nContent: {content[:1000]}"
        try:
            vecs = await embed_batch([text_to_embed])
            news_rec.embedding = vecs[0]
        except Exception as e:
            logger.warning("Failed to embed news: %s", e)

        self.db.commit()
        logger.info("Ingested news: %s (Sentiment: %s)", title, news_rec.sentiment_label)
        return news_rec
