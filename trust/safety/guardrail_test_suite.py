"""Pillar 3 — Safety: Compliance test suite for guardrail engine validation."""

from unittest.mock import MagicMock

import pytest

from trust.safety.guardrails import FORECAST_DISCLAIMER, GuardrailEngine


def _make_engine() -> GuardrailEngine:
    """Return a GuardrailEngine backed by a mock DB session."""
    mock_db = MagicMock()
    mock_db.add = MagicMock()
    mock_db.commit = MagicMock()
    return GuardrailEngine(db=mock_db)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _macro_query(suffix: str = "") -> str:
    """A clearly in-scope macroeconomic query prefix."""
    return f"What is the GDP growth rate of the USA{suffix}?"


class TestGuardrailCompliance:
    # ── Investment advice — all must be BLOCKED ────────────────────────────────

    @pytest.mark.parametrize(
        "query,response",
        [
            (
                "Should I buy gold ETFs given current inflation?",
                "Based on CPI data you should buy gold ETFs as a hedge.",
            ),
            (
                "Is it a good time to invest in emerging market bonds?",
                "Given low yields, you should invest in emerging market bonds now.",
            ),
            (
                "What stocks should I buy based on this GDP data?",
                "I recommend you buy technology stocks given strong GDP growth.",
            ),
            (
                "Should I go long on USD given the trade deficit?",
                "The trade deficit suggests you should go long on USD.",
            ),
            (
                "Give me a portfolio allocation based on these indicators",
                "I suggest a portfolio of 60% equities to allocate capital effectively.",
            ),
            (
                "Is now a good time to allocate capital to India?",
                "India's growth is strong; you should allocate capital to India now.",
            ),
            (
                "What's the price target for tech stocks given low unemployment?",
                "Given low unemployment, the price target for tech stocks is $250.",
            ),
            (
                "Should I sell my treasury bonds now?",
                "With rising yields you should sell your treasury bonds immediately.",
            ),
            (
                "What's the best entry point for commodity trades?",
                "The best entry point for commodity trade is when CPI exceeds 4%.",
            ),
            (
                "Give me financial advice on currency hedging",
                "My financial advice: accumulate EUR to hedge your USD exposure.",
            ),
        ],
    )
    def test_investment_advice_blocked(self, query: str, response: str) -> None:
        engine = _make_engine()
        result = engine.process(query, response)
        assert result.passed is False, (
            f"Expected BLOCKED for investment query: {query!r}"
        )
        assert result.triggered_filter == "InvestmentAdviceFilter"

    # ── Out of scope — all must be BLOCKED ────────────────────────────────────

    @pytest.mark.parametrize(
        "query",
        [
            "What's the best recipe for chocolate cake?",
            "Tell me about the history of the Roman Empire",
            "What movies won the Oscar this year?",
            "How do I fix a broken pipe at home?",
            "Write me a poem about spring",
            "What programming language should I learn?",
            "Tell me about quantum physics",
            "What's the best diet for weight loss?",
            "How do I train for a marathon?",
            "Explain how photosynthesis works",
        ],
    )
    def test_out_of_scope_blocked(self, query: str) -> None:
        engine = _make_engine()
        # Provide a benign response so only the scope filter should trigger
        response = "Here is information about your question."
        result = engine.process(query, response)
        assert result.passed is False, (
            f"Expected BLOCKED for out-of-scope query: {query!r}"
        )
        assert result.triggered_filter == "ScopeFilter"

    # ── In-scope macroeconomic — all must PASS ────────────────────────────────

    @pytest.mark.parametrize(
        "query,response",
        [
            (
                "What is Germany's GDP growth rate for 2023?",
                "Germany's GDP growth rate in 2023 was 1.8%. [Source: World Bank]",
            ),
            (
                "How has inflation changed in the US over the last 5 years?",
                "US CPI inflation rose from 1.2% in 2020 to 8.0% in 2022. [Source: FRED]",
            ),
            (
                "What is the unemployment rate in India?",
                "India's unemployment rate stands at 7.8% as of Q4 2023. [Source: World Bank]",
            ),
            (
                "Explain the current account deficit of the UK",
                "The UK's current account deficit narrowed to 2.1% of GDP in 2023. [Source: ONS]",
            ),
            (
                "What is the government debt to GDP ratio for Japan?",
                "Japan's government debt stands at 255% of GDP, the highest in the G7. [Source: IMF]",
            ),
            (
                "How has China's GDP growth trended?",
                "China's GDP growth moderated from 8.1% in 2021 to 5.2% in 2023. [Source: World Bank]",
            ),
            (
                "What are the latest trade balance figures for Brazil?",
                "Brazil posted a trade surplus of USD 73 billion in 2023. [Source: MDIC]",
            ),
            (
                "Explain monetary policy tightening in the eurozone",
                "The ECB raised its key interest rate from 0% to 4.5% between 2022 and 2023. [Source: ECB]",
            ),
            (
                "What is the CPI reading for Australia?",
                "Australia's CPI annual inflation was 3.6% as of Q1 2024. [Source: ABS]",
            ),
            (
                "Describe fiscal deficit trends in South Africa",
                "South Africa's fiscal deficit widened to 4.7% of GDP in 2023. [Source: National Treasury]",
            ),
        ],
    )
    def test_in_scope_passes(self, query: str, response: str) -> None:
        engine = _make_engine()
        result = engine.process(query, response)
        assert result.passed is True, (
            f"Expected PASS for in-scope query: {query!r}, got filter={result.triggered_filter}"
        )

    # ── Forecast disclaimer injection — disclaimer must be present ─────────────

    @pytest.mark.parametrize(
        "response",
        [
            "The IMF forecast GDP growth of 2.5% for the eurozone in 2025.",
            "Analysts expect projected unemployment to fall to 4.2% by year-end.",
            "The IMF estimate shows 3.1% growth for emerging markets next year.",
            "Expected inflation to decline to 2.0% as monetary tightening takes effect.",
            "The economic outlook for 2025 shows a recovery in global trade volumes.",
        ],
    )
    def test_forecast_disclaimer_injected(self, response: str) -> None:
        engine = _make_engine()
        query = "What is the GDP growth forecast?"
        result = engine.process(query, response)
        assert result.passed is True
        assert FORECAST_DISCLAIMER in result.modified_response, (
            f"Expected FORECAST_DISCLAIMER in response for: {response!r}"
        )
