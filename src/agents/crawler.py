"""Dynamic crawler agent — Crawl4AI + LLM extraction for HTML sources."""

import asyncio
import concurrent.futures
import ipaddress
import logging
import socket
from typing import Optional
from urllib.parse import urlparse, urljoin

from src.agents.llm_client import get_llm_client
from src.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

_BLOCKED_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]


def _is_safe_url(url: str) -> bool:
    """Return False if url resolves to any private/reserved address."""
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        hostname = (parsed.hostname or "").lower().rstrip(".")
        if not hostname:
            return False
        for (_fam, _type, _proto, _canon, sockaddr) in socket.getaddrinfo(hostname, None):
            try:
                ip = ipaddress.ip_address(sockaddr[0])
            except ValueError:
                continue
            if any(ip in net for net in _BLOCKED_NETWORKS):
                return False
        return True
    except Exception:
        return False


async def _safe_get_text(client, url: str, *, headers: Optional[dict] = None, max_redirects: int = 5) -> Optional[str]:
    """GET url with manual redirect following, re-checking SSRF safety at each hop."""
    current_url = url
    for _ in range(max_redirects + 1):
        resp = await client.get(current_url, headers=headers or {})
        if resp.is_redirect:
            location = resp.headers.get("location", "")
            next_url = urljoin(current_url, location)
            if not _is_safe_url(next_url):
                logger.warning("SSRF: blocked redirect to %s", next_url)
                return None
            current_url = next_url
            continue
        resp.raise_for_status()
        return resp.text
    logger.warning("Too many redirects for %s", url)
    return None


def _html_in_thread(url: str, headless: bool) -> Optional[str]:
    """Run Playwright in a thread and return the raw rendered HTML (for link discovery)."""
    async def _inner() -> Optional[str]:
        from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig
        browser_cfg = BrowserConfig(headless=headless)
        run_cfg = CrawlerRunConfig(remove_overlay_elements=True)
        async with AsyncWebCrawler(config=browser_cfg) as crawler:
            result = await crawler.arun(url=url, config=run_cfg)
            return result.html if result.success else None

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_inner())
    finally:
        loop.close()


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


# Curated high-value articles per source — shown as fallback when dynamic discovery finds nothing
CURATED_ARTICLES: dict[str, list[dict]] = {
    "IMF_BLOG": [
        {"title": "World Economic Outlook Update, January 2025", "url": "https://www.imf.org/en/Publications/WEO/Issues/2025/01/17/world-economic-outlook-update-january-2025"},
        {"title": "World Economic Outlook, October 2024", "url": "https://www.imf.org/en/Publications/WEO/Issues/2024/10/22/world-economic-outlook-october-2024"},
        {"title": "World Economic Outlook Update, July 2024", "url": "https://www.imf.org/en/Publications/WEO/Issues/2024/07/16/world-economic-outlook-update-july-2024"},
        {"title": "World Economic Outlook, April 2024", "url": "https://www.imf.org/en/Publications/WEO/Issues/2024/04/16/world-economic-outlook-april-2024"},
        {"title": "Global Economic Prospects, January 2025 (WB)", "url": "https://www.worldbank.org/en/publication/global-economic-prospects"},
    ],
    "WB_PROSPECTS": [
        {"title": "Global Economic Prospects, January 2025", "url": "https://www.worldbank.org/en/publication/global-economic-prospects"},
        {"title": "Global Economic Prospects, June 2024", "url": "https://openknowledge.worldbank.org/bitstreams/7d84ce04-c277-4f2b-a51e-c44bbc9e7760/download"},
        {"title": "Commodity Markets Outlook, October 2024", "url": "https://www.worldbank.org/en/publication/commodity-markets-outlook"},
    ],
    "OECD_OUTLOOK": [
        {"title": "OECD Economic Outlook, November 2024", "url": "https://www.oecd.org/en/publications/oecd-economic-outlook-volume-2024-issue-2_a41daca5-en.html"},
        {"title": "OECD Interim Economic Outlook, September 2024", "url": "https://www.oecd.org/en/publications/oecd-economic-outlook-interim-report-september-2024_16690dfb-en.html"},
    ],
    "BIS_REVIEW": [
        {"title": "BIS Quarterly Review, December 2024", "url": "https://www.bis.org/publ/qtrpdf/r_qt2412.htm"},
        {"title": "BIS Quarterly Review, September 2024", "url": "https://www.bis.org/publ/qtrpdf/r_qt2409.htm"},
    ],
}


class DynamicCrawlerAgent:
    """
    Crawls HTML pages using Crawl4AI, extracts clean markdown,
    then passes to LLM for structured indicator extraction.
    """

    async def discover_articles(self, source_url: str, source_code: str = "") -> list[dict]:
        """
        Render the source index page and extract article links.
        Falls back to curated list if the page yields nothing.
        Returns list of {title, url} dicts.
        """
        articles = await self._discover_dynamic(source_url)
        if not articles:
            articles = CURATED_ARTICLES.get(source_code, [])
        return articles

    async def _discover_dynamic(self, source_url: str) -> list[dict]:
        """Render page with Playwright and extract article links via regex."""
        import re
        from urllib.parse import urlparse

        html: Optional[str] = None
        try:
            import crawl4ai  # noqa: F401
            loop = asyncio.get_event_loop()
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                html = await loop.run_in_executor(
                    pool, _html_in_thread, source_url, settings.crawl_headless
                )
        except Exception:
            pass

        if not html:
            try:
                import httpx
                async with httpx.AsyncClient(
                    headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=False, timeout=15
                ) as client:
                    html = await _safe_get_text(client, source_url)
            except Exception:
                pass

        if not html:
            return []

        parsed = urlparse(source_url)
        base = f"{parsed.scheme}://{parsed.netloc}"

        ARTICLE_PATTERNS = [
            r"/[Bb]logs?/[Aa]rticles?/\d{4}/",
            r"/[Pp]ublications?/[A-Z]",
            r"/\d{4}/\d{2}/\d{2}/",
            r"/press-release/\d{4}/",
            r"/[Oo]utlook/",
            r"/publ/qtr",
            r"/en/doc/",
        ]

        links = re.findall(
            r'<a[^>]+href=["\']([^"\'#?][^"\']*)["\'][^>]*>(.*?)</a>',
            html, re.IGNORECASE | re.DOTALL,
        )
        seen: set[str] = set()
        articles: list[dict] = []
        for href, raw_text in links:
            url = href if href.startswith("http") else base + href
            if url in seen:
                continue
            if not any(re.search(p, url) for p in ARTICLE_PATTERNS):
                continue
            title = re.sub(r"<[^>]+>", "", raw_text).strip()
            title = " ".join(title.split())
            if len(title) < 8:
                continue
            seen.add(url)
            articles.append({"title": title[:120], "url": url})
            if len(articles) >= 25:
                break

        return articles

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
            async with httpx.AsyncClient(timeout=settings.crawl_timeout, follow_redirects=False) as client:
                html = await _safe_get_text(client, url, headers={"User-Agent": "MacroPlatform/1.0"})
                if html is None:
                    return None
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
