"""Static data agents — World Bank, IMF, OECD, FRED REST API connectors."""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

import aiohttp

from src.config import (
    FRED_SERIES, IMF_INDICATORS, INDICATOR_CATALOGUE,
    PHASE1_COUNTRIES, WORLD_BANK_INDICATORS, get_settings,
)

logger = logging.getLogger(__name__)
settings = get_settings()

_USER_AGENT = "Mozilla/5.0"


class WorldBankAgent:
    """Fetches indicator data from the World Bank Open Data API v2."""

    BASE = settings.world_bank_base_url

    async def fetch_indicator(
        self, session: aiohttp.ClientSession, indicator_code: str, country: str, year_range: tuple[int, int]
    ) -> list[dict]:
        wb_code = WORLD_BANK_INDICATORS.get(indicator_code)
        if not wb_code:
            return []

        url = (
            f"{self.BASE}/country/{country.lower()}/indicator/{wb_code}"
            f"?format=json&per_page=100&mrv={year_range[1] - year_range[0] + 10}"
        )
        try:
            async with session.get(url, headers={"User-Agent": _USER_AGENT}) as resp:
                resp.raise_for_status()
                data = await resp.json()
        except Exception as exc:
            logger.error("WorldBank fetch failed: %s | %s", url, exc)
            return []

        if len(data) < 2 or not data[1]:
            return []

        records = []
        for entry in data[1]:
            val = entry.get("value")
            year = str(entry.get("date", ""))
            if val is None or not year:
                continue
            records.append({
                "indicator_code": indicator_code,
                "country_code": country,
                "period": year,
                "raw_value": str(val),
                "raw_unit": INDICATOR_CATALOGUE[indicator_code]["standard_unit"],
                "source_url": url,
                "raw_json": entry,
            })
        return records

    async def run_all(self, year_from: int = 2015, year_to: Optional[int] = None) -> list[dict]:
        year_to = year_to or datetime.now(timezone.utc).year
        all_records: list[dict] = []

        async with aiohttp.ClientSession() as session:
            tasks = []
            for country in PHASE1_COUNTRIES:
                for ind_code in WORLD_BANK_INDICATORS:
                    tasks.append(
                        self.fetch_indicator(session, ind_code, country, (year_from, year_to))
                    )
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in results:
                if isinstance(r, list):
                    all_records.extend(r)
                else:
                    logger.warning("WorldBank task error: %s", r)

        logger.info("WorldBank: fetched %d raw records", len(all_records))
        return all_records


class IMFAgent:
    """Fetches WEO data from the IMF DataMapper API.

    IMF blocks concurrent connections and comma-separated country lists.
    Fix: fetch each indicator once (all countries), filter to Phase 1 locally.
    6 sequential requests instead of 120 per-country requests.
    """

    BASE = settings.imf_base_url
    _HEADERS = {
        "User-Agent": _USER_AGENT,
        "Accept": "application/json, text/plain, */*",
    }
    _PHASE1_SET = set(PHASE1_COUNTRIES)

    def _fetch_indicator_sync(self, indicator_code: str) -> list[dict]:
        """Fetch one indicator for all countries, return only Phase 1 records."""
        import httpx
        imf_code = IMF_INDICATORS.get(indicator_code)
        if not imf_code:
            return []
        url = f"{self.BASE}/{imf_code}"
        try:
            resp = httpx.get(url, headers=self._HEADERS, follow_redirects=True, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.error("IMF fetch failed: %s | %s", url, exc)
            return []

        all_countries = data.get("values", {}).get(imf_code, {})
        records = []
        for country, year_map in all_countries.items():
            if country not in self._PHASE1_SET:
                continue
            for year, val in year_map.items():
                if val is None:
                    continue
                records.append({
                    "indicator_code": indicator_code,
                    "country_code": country,
                    "period": str(year),
                    "raw_value": str(val),
                    "raw_unit": INDICATOR_CATALOGUE[indicator_code]["standard_unit"],
                    "source_url": url,
                    "raw_json": {"country": country, "year": year, "value": val},
                })
        return records

    async def run_all(self) -> list[dict]:
        import concurrent.futures
        loop = asyncio.get_event_loop()
        all_records: list[dict] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            futures = [
                loop.run_in_executor(pool, self._fetch_indicator_sync, ind)
                for ind in IMF_INDICATORS
            ]
            results = await asyncio.gather(*futures, return_exceptions=True)
        for r in results:
            if isinstance(r, list):
                all_records.extend(r)
            else:
                logger.warning("IMF task error: %s", r)
        logger.info("IMF: fetched %d raw records", len(all_records))
        return all_records


class FREDAgent:
    """Fetches US economic data from the St. Louis Fed FRED API."""

    BASE = "https://api.stlouisfed.org/fred"

    async def fetch_series(
        self, session: aiohttp.ClientSession, indicator_code: str
    ) -> list[dict]:
        fred_series = FRED_SERIES.get(indicator_code)
        if not fred_series or not settings.fred_api_key:
            return []

        url = (
            f"{self.BASE}/series/observations"
            f"?series_id={fred_series}&api_key={settings.fred_api_key}"
            f"&file_type=json&frequency=a&observation_start=2010-01-01"
        )
        try:
            async with session.get(url, headers={"User-Agent": _USER_AGENT}) as resp:
                resp.raise_for_status()
                data = await resp.json()
        except Exception as exc:
            logger.error("FRED fetch failed: %s | %s", url, exc)
            return []

        records = []
        for obs in data.get("observations", []):
            val = obs.get("value", ".")
            if val in (".", ""):
                continue
            year = obs.get("date", "")[:4]
            records.append({
                "indicator_code": indicator_code,
                "country_code": "USA",
                "period": year,
                "raw_value": val,
                "raw_unit": INDICATOR_CATALOGUE[indicator_code]["standard_unit"],
                "source_url": url,
                "raw_json": obs,
            })
        return records

    async def run_all(self) -> list[dict]:
        all_records: list[dict] = []
        async with aiohttp.ClientSession() as session:
            tasks = [self.fetch_series(session, code) for code in FRED_SERIES]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in results:
                if isinstance(r, list):
                    all_records.extend(r)
        logger.info("FRED: fetched %d raw records", len(all_records))
        return all_records
