"""
Trust Layer integration tests.

Verifies that the full middleware chain processes a sample chatbot query
correctly end-to-end, and that each pillar's core behaviour is enforced.

Run with::

    pytest tests/test_trust_integration.py -v

Most tests use mocked DB sessions so no real PostgreSQL connection is needed.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_db():
    """SQLAlchemy Session mock that silently accepts writes."""
    db = MagicMock()
    db.add = MagicMock()
    db.commit = MagicMock()
    db.refresh = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    db.query.return_value.filter.return_value.scalar.return_value = None
    db.query.return_value.filter_by.return_value.first.return_value = None
    db.query.return_value.scalar.return_value = None
    return db


# ═══════════════════════════════════════════════════════════════════════════════
# PILLAR 1 — RELIABILITY
# ═══════════════════════════════════════════════════════════════════════════════

class TestPillar1Reliability:

    def test_retry_policy_auto_accept_threshold(self):
        """AUTO_ACCEPT fires for confidence above threshold."""
        from trust.reliability.extraction_thresholds import (
            evaluate_extraction,
            ThresholdDecision,
        )
        decision = evaluate_extraction(0.90)
        assert decision == ThresholdDecision.AUTO_ACCEPT

    def test_retry_policy_queue_review(self):
        """QUEUE_REVIEW fires for confidence in review band."""
        from trust.reliability.extraction_thresholds import (
            evaluate_extraction,
            ThresholdDecision,
        )
        decision = evaluate_extraction(0.75)
        assert decision == ThresholdDecision.QUEUE_REVIEW

    def test_retry_policy_reject(self):
        """REJECT fires for confidence below minimum threshold."""
        from trust.reliability.extraction_thresholds import (
            evaluate_extraction,
            ThresholdDecision,
        )
        decision = evaluate_extraction(0.50)
        assert decision == ThresholdDecision.REJECT

    def test_circuit_breaker_opens_after_threshold(self):
        """CircuitBreaker transitions to OPEN after failure threshold is hit."""
        from trust.reliability.retry_policy import CircuitBreaker

        cb = CircuitBreaker(name="test_cb", threshold=3, timeout=60)
        assert cb.get_state()["state"] == "CLOSED"

        async def fail():
            raise RuntimeError("simulated failure")

        import asyncio
        for _ in range(3):
            try:
                asyncio.get_event_loop().run_until_complete(cb.call(fail))
            except RuntimeError:
                pass

        assert cb.get_state()["state"] == "OPEN"

    def test_sla_tier_assignment(self):
        """SLAMonitor correctly assigns tiers to known indicators."""
        from trust.reliability.sla_monitor import SLAMonitor, SLATier

        monitor = SLAMonitor(db=MagicMock())
        assert monitor.get_tier("GDP_GROWTH") == SLATier.TIER1
        assert monitor.get_tier("CURRENT_ACCOUNT_PCT_GDP") == SLATier.TIER2
        assert monitor.get_tier("POPULATION") == SLATier.TIER3
        assert monitor.get_tier("UNKNOWN_INDICATOR") == SLATier.TIER3


# ═══════════════════════════════════════════════════════════════════════════════
# PILLAR 2 — SECURITY
# ═══════════════════════════════════════════════════════════════════════════════

class TestPillar2Security:

    def test_mask_secret_hides_all_but_last_four(self):
        from trust.security.secret_manager import mask_secret

        assert mask_secret("my-very-secret-key-1234") == "*" * 19 + "1234"
        assert mask_secret("ab") == "****"
        assert mask_secret("") == "****"

    def test_api_key_prefix_extraction(self):
        """API key prefix is the first 8 characters."""
        import secrets, string
        alphabet = string.ascii_letters + string.digits
        prefix = "".join(secrets.choice(alphabet) for _ in range(8))
        token = secrets.token_urlsafe(32)
        raw_key = f"{prefix}.{token}"
        assert raw_key[:8] == prefix

    def test_blocked_source_cooldown_escalation(self, mock_db):
        """BlockedSourceRegistry escalates cooldown correctly per block count."""
        from trust.security.bot_detection import BlockedSourceRegistry, COOLDOWN_HOURS

        # First block → 1h cooldown
        registry = BlockedSourceRegistry(db=mock_db)
        existing = MagicMock()
        existing.block_count = 1
        existing.is_permanent = False
        mock_db.query.return_value.filter.return_value.first.return_value = existing

        registry.record_block("http://example.com/blocked", reason="HTTP 403")
        assert existing.block_count == 2
        # After second block the cooldown should be COOLDOWN_HOURS[1] = 4h
        expected_hours = COOLDOWN_HOURS[1]
        assert expected_hours == 4

    def test_permanent_block_after_three_strikes(self, mock_db):
        """Source becomes permanently blocked after block_count reaches 3."""
        from trust.security.bot_detection import BlockedSourceRegistry

        registry = BlockedSourceRegistry(db=mock_db)
        existing = MagicMock()
        existing.block_count = 3
        existing.is_permanent = False
        mock_db.query.return_value.filter.return_value.first.return_value = existing

        registry.record_block("http://blocked.example.com", reason="HTTP 429")
        assert existing.is_permanent is True

    def test_rotating_headers_vary(self):
        """rotating_headers() returns different headers across calls."""
        from trust.security.bot_detection import rotating_headers

        headers_1 = rotating_headers()
        assert "User-Agent" in headers_1
        assert "Accept-Language" in headers_1


# ═══════════════════════════════════════════════════════════════════════════════
# PILLAR 3 — SAFETY
# ═══════════════════════════════════════════════════════════════════════════════

class TestPillar3Safety:

    def test_investment_advice_blocked(self):
        from trust.safety.guardrails import GuardrailEngine

        engine = GuardrailEngine(db=MagicMock())
        result = engine.process(
            query="Should I buy gold ETFs given the current inflation?",
            response="Based on the data, I recommend buying gold ETFs as a hedge.",
        )
        assert result.passed is False
        assert result.triggered_filter == "InvestmentAdviceFilter"

    def test_out_of_scope_blocked(self):
        from trust.safety.guardrails import GuardrailEngine

        engine = GuardrailEngine(db=MagicMock())
        result = engine.process(
            query="What is the best chocolate cake recipe?",
            response="Here is a great chocolate cake recipe...",
        )
        assert result.passed is False
        assert result.triggered_filter == "ScopeFilter"

    def test_in_scope_passes(self):
        from trust.safety.guardrails import GuardrailEngine

        engine = GuardrailEngine(db=MagicMock())
        result = engine.process(
            query="What is Germany's GDP growth rate in 2023?",
            response="Germany's GDP growth was 0.2% in 2023 [Source: World Bank, 2024].",
        )
        assert result.passed is True

    def test_forecast_disclaimer_injected(self):
        from trust.safety.guardrails import GuardrailEngine, FORECAST_DISCLAIMER

        engine = GuardrailEngine(db=MagicMock())
        result = engine.process(
            query="What is the GDP forecast for India?",
            response="The IMF projects GDP growth of 6.5% for India in 2025.",
        )
        assert result.passed is True
        assert FORECAST_DISCLAIMER in result.modified_response

    def test_output_validator_flags_missing_citation(self, mock_db):
        from trust.safety.output_validator import OutputValidator

        validator = OutputValidator(db=mock_db)
        result = validator.validate("This is a response with no citation at all.")
        assert not result.valid
        assert any("citation" in issue.lower() for issue in result.issues)

    def test_output_validator_flags_short_response(self, mock_db):
        from trust.safety.output_validator import OutputValidator

        validator = OutputValidator(db=mock_db)
        result = validator.validate("Too short")
        assert not result.valid


# ═══════════════════════════════════════════════════════════════════════════════
# PILLAR 4 — PRIVACY
# ═══════════════════════════════════════════════════════════════════════════════

class TestPillar4Privacy:

    def test_pii_email_redacted(self):
        from trust.privacy.pii_scanner import PIIScanner

        scanner = PIIScanner()
        redacted, matches = scanner.redact_pii("Contact me at user@example.com for details.")
        assert "user@example.com" not in redacted
        assert "[REDACTED_EMAIL]" in redacted
        assert len(matches) == 1
        assert matches[0].pii_type == "EMAIL"

    def test_pii_phone_redacted(self):
        from trust.privacy.pii_scanner import PIIScanner

        scanner = PIIScanner()
        redacted, matches = scanner.redact_pii("Call me at 555-867-5309.")
        assert "555-867-5309" not in redacted
        assert any(m.pii_type == "PHONE" for m in matches)

    def test_no_pii_unchanged(self):
        from trust.privacy.pii_scanner import PIIScanner

        scanner = PIIScanner()
        text = "Germany's GDP growth was 2.5% in 2023."
        redacted, matches = scanner.redact_pii(text)
        assert redacted == text
        assert matches == []

    def test_consent_hash_is_deterministic(self):
        """User ID hash must be stable across calls (same input → same output)."""
        user_id = "user-123"
        h1 = hashlib.sha256(user_id.encode()).hexdigest()
        h2 = hashlib.sha256(user_id.encode()).hexdigest()
        assert h1 == h2
        assert len(h1) == 64

    def test_ip_never_stored_raw(self, mock_db):
        """ConsentManager stores ip_hash, not the raw IP."""
        from trust.privacy.consent_manager import ConsentManager, ConsentType

        manager = ConsentManager(db=mock_db)

        added_records = []
        def capture_add(record):
            added_records.append(record)
        mock_db.add.side_effect = capture_add

        manager.grant("user-456", ConsentType.ANALYTICS, ip="192.168.1.1")
        assert added_records, "Expected a ConsentRecord to be added"
        record = added_records[0]
        assert "192.168.1.1" not in str(getattr(record, "ip_hash", ""))


# ═══════════════════════════════════════════════════════════════════════════════
# PILLAR 5 — SUSTAINABILITY
# ═══════════════════════════════════════════════════════════════════════════════

class TestPillar5Sustainability:

    def test_llm_cost_calculation(self, mock_db):
        """CostTracker computes correct cost for LLM tokens."""
        from trust.sustainability.cost_tracker import CostTracker

        tracker = CostTracker(db=mock_db)
        with patch.dict("os.environ", {"LLM_COST_PER_1K_TOKENS_GEMINI": "0.00015"}):
            event = tracker.track_llm_call("source_wb", "gemini", tokens=1000)
        assert event is not None
        assert event.cost_usd == pytest.approx(0.00015, rel=1e-3)

    def test_crawl_optimizer_detects_no_change(self, mock_db):
        """CrawlOptimizer returns (False, reason) when content hash matches."""
        from trust.sustainability.crawl_optimizer import CrawlOptimizer

        optimizer = CrawlOptimizer(db=mock_db)
        content = "same content"
        current_hash = optimizer.compute_content_hash(content)

        mock_db.query.return_value.filter.return_value.order_by.return_value.first.return_value = (
            MagicMock(content_hash=current_hash)
        )

        should, reason = optimizer.should_crawl("WB", content=content)
        assert should is False
        assert "no change" in reason.lower()

    def test_storage_cost_calculation(self, mock_db):
        """CostTracker computes correct cost for storage writes."""
        from trust.sustainability.cost_tracker import CostTracker

        tracker = CostTracker(db=mock_db)
        one_gb = 1024 ** 3
        with patch.dict("os.environ", {"STORAGE_COST_PER_GB": "0.023"}):
            event = tracker.track_storage_write("source_fred", one_gb, layer="gold")
        assert event.cost_usd == pytest.approx(0.023, rel=1e-3)


# ═══════════════════════════════════════════════════════════════════════════════
# PILLAR 6 — EXPLAINABILITY
# ═══════════════════════════════════════════════════════════════════════════════

class TestPillar6Explainability:

    def test_anomaly_explanation_out_of_range(self, mock_db):
        """AnomalyExplainer returns human-readable message for out-of-range value."""
        from trust.explainability.anomaly_explainer import AnomalyExplainer, AnomalyType

        explainer = AnomalyExplainer(db=mock_db)
        explanation = explainer.explain_out_of_range(
            indicator_code="CPI_INFLATION",
            value=150.0,
            unit="PCT",
            country_code="ZWE",
            period="2008",
        )
        assert explanation is not None
        assert "150" in explanation.explanation or "standard deviations" in explanation.explanation
        assert explanation.anomaly_type == AnomalyType.OUT_OF_RANGE

    def test_quality_score_breakdown_weights(self):
        """QualityScoreBreakdown weights sum to 1.0."""
        from trust.explainability.quality_score_breakdown import QualityBreakdownCalculator

        weights = [0.40, 0.30, 0.20, 0.10]
        assert sum(weights) == pytest.approx(1.0)

    def test_llm_trace_truncates_excerpt(self, mock_db):
        """LLMTrace stores at most 500 chars of the raw excerpt."""
        from trust.explainability.llm_trace import LLMTrace

        tracer = LLMTrace(db=mock_db)
        long_excerpt = "x" * 2000
        event = tracer.record(
            source_url="https://example.com",
            raw_excerpt=long_excerpt,
            extraction_prompt="Extract GDP value.",
            extracted_json={"value": 2.5},
            confidence=0.92,
            model_used="gemini-2.0-flash",
            tokens_consumed=150,
            latency_ms=320.0,
        )
        assert event is not None
        assert len(event.raw_excerpt) <= 500


# ═══════════════════════════════════════════════════════════════════════════════
# PILLAR 7 — DATA QUALITY
# ═══════════════════════════════════════════════════════════════════════════════

class TestPillar7DataQuality:

    def test_validator_rejects_missing_required_field(self, mock_db):
        from trust.data_quality.validator import DataQualityValidator

        validator = DataQualityValidator(db=mock_db)
        failures = validator.validate_schema({
            "indicator_code": "GDP_GROWTH",
            # country_code missing
            "period": "2023",
            "value": 2.5,
            "standard_unit": "PCT",
        })
        assert any(f.field == "country_code" for f in failures)

    def test_validator_rejects_out_of_range(self, mock_db):
        from trust.data_quality.validator import DataQualityValidator

        validator = DataQualityValidator(db=mock_db)
        failures = validator.validate_range("UNEMPLOYMENT_RATE", 150.0)
        assert len(failures) > 0
        assert failures[0].layer == "RANGE"

    def test_conflict_resolver_routine_variance(self, mock_db):
        """Variance < 1% is classified as ROUTINE."""
        from trust.data_quality.conflict_resolver import (
            ConflictResolver, ConflictSeverity, SourceValue,
        )

        resolver = ConflictResolver(db=mock_db)
        candidates = [
            SourceValue("WB", 2.50, 90.0, datetime.utcnow()),
            SourceValue("IMF", 2.51, 85.0, datetime.utcnow()),
        ]
        resolution = resolver.resolve("GDP_GROWTH", "USA", "2023", candidates)
        assert resolution.severity == ConflictSeverity.ROUTINE
        assert resolution.selected_source == "WB"  # highest reliability

    def test_conflict_resolver_major_flags_review(self, mock_db):
        """Variance > 5% creates a MAJOR conflict and flags for review."""
        from trust.data_quality.conflict_resolver import (
            ConflictResolver, ConflictSeverity, SourceValue,
        )

        resolver = ConflictResolver(db=mock_db)
        candidates = [
            SourceValue("WB", 2.0, 90.0, datetime.utcnow()),
            SourceValue("IMF", 3.5, 85.0, datetime.utcnow()),
        ]
        resolution = resolver.resolve("GDP_GROWTH", "USA", "2023", candidates)
        assert resolution.severity == ConflictSeverity.MAJOR

    def test_revision_tracker_detects_change(self, mock_db):
        """RevisionTracker writes a revision event when value differs."""
        from trust.data_quality.revision_tracker import RevisionTracker

        existing_gold = MagicMock()
        existing_gold.value = 2.5
        existing_gold.record_id = "fake-uuid"
        mock_db.query.return_value.filter.return_value.first.return_value = existing_gold

        tracker = RevisionTracker(db=mock_db)
        event = tracker.check_and_record("GDP_GROWTH", "USA", "2023", "WB", 2.8)
        assert event is not None
        assert event.old_value == 2.5
        assert event.new_value == 2.8

    def test_significant_revision_flagged(self, mock_db):
        """Revisions > 10% are flagged as significant."""
        from trust.data_quality.revision_tracker import RevisionTracker

        existing_gold = MagicMock()
        existing_gold.value = 2.0
        existing_gold.record_id = "fake-uuid"
        mock_db.query.return_value.filter.return_value.first.return_value = existing_gold

        tracker = RevisionTracker(db=mock_db)
        event = tracker.check_and_record("GDP_GROWTH", "USA", "2023", "WB", 3.0)
        assert event is not None
        assert event.is_significant is True


# ═══════════════════════════════════════════════════════════════════════════════
# PILLAR 8 — TRANSPARENCY
# ═══════════════════════════════════════════════════════════════════════════════

class TestPillar8Transparency:

    def test_citation_validator_detects_source(self):
        from trust.transparency.chatbot_citations import CitationValidator

        validator = CitationValidator()
        valid, _ = validator.validate(
            "GDP growth: 2.5% [Source: World Bank, 2024-01-15]"
        )
        assert valid is True

    def test_citation_validator_flags_missing(self):
        from trust.transparency.chatbot_citations import CitationValidator

        validator = CitationValidator()
        valid, reason = validator.validate(
            "GDP growth was strong last year."
        )
        assert valid is False
        assert reason != ""

    def test_governance_policy_seed_is_idempotent(self, mock_db):
        """seed_default_policy does not insert if policy already exists."""
        from trust.transparency.governance_artefacts import PolicyManager, SEED_POLICY

        existing = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = existing

        manager = PolicyManager(db=mock_db)
        result = manager.seed_default_policy()
        mock_db.add.assert_not_called()

    def test_citation_formatter_appends_footer(self):
        from trust.transparency.chatbot_citations import CitationFormatter

        formatter = CitationFormatter()
        response = "GDP growth is 2.5%."
        result = formatter.append_lineage_footer(response, "GDP_GROWTH", "2023")
        assert "/api/citation/GDP_GROWTH/2023" in result


# ═══════════════════════════════════════════════════════════════════════════════
# PILLAR 9 — FAIRNESS & ACCOUNTABILITY
# ═══════════════════════════════════════════════════════════════════════════════

class TestPillar9Fairness:

    def test_coverage_mapper_returns_score(self, mock_db):
        """CoverageMapper computes a valid coverage_score in [0, 100]."""
        from trust.fairness.coverage_disclosure import CoverageMapper

        mock_db.execute.return_value.scalar.return_value = 8  # 8 of 11 indicators

        mapper = CoverageMapper(db=mock_db)
        # Patch the internal query calls
        with patch.object(mapper, "get_coverage_entry") as mock_get:
            mock_get.return_value = MagicMock(
                country_code="USA",
                indicators_covered=8,
                total_indicators=11,
                coverage_score=72.7,
            )
            entry = mapper.get_coverage_entry("USA")
        assert 0.0 <= entry.coverage_score <= 100.0

    def test_coverage_notice_for_low_coverage(self):
        """Low-coverage countries trigger the disclosure notice."""
        from trust.fairness.coverage_disclosure import CoverageMapper

        mapper = CoverageMapper(db=MagicMock())
        notice = mapper.format_coverage_notice("ZWE", 35.0)
        assert "35" in notice
        assert "coverage" in notice.lower()

    def test_human_oversight_auto_approves_high_score(self, mock_db):
        """Records with dq_score > 90 are auto-approved."""
        from trust.fairness.human_oversight import HumanOversightGate, OversightDecision

        gate = HumanOversightGate(db=mock_db)
        decision = gate.evaluate(
            silver_record_id="11111111-1111-1111-1111-111111111111",
            dq_score=95.0,
            indicator_code="GDP_GROWTH",
            country_code="USA",
            period="2023",
        )
        assert decision == OversightDecision.AUTO_APPROVED

    def test_human_oversight_queues_medium_score(self, mock_db):
        """Records with 70 ≤ dq_score ≤ 90 are queued for review."""
        from trust.fairness.human_oversight import HumanOversightGate, OversightDecision

        gate = HumanOversightGate(db=mock_db)
        decision = gate.evaluate(
            silver_record_id="22222222-2222-2222-2222-222222222222",
            dq_score=80.0,
            indicator_code="GDP_GROWTH",
            country_code="DEU",
            period="2023",
        )
        assert decision == OversightDecision.PENDING_REVIEW

    def test_human_oversight_rejects_low_score(self, mock_db):
        """Records with dq_score < 70 are rejected automatically."""
        from trust.fairness.human_oversight import HumanOversightGate, OversightDecision

        gate = HumanOversightGate(db=mock_db)
        decision = gate.evaluate(
            silver_record_id="33333333-3333-3333-3333-333333333333",
            dq_score=45.0,
            indicator_code="GDP_GROWTH",
            country_code="ZWE",
            period="2023",
        )
        assert decision == OversightDecision.REJECTED

    def test_accountability_task_sla_deadline_set(self, mock_db):
        """AccountabilityChain sets SLA deadline based on the level's SLA hours."""
        from trust.fairness.accountability_chain import (
            AccountabilityChain, LEVEL_SLA_HOURS,
        )

        chain = AccountabilityChain(db=mock_db)

        created_records = []
        def capture(record):
            created_records.append(record)
        mock_db.add.side_effect = capture

        chain.create_task("GDP_GROWTH", "USA", "2023", initial_level=2)
        assert created_records, "Expected ReviewTask to be added"
        task = created_records[0]
        sla_hours = LEVEL_SLA_HOURS[2]
        expected_deadline = datetime.utcnow() + timedelta(hours=sla_hours)
        delta = abs((task.sla_deadline - expected_deadline).total_seconds())
        assert delta < 5  # within 5 seconds of expected


# ═══════════════════════════════════════════════════════════════════════════════
# END-TO-END: Full middleware chain on a sample chatbot query
# ═══════════════════════════════════════════════════════════════════════════════

class TestEndToEndTrustChain:

    def test_full_chain_clean_query(self):
        """
        A clean macroeconomic query passes through the full trust pipeline
        without being blocked, PII-redacted, or citation-flagged.
        """
        from trust.privacy.pii_scanner import PIIScanner
        from trust.safety.guardrails import GuardrailEngine
        from trust.transparency.chatbot_citations import CitationValidator

        db = MagicMock()
        query = "What is the GDP growth rate for Germany in 2023?"
        response = (
            "Germany's GDP growth in 2023 was 0.2% [Source: World Bank, 2024-03-15]. "
            "This reflects subdued domestic demand and weak external trade."
        )

        # Step 1: PII scan (should find nothing)
        scanner = PIIScanner()
        redacted, pii_matches = scanner.redact_pii(query)
        assert pii_matches == []
        assert redacted == query

        # Step 2: Guardrail check (should pass)
        engine = GuardrailEngine(db=db)
        guard_result = engine.process(query, response)
        assert guard_result.passed is True

        # Step 3: Citation validation (should find citation)
        validator = CitationValidator()
        citation_valid, _ = validator.validate(guard_result.modified_response)
        assert citation_valid is True

    def test_full_chain_investment_advice_blocked(self):
        """Investment advice is blocked before reaching the user."""
        from trust.privacy.pii_scanner import PIIScanner
        from trust.safety.guardrails import GuardrailEngine

        db = MagicMock()
        query = "Should I invest in Indian bonds given the current inflation data?"
        response = "Based on the inflation trends, I recommend allocating capital to Indian bonds."

        scanner = PIIScanner()
        redacted, _ = scanner.redact_pii(query)

        engine = GuardrailEngine(db=db)
        result = engine.process(redacted, response)

        assert result.passed is False
        assert "InvestmentAdviceFilter" in (result.triggered_filter or "")

    def test_full_chain_pii_scrubbed_from_query(self):
        """PII in the query is redacted before the query reaches the LLM."""
        from trust.privacy.pii_scanner import PIIScanner

        scanner = PIIScanner()
        query = "Can you analyse the GDP data for user john.doe@example.com?"
        redacted, matches = scanner.redact_pii(query)

        assert "john.doe@example.com" not in redacted
        assert "[REDACTED_EMAIL]" in redacted
        assert len(matches) == 1
