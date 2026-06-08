"""Dynamic crawler agent — Crawl4AI + LLM extraction for HTML sources."""

import json
import logging
from typing import Optional

from src.agents.llm_client import get_llm_client
from src.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

EXTRACTION_SYSTEM = """You are a macroeconomic data extraction specialist.
Extract ALL macroeconomic indicator values from the provided text.
Return ONLY a JSON array of objects. Each object must have these exact keys:
  indicator_code: one of GDP_CURRENT_USD, GDP_GROWTH, CPI_INFLATION, UNEMPLOYMENT_RATE, CURRENT_ACCOUNT_PCT_GDP, GOVT_DEBT_PCT_GDP
  country_code: ISO3 country code (e.g. USA, GBR, CHN)
  period: year string e.g. "2024" or "2024-Q1"
  raw_value: the numeric value as a string exactly as written in the text
  raw_unit: the unit as written (e.g. "percent", "%", "USD billions")
  is_forecast: true if labeled as forecast/projection/estimate, else false
  confidence: float 0-1 indicating extraction confidence

If no indicator data is found, return an empty array [].
Do not invent values. Only extract what is explicitly stated in the text."""


class DynamicCrawlerAgent:
    """
    Crawls HTML pages using Crawl4AI, extracts clean markdown,
    then passes to LLM for structured indicator extraction.
    """

    async def crawl_and_extract(
        self, url: str, extraction_prompt: Optional[str] = None
    ) -> list[dict]:
        """
        Fetch and parse a URL, then extract indicator records via LLM.
        Returns list of raw dicts ready for the pipeline.
        """
        markdown = await self._crawl(url)
        if not markdown:
            logger.warning("Crawl returned no content for: %s", url)
            return []

        return await self._extract(markdown, url, extraction_prompt)

    async def _crawl(self, url: str) -> Optional[str]:
        """Use Crawl4AI to render the page and return clean markdown."""
        try:
            from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig

            browser_cfg = BrowserConfig(headless=settings.crawl_headless)
            run_cfg = CrawlerRunConfig(
                word_count_threshold=50,
                exclude_external_links=True,
                remove_overlay_elements=True,
            )
            async with AsyncWebCrawler(config=browser_cfg) as crawler:
                result = await crawler.arun(url=url, config=run_cfg)
                if result.success:
                    return result.markdown.fit_markdown or result.markdown.raw_markdown
                logger.error("Crawl4AI failed for %s: %s", url, result.error_message)
                return None
        except ImportError:
            logger.warning("crawl4ai not installed; falling back to httpx text fetch")
            return await self._simple_fetch(url)
        except Exception as exc:
            logger.error("Crawler error for %s: %s", url, exc)
            return None

    async def _simple_fetch(self, url: str) -> Optional[str]:
        """Fallback: plain HTTP fetch without JS rendering."""
        import httpx
        try:
            async with httpx.AsyncClient(timeout=settings.crawl_timeout) as client:
                resp = await client.get(url, headers={"User-Agent": "HexawareMacro/1.0"})
                resp.raise_for_status()
                # Strip HTML tags roughly
                import re
                text = re.sub(r"<[^>]+>", " ", resp.text)
                return " ".join(text.split())
        except Exception as exc:
            logger.error("Simple fetch failed for %s: %s", url, exc)
            return None

    async def _extract(
        self, markdown: str, source_url: str, extra_prompt: Optional[str]
    ) -> list[dict]:
        """Pass page content to LLM for structured extraction."""
        # Trim to first 12k chars to stay within context limits
        content = markdown[:12000]
        prompt = f"Source URL: {source_url}\n\n"
        if extra_prompt:
            prompt += f"Additional context: {extra_prompt}\n\n"
        prompt += f"Page content:\n{content}\n\nExtract all macroeconomic indicator values."

        client = get_llm_client()
        try:
            result, model_used = await client.extract_json(
                prompt=prompt,
                system=EXTRACTION_SYSTEM,
                tier="medium",
            )
            records = result if isinstance(result, list) else result.get("data", [])
            # Filter out low-confidence extractions
            valid = [r for r in records if isinstance(r, dict) and float(r.get("confidence", 0)) >= 0.6]
            logger.info(
                "Extracted %d/%d records from %s using %s",
                len(valid), len(records), source_url, model_used,
            )
            return valid
        except Exception as exc:
            logger.error("LLM extraction failed for %s: %s", source_url, exc)
            return []
