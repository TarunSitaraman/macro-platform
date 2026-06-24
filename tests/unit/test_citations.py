"""Unit tests for citation/context-record building from structured tool data."""

from src.agents.runtime.citations import build_citations, dedupe_records


def _gold(record_id="r1", source="IMF", indicator="GDP_GROWTH", country="USA", period="2023"):
    return {
        "record_id": record_id,
        "type": "gold",
        "source_name": source,
        "source_url": "https://example.org",
        "indicator_code": indicator,
        "country_code": country,
        "period": period,
        "value": 2.1,
        "unit": "%",
    }


def test_dedupe_records_by_record_id():
    records = [_gold(), _gold(), _gold(record_id="r2")]
    unique = dedupe_records(records)
    assert len(unique) == 2
    assert [r["record_id"] for r in unique] == ["r1", "r2"]


def test_dedupe_records_without_id_uses_composite_key():
    news_a = {"type": "news", "source_name": "Reuters", "title": "Fed holds rates", "period": "2024-01-01"}
    news_b = dict(news_a)  # identical → collapses
    news_c = {"type": "news", "source_name": "Reuters", "title": "Different story", "period": "2024-01-02"}
    unique = dedupe_records([news_a, news_b, news_c])
    assert len(unique) == 2


def test_build_citations_enriches_and_dedupes():
    records = [_gold(), _gold(record_id="r2")]  # same source/indicator/country/period
    citations = build_citations(records)
    assert len(citations) == 1
    c = citations[0]
    assert c["source_name"] == "IMF"
    assert c["indicator_code"] == "GDP_GROWTH"
    assert c["country_code"] == "USA"
    assert c["value"] == 2.1
    assert c["unit"] == "%"
    assert c["record_id"] == "r1"


def test_build_citations_keeps_distinct_sources():
    records = [_gold(), _gold(source="World Bank", record_id="r2")]
    citations = build_citations(records)
    assert {c["source_name"] for c in citations} == {"IMF", "World Bank"}


def test_build_citations_skips_records_without_source():
    records = [{"record_id": "x", "type": "gold", "indicator_code": "CPI"}]
    assert build_citations(records) == []


def test_build_citations_handles_news_titles():
    news = {"type": "news", "source_name": "Reuters", "title": "Fed holds rates", "period": "2024-01-01"}
    citations = build_citations([news])
    assert len(citations) == 1
    assert citations[0]["title"] == "Fed holds rates"
    assert citations[0]["type"] == "news"
