"""Pillar 3 — Safety: Guardrail engine protecting chatbot outputs from harmful content. Satisfies INTERNAL AI Safety Policy."""

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import structlog
from sqlalchemy import Boolean, Column, DateTime, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Session

from src.database import Base

logger = structlog.get_logger().bind(pillar="safety")

# ── Constants ──────────────────────────────────────────────────────────────────

INVESTMENT_ADVICE_KEYWORDS = [
    "buy",
    "sell",
    "invest",
    "portfolio",
    "stock pick",
    "short",
    "long position",
    "trade",
    "accumulate",
    "divest",
    "allocate capital",
    "financial advice",
    "recommend buying",
    "recommend selling",
    "bullish on",
    "bearish on",
    "price target",
    "upside potential",
    "entry point",
    "exit strategy",
]

INVESTMENT_REFUSAL = (
    "I can provide macroeconomic data and analysis, but I'm not able to offer "
    "investment advice or recommendations. Please consult a qualified financial advisor."
)

DOMAIN_KEYWORDS = [
    "gdp",
    "inflation",
    "unemployment",
    "monetary policy",
    "fiscal",
    "interest rate",
    "central bank",
    "trade balance",
    "current account",
    "government debt",
    "cpi",
    "pmi",
    "recession",
    "economic growth",
    "exchange rate",
    "bop",
    "balance of payments",
    "sovereign debt",
    "bond yield",
    "budget deficit",
    "surplus",
    "import",
    "export",
    "forex",
    "currency",
    "quantitative easing",
    "tightening",
    "yield curve",
    "credit rating",
    "economic indicator",
]

OUT_OF_SCOPE_REFUSAL = (
    "I'm specialized in macroeconomic data and analysis. Your question appears to be "
    "outside this domain. I can help with GDP, inflation, trade data, monetary policy, "
    "and related macroeconomic topics."
)

FORECAST_KEYWORDS = [
    "forecast",
    "projected",
    "expected",
    "estimate",
    "projection",
    "outlook",
    "predicted",
]

FORECAST_DISCLAIMER = (
    "\n\n_Disclaimer: Forecasts are based on published sources and historical models. "
    "Actual results may differ materially._"
)


# ── Dataclass ──────────────────────────────────────────────────────────────────

@dataclass
class GuardrailResult:
    passed: bool
    triggered_filter: Optional[str]
    modified_response: str
    compliance_context: str = "INTERNAL - AI Safety"


# ── SQLAlchemy model ───────────────────────────────────────────────────────────

class GuardrailAuditLog(Base):
    __tablename__ = "guardrail_audit_log"

    log_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    query_hash = Column(String(64), nullable=False)
    triggered_filter = Column(String(100), nullable=True)
    passed = Column(Boolean, nullable=False)
    response_length = Column(Integer, nullable=False)
    logged_at = Column(DateTime, default=datetime.utcnow)
    compliance_context = Column(String, default="INTERNAL - AI Safety")


# ── Filters ────────────────────────────────────────────────────────────────────

class InvestmentAdviceFilter:
    def check(self, query: str, response: str) -> GuardrailResult:
        response_lower = response.lower()
        for keyword in INVESTMENT_ADVICE_KEYWORDS:
            if keyword in response_lower:
                logger.warning(
                    "investment_advice_filter_triggered",
                    keyword=keyword,
                )
                return GuardrailResult(
                    passed=False,
                    triggered_filter="InvestmentAdviceFilter",
                    modified_response=INVESTMENT_REFUSAL,
                )
        return GuardrailResult(
            passed=True,
            triggered_filter=None,
            modified_response=response,
        )


class ScopeFilter:
    def check(self, query: str, response: str) -> GuardrailResult:
        query_lower = query.lower()
        count = sum(1 for kw in DOMAIN_KEYWORDS if kw in query_lower)
        if count == 0:
            logger.warning("scope_filter_triggered", query_preview=query[:80])
            return GuardrailResult(
                passed=False,
                triggered_filter="ScopeFilter",
                modified_response=OUT_OF_SCOPE_REFUSAL,
            )
        return GuardrailResult(
            passed=True,
            triggered_filter=None,
            modified_response=response,
        )


class ForecastDisclaimerInjector:
    def inject(self, response: str) -> str:
        response_lower = response.lower()
        has_forecast_term = any(kw in response_lower for kw in FORECAST_KEYWORDS)
        if has_forecast_term and FORECAST_DISCLAIMER not in response:
            return response + FORECAST_DISCLAIMER
        return response


# ── Engine ─────────────────────────────────────────────────────────────────────

class GuardrailEngine:
    def __init__(self, db: Session) -> None:
        self._db = db

    def _log(self, query: str, result: GuardrailResult, response_length: int) -> None:
        query_hash = hashlib.sha256(query.encode()).hexdigest()
        entry = GuardrailAuditLog(
            query_hash=query_hash,
            triggered_filter=result.triggered_filter,
            passed=result.passed,
            response_length=response_length,
            compliance_context=result.compliance_context,
        )
        self._db.add(entry)
        self._db.commit()

    def process(self, query: str, response: str) -> GuardrailResult:
        # 1. Investment advice check
        result = InvestmentAdviceFilter().check(query, response)
        if not result.passed:
            self._log(query, result, len(result.modified_response))
            return result

        # 2. Scope check
        result = ScopeFilter().check(query, response)
        if not result.passed:
            self._log(query, result, len(result.modified_response))
            return result

        # 3. Forecast disclaimer injection
        final_response = ForecastDisclaimerInjector().inject(result.modified_response)

        final_result = GuardrailResult(
            passed=True,
            triggered_filter=None,
            modified_response=final_response,
        )
        self._log(query, final_result, len(final_response))
        return final_result
