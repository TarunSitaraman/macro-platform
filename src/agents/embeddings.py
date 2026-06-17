"""Jina AI & Google Gemini embeddings client for pgvector RAG retrieval."""

import asyncio
import logging
from typing import Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception

from src.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

JINA_ENDPOINT = "https://api.jina.ai/v1/embeddings"


class EmbeddingError(Exception):
    """Raised when embedding generation fails and no valid fallback is available."""


def _is_valid_key(key: str) -> bool:
    """Check if the API key is provided and is not a placeholder."""
    if not key:
        return False
    placeholders = ["your_key", "mock", "placeholder", "change_me", "change-me"]
    return not any(p in key.lower() for p in placeholders)


async def _embed_jina(texts: list[str]) -> list[list[float]]:
    """Helper to generate embeddings using Jina AI API."""
    api_key = settings.jina_api_key
    if not _is_valid_key(api_key):
        raise ValueError("Jina API key is missing or placeholder.")

    payload = {
        "model": settings.jina_embedding_model,
        "task": "retrieval.passage",
        "dimensions": settings.jina_embedding_dimensions,
        "embedding_type": "float",
        "input": texts,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(JINA_ENDPOINT, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    ordered = sorted(data["data"], key=lambda x: x["index"])
    return [item["embedding"] for item in ordered]


def _is_retryable_exception(exc: Exception) -> bool:
    """Determine if the exception is retryable (429/5xx status or request errors)."""
    if isinstance(exc, httpx.HTTPStatusError):
        # Retry on 429 (Too Many Requests) or 5xx (Server errors)
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    if isinstance(exc, (httpx.RequestError, ConnectionError, OSError)):
        return True
    return False


@retry(
    retry=retry_if_exception(_is_retryable_exception),
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1.5, min=2, max=15),
    reraise=True,
)
async def _post_gemini_chunk_with_retry(client: httpx.AsyncClient, url: str, payload: dict) -> httpx.Response:
    """Post to Gemini API with exponential backoff on retryable errors."""
    resp = await client.post(url, json=payload)
    resp.raise_for_status()
    return resp


async def _embed_gemini(texts: list[str]) -> list[list[float]]:
    """Helper to generate embeddings using Google Gemini REST API.
    
    Splits the request into chunks of max 100 texts to respect Gemini's 
    synchronous batch limit and prevent 400 Bad Request errors.
    """
    api_key = settings.gemini_api_key
    if not _is_valid_key(api_key):
        raise ValueError("Gemini API key is missing or placeholder.")

    base_url = settings.gemini_base_url.replace("/openai", "")
    model_name = settings.gemini_embedding_model
    url = f"{base_url}/models/{model_name.split('/')[-1]}:batchEmbedContents?key={api_key}"

    # Chunk into sub-batches of 100 (Gemini synchronous batch limit is 100 requests)
    chunk_size = 100
    chunks = [texts[i : i + chunk_size] for i in range(0, len(texts), chunk_size)]

    all_embeddings = []
    async with httpx.AsyncClient(timeout=40.0) as client:
        for index, chunk in enumerate(chunks):
            if index > 0:
                # Add a brief delay between chunks to respect rate limit guidelines
                await asyncio.sleep(0.5)

            requests = []
            for text in chunk:
                requests.append({
                    "model": model_name if model_name.startswith("models/") else f"models/{model_name}",
                    "content": {
                        "parts": [{"text": text}]
                    },
                    "outputDimensionality": settings.gemini_embedding_dimensions
                })
            payload = {"requests": requests}

            resp = await _post_gemini_chunk_with_retry(client, url, payload)
            data = resp.json()

            embeddings = data.get("embeddings", [])
            if len(embeddings) != len(chunk):
                raise ValueError(f"Gemini API returned {len(embeddings)} embeddings for {len(chunk)} inputs.")
            
            all_embeddings.extend([emb["values"] for emb in embeddings])

    return all_embeddings


async def embed_text(text: str) -> list[float]:
    """Generate a single embedding vector for the given text."""
    results, _ = await embed_batch([text])
    return results[0]


async def embed_batch(texts: list[str]) -> tuple[list[list[float]], str]:
    """Generate embeddings for multiple texts in one batch API call.
    
    Tries the configured embedding provider first. If it fails, falls back to the
    alternate provider. If both fail/are unavailable, falls back to zero-vector embeddings.
    
    Returns a tuple of (embeddings_list, model_name_used).
    """
    if not texts:
        return [], ""

    provider = settings.embedding_provider.lower()

    # Try primary provider
    try:
        if provider == "gemini":
            logger.info("Attempting to generate embeddings with Gemini (%s)", settings.gemini_embedding_model)
            vecs = await _embed_gemini(texts)
            return vecs, settings.gemini_embedding_model
        elif provider == "jina":
            logger.info("Attempting to generate embeddings with Jina (%s)", settings.jina_embedding_model)
            vecs = await _embed_jina(texts)
            return vecs, settings.jina_embedding_model
    except Exception as exc:
        logger.warning(
            "Primary embedding provider %s failed: %s. Trying fallback...",
            provider,
            exc
        )

    # Fallback to the other provider
    fallback_provider = "jina" if provider == "gemini" else "gemini"
    try:
        if fallback_provider == "gemini":
            logger.info("Attempting fallback to Gemini (%s)", settings.gemini_embedding_model)
            vecs = await _embed_gemini(texts)
            return vecs, settings.gemini_embedding_model
        elif fallback_provider == "jina":
            logger.info("Attempting fallback to Jina (%s)", settings.jina_embedding_model)
            vecs = await _embed_jina(texts)
            return vecs, settings.jina_embedding_model
    except Exception as exc:
        logger.warning(
            "Fallback embedding provider %s failed: %s. Falling back to mock zero-vector embeddings.",
            fallback_provider,
            exc
        )

    # Ultimate fallback — fail loudly in production; allow dev zero-vectors for offline work
    dimensions = (
        settings.gemini_embedding_dimensions
        if provider == "gemini"
        else settings.jina_embedding_dimensions
    )
    if settings.app_env == "production":
        raise EmbeddingError(
            f"All embedding providers failed for provider={provider}. "
            "Cannot perform vector search without valid embeddings."
        )
    logger.error(
        "All embedding providers failed — using zero-vector fallback (development only)"
    )
    return [[0.0] * dimensions for _ in texts], "mock-zero-vector"


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
