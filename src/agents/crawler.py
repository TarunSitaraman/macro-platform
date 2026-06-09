"""Dynamic crawler agent — Crawl4AI + LLM extraction for HTML sources."""

import asyncio
import concurrent.futures
import logging
from typing import Optional

from src.agents.llm_client import get_llm_client
from src.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


def _crawl4ai_in_thread(url: str, headless: bool) -> Optional[str]:
    """
    Run Crawl4AI in a dedicated thread with its own event loop.
    Playwright uses asyncio.create_subprocess_exec which fails inside
    Streamlit's nested event loop on Windows — a fresh thread avoids this.
    """
    async def _inner() -> Optional[str]:
        from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig
        browser_cfg = BrowserConfig(headless=headless)
        run_cfg = CrawlerRunConfig(
            word_count_threshold=50,
            exclude_external_links=True,
            remove_overlay_elements=True,
        )
        async with AsyncWebCrawler(config=browser_cfg) as crawler:
            result = await crawler.arun(url=url, config=run_cfg)
            if result.success:
                return result.markdown.raw_markdown or result.markdown.fit_markdown
            logger.error("Crawl4AI failed for %s: %s", url, result.error_message)
            return None

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_inner())
    finally:
        loop.close()

EXTRACTION_SYSTEM = """You are a macroeconomic data extraction specialist.
Extract ALL macroeconomic indicator values from the provided text.
Return a JSON object with a single key "records" whose value is an array of objects.
Each object must have these exact keys:
  indicator_code: one of GDP_CURRENT_USD, GDP_GROWTH, CPI_INFLATION, UNEMPLOYMENT_RATE, CURRENT_ACCOUNT_PCT_GDP, GOVT_DEBT_PCT_GDP
  country_code: ISO3 country code (e.g. USA, GBR, CHN)
  period: year string e.g. "2024" or "2024-Q1"
  raw_value: the numeric value as a string exactly as written in the text
  raw_unit: the unit as written (e.g. "percent", "%", "USD billions")
  is_forecast: true if labeled as forecast/projection/estimate, else false
  confidence: float 0-1 indicating extraction confidence

Example response format:
{"records": [{"indicator_code": "GDP_GROWTH", "country_code": "USA", "period": "2024", "raw_value": "2.8", "raw_unit": "%", "is_forecast": false, "confidence": 0.95}]}

If no indicator data is found, return {"records": []}.
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
        """Use Crawl4AI (in a thread) to JS-render the page and return clean markdown."""
        try:
            import crawl4ai  # noqa: F401 — check import before spinning a thread
            loop = asyncio.get_event_loop()
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                markdown = await loop.run_in_executor(
                    pool, _crawl4ai_in_thread, url, settings.crawl_headless
                )
            if markdown:
                return markdown
            logger.warning("Crawl4AI returned no content for %s; falling back to httpx", url)
            return await self._simple_fetch(url)
        except ImportError:
            logger.warning("crawl4ai not installed; falling back to httpx text fetch")
            return await self._simple_fetch(url)
        except Exception as exc:
            logger.warning("Crawl4AI failed (%s); falling back to httpx fetch for %s", exc, url)
            return await self._simple_fetch(url)

    async def _simple_fetch(self, url: str) -> Optional[str]:
        """Fallback: plain HTTP fetch without JS rendering."""
        import httpx
        import re
        try:
            async with httpx.AsyncClient(timeout=settings.crawl_timeout, follow_redirects=True) as client:
                resp = await client.get(url, headers={"User-Agent": "MacroPlatform/1.0"})
                resp.raise_for_status()
                html = resp.text
                # Remove script/style blocks including their content before stripping tags
                html = re.sub(r"<(script|style)[^>]*>.*?</(script|style)>", " ", html, flags=re.DOTALL | re.IGNORECASE)
                text = re.sub(r"<[^>]+>", " ", html)
                return " ".join(text.split())
        except Exception as exc:
            logger.error("Simple fetch failed for %s: %s", url, exc)
            return None

    async def _extract(
        self, markdown: str, source_url: str, extra_prompt: Optional[str]
    ) -> list[dict]:
        """Pass page content to LLM for structured extraction."""
        # Trim to first 24k chars — data often appears mid-article
        content = markdown[:24000]
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
            # Unwrap regardless of key name used by the model
            if isinstance(result, list):
                records = result
            elif isinstance(result, dict):
                records = next(
                    (v for v in result.values() if isinstance(v, list)),
                    [],
                )
            else:
                records = []
            # Drop clearly low-confidence extractions (threshold lowered to 0.4)
            valid = [r for r in records if isinstance(r, dict) and float(r.get("confidence", 1)) >= 0.4]
            logger.info(
                "Extracted %d/%d records from %s using %s",
                len(valid), len(records), source_url, model_used,
            )
            return valid
        except Exception as exc:
            logger.error("LLM extraction failed for %s: %s", source_url, exc)
            return []
