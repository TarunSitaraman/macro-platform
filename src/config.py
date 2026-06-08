"""Central configuration — reads from environment / .env file."""

import os
from functools import lru_cache
from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    # Database
    database_url: str = Field(..., env="DATABASE_URL")

    # LLM providers (all optional — set whichever you have a key for)
    groq_api_key: str = Field("", env="GROQ_API_KEY")
    groq_base_url: str = Field("https://api.groq.com/openai/v1", env="GROQ_BASE_URL")
    gemini_api_key: str = Field("", env="GEMINI_API_KEY")
    gemini_base_url: str = Field(
        "https://generativelanguage.googleapis.com/v1beta/openai", env="GEMINI_BASE_URL"
    )
    openrouter_api_key: str = Field("", env="OPENROUTER_API_KEY")
    openrouter_base_url: str = Field(
        "https://openrouter.ai/api/v1", env="OPENROUTER_BASE_URL"
    )

    # Embeddings (Jina AI — free tier 1M tokens/month)
    jina_api_key: str = Field(..., env="JINA_API_KEY")
    jina_embedding_model: str = Field("jina-embeddings-v3", env="JINA_EMBEDDING_MODEL")
    jina_embedding_dimensions: int = Field(1024, env="JINA_EMBEDDING_DIMENSIONS")

    # App
    app_env: str = Field("development", env="APP_ENV")
    log_level: str = Field("INFO", env="LOG_LEVEL")
    api_secret_key: str = Field("change-me", env="API_SECRET_KEY")
    api_rate_limit: int = Field(100, env="API_RATE_LIMIT")

    # External data sources
    world_bank_base_url: str = Field(
        "https://api.worldbank.org/v2", env="WORLD_BANK_BASE_URL"
    )
    imf_base_url: str = Field(
        "https://www.imf.org/external/datamapper/api/v1", env="IMF_BASE_URL"
    )
    fred_api_key: str = Field("", env="FRED_API_KEY")
    oecd_base_url: str = Field(
        "https://sdmx.oecd.org/public/rest", env="OECD_BASE_URL"
    )

    # Crawling
    crawl_headless: bool = Field(True, env="CRAWL_HEADLESS")
    crawl_timeout: int = Field(30, env="CRAWL_TIMEOUT")
    crawl_max_retries: int = Field(3, env="CRAWL_MAX_RETRIES")

    # DQ thresholds
    dq_auto_promote_threshold: float = Field(90.0, env="DQ_AUTO_PROMOTE_THRESHOLD")
    dq_review_threshold: float = Field(70.0, env="DQ_REVIEW_THRESHOLD")
    review_sla_hours: int = Field(4, env="REVIEW_SLA_HOURS")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False


@lru_cache
def get_settings() -> Settings:
    return Settings()


# ── Model routing config ──────────────────────────────────────────────────────
# Three tiers: simple → medium → complex
# Each tier has a primary model and ordered fallback chain.

MODEL_ROUTES: dict[str, dict] = {
    # Intent classification, simple field extraction, JSON normalization
    "simple": {
        "candidates": [
            {"provider": "groq", "model": "llama-3.3-70b-versatile"},
            {"provider": "gemini", "model": "gemini-2.0-flash"},
            {"provider": "openrouter", "model": "meta-llama/llama-3.3-70b-instruct:free"},
        ],
        "max_tokens": 1024,
        "temperature": 0.0,
    },
    # Structured extraction, DQ rationale
    "medium": {
        "candidates": [
            {"provider": "groq", "model": "llama-3.3-70b-versatile"},
            {"provider": "gemini", "model": "gemini-2.0-flash"},
            {"provider": "openrouter", "model": "meta-llama/llama-3.3-70b-instruct:free"},
        ],
        "max_tokens": 4096,
        "temperature": 0.0,
    },
    # Summaries, RAG chat, complex reasoning
    "complex": {
        "candidates": [
            {"provider": "groq", "model": "llama-3.3-70b-versatile"},
            {"provider": "groq", "model": "llama-3.1-8b-instant"},
            {"provider": "gemini", "model": "gemini-2.0-flash"},
            {"provider": "openrouter", "model": "meta-llama/llama-3.3-70b-instruct:free"},
        ],
        "max_tokens": 8192,
        "temperature": 0.2,
    },
}

# Phase 1 indicator catalogue
INDICATOR_CATALOGUE = {
    "GDP_CURRENT_USD": {
        "name": "GDP (Current USD)",
        "category": "Economic Growth",
        "standard_unit": "USD_BN",
        "description": "Gross Domestic Product in current US Dollars",
        "frequency": "ANNUAL",
    },
    "GDP_GROWTH": {
        "name": "GDP Growth Rate",
        "category": "Economic Growth",
        "standard_unit": "PCT",
        "description": "Annual percentage growth rate of GDP",
        "frequency": "ANNUAL",
    },
    "CPI_INFLATION": {
        "name": "CPI Inflation Rate",
        "category": "Inflation",
        "standard_unit": "PCT",
        "description": "Consumer Price Index annual inflation rate",
        "frequency": "ANNUAL",
    },
    "UNEMPLOYMENT_RATE": {
        "name": "Unemployment Rate",
        "category": "Employment",
        "standard_unit": "PCT",
        "description": "Percentage of labour force that is unemployed",
        "frequency": "ANNUAL",
    },
    "CURRENT_ACCOUNT_PCT_GDP": {
        "name": "Current Account Balance (% GDP)",
        "category": "Trade",
        "standard_unit": "PCT_GDP",
        "description": "Current account balance as percentage of GDP",
        "frequency": "ANNUAL",
    },
    "GOVT_DEBT_PCT_GDP": {
        "name": "Government Debt (% GDP)",
        "category": "Fiscal",
        "standard_unit": "PCT_GDP",
        "description": "General government gross debt as percentage of GDP",
        "frequency": "ANNUAL",
    },
}

# Phase 1 country list (ISO3 codes)
PHASE1_COUNTRIES = [
    "USA", "GBR", "DEU", "FRA", "JPN", "CHN", "IND", "BRA",
    "CAN", "AUS", "KOR", "MEX", "ITA", "ESP", "NLD",
    "SAU", "ZAF", "ARG", "IDN", "TUR",
]

# World Bank indicator codes mapping
WORLD_BANK_INDICATORS = {
    "GDP_CURRENT_USD": "NY.GDP.MKTP.CD",
    "GDP_GROWTH": "NY.GDP.MKTP.KD.ZG",
    "CPI_INFLATION": "FP.CPI.TOTL.ZG",
    "UNEMPLOYMENT_RATE": "SL.UEM.TOTL.ZS",
    "CURRENT_ACCOUNT_PCT_GDP": "BN.CAB.XOKA.GD.ZS",
    "GOVT_DEBT_PCT_GDP": "GC.DOD.TOTL.GD.ZS",
}

# IMF WEO indicator codes mapping
IMF_INDICATORS = {
    "GDP_CURRENT_USD": "NGDPD",
    "GDP_GROWTH": "NGDP_RPCH",
    "CPI_INFLATION": "PCPIPCH",
    "UNEMPLOYMENT_RATE": "LUR",
    "CURRENT_ACCOUNT_PCT_GDP": "BCA_NGDPD",
    "GOVT_DEBT_PCT_GDP": "GGXWDG_NGDP",
}

# FRED series codes
FRED_SERIES = {
    "GDP_CURRENT_USD": "GDP",
    "CPI_INFLATION": "CPIAUCSL",
    "UNEMPLOYMENT_RATE": "UNRATE",
}
