"""Jina AI embeddings client for pgvector RAG retrieval.
Free tier: 1M tokens/month — https://jina.ai
"""

import logging
from typing import Optional

import httpx

from src.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

JINA_ENDPOINT = "https://api.jina.ai/v1/embeddings"


async def embed_text(text: str) -> list[float]:
    """Generate a single embedding vector for the given text."""
    results = await embed_batch([text])
    return results[0]


async def embed_batch(texts: list[str]) -> list[list[float]]:
    """Generate embeddings for multiple texts in one API call."""
    if not texts:
        return []

    payload = {
        "model": settings.jina_embedding_model,
        "task": "retrieval.passage",
        "dimensions": settings.jina_embedding_dimensions,
        "embedding_type": "float",
        "input": texts,
    }
    headers = {
        "Authorization": f"Bearer {settings.jina_api_key}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(JINA_ENDPOINT, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    # API returns results sorted by index
    ordered = sorted(data["data"], key=lambda x: x["index"])
    return [item["embedding"] for item in ordered]


def build_gold_record_text(record) -> str:
    """Construct the text string embedded for a gold record."""
    parts = [
        f"Indicator: {record.indicator_code}",
        f"Country: {record.country_code}",
        f"Period: {record.period}",
        f"Value: {record.value} {record.standard_unit}",
        f"Source: {record.source_name}",
    ]
    if record.is_forecast:
        parts.append("Type: forecast/projection")
    return " | ".join(parts)
