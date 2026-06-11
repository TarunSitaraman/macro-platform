"""Unit tests for the flexible embeddings system."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.agents.embeddings import embed_batch, embed_text


@pytest.mark.asyncio
@patch("src.agents.embeddings.settings")
@patch("httpx.AsyncClient")
async def test_embed_batch_gemini_success(mock_client, mock_settings):
    """Test successful embedding generation using Gemini provider."""
    # Setup mock settings
    mock_settings.embedding_provider = "gemini"
    mock_settings.gemini_api_key = "valid_key"
    mock_settings.gemini_base_url = "https://generativelanguage.googleapis.com/v1beta/openai"
    mock_settings.gemini_embedding_model = "models/gemini-embedding-2"
    mock_settings.gemini_embedding_dimensions = 1024

    # Setup mock httpx client response
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "embeddings": [
            {"values": [0.1] * 1024},
            {"values": [0.2] * 1024}
        ]
    }
    
    # Mock AsyncClient.post context manager and request execution
    mock_client_instance = AsyncMock()
    mock_client_instance.post.return_value = mock_response
    mock_client.return_value.__aenter__.return_value = mock_client_instance

    texts = ["hello", "world"]
    vecs, model = await embed_batch(texts)

    assert model == "models/gemini-embedding-2"
    assert len(vecs) == 2
    assert len(vecs[0]) == 1024
    assert vecs[0][0] == 0.1
    assert vecs[1][0] == 0.2
    
    # Check that it tried Gemini
    mock_client_instance.post.assert_called_once()
    called_url = mock_client_instance.post.call_args[0][0]
    assert "gemini-embedding-2" in called_url


@pytest.mark.asyncio
@patch("src.agents.embeddings.settings")
@patch("httpx.AsyncClient")
async def test_embed_batch_jina_success(mock_client, mock_settings):
    """Test successful embedding generation using Jina provider."""
    # Setup mock settings
    mock_settings.embedding_provider = "jina"
    mock_settings.jina_api_key = "jina_valid_key"
    mock_settings.jina_embedding_model = "jina-embeddings-v3"
    mock_settings.jina_embedding_dimensions = 1024

    # Setup mock httpx client response
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "data": [
            {"index": 0, "embedding": [0.5] * 1024},
            {"index": 1, "embedding": [0.6] * 1024}
        ]
    }
    
    # Mock AsyncClient
    mock_client_instance = AsyncMock()
    mock_client_instance.post.return_value = mock_response
    mock_client.return_value.__aenter__.return_value = mock_client_instance

    texts = ["hello", "world"]
    vecs, model = await embed_batch(texts)

    assert model == "jina-embeddings-v3"
    assert len(vecs) == 2
    assert len(vecs[0]) == 1024
    assert vecs[0][0] == 0.5
    assert vecs[1][0] == 0.6


@pytest.mark.asyncio
@patch("src.agents.embeddings.settings")
@patch("httpx.AsyncClient")
async def test_embed_batch_fallback_to_jina(mock_client, mock_settings):
    """Test fallback to Jina when primary Gemini fails."""
    mock_settings.embedding_provider = "gemini"
    mock_settings.gemini_api_key = "valid_key"
    mock_settings.gemini_base_url = "https://generativelanguage.googleapis.com/v1beta/openai"
    mock_settings.gemini_embedding_model = "models/gemini-embedding-2"
    mock_settings.jina_api_key = "jina_valid_key"
    mock_settings.jina_embedding_model = "jina-embeddings-v3"
    mock_settings.jina_embedding_dimensions = 1024

    # Setup mocks: first call (Gemini) fails, second call (Jina) succeeds
    mock_resp_jina = MagicMock()
    mock_resp_jina.status_code = 200
    mock_resp_jina.json.return_value = {
        "data": [{"index": 0, "embedding": [0.8] * 1024}]
    }

    mock_client_instance = AsyncMock()
    # Mock side_effect to raise exception for Gemini URL, but return successful response for Jina
    async def post_side_effect(url, **kwargs):
        if "generativelanguage" in url:
            raise Exception("Gemini connection error")
        return mock_resp_jina

    mock_client_instance.post.side_effect = post_side_effect
    mock_client.return_value.__aenter__.return_value = mock_client_instance

    texts = ["test"]
    vecs, model = await embed_batch(texts)

    assert model == "jina-embeddings-v3"
    assert len(vecs) == 1
    assert vecs[0][0] == 0.8


@pytest.mark.asyncio
@patch("src.agents.embeddings.settings")
@patch("httpx.AsyncClient")
async def test_embed_batch_all_fail_mock_fallback(mock_client, mock_settings):
    """Test ultimate fallback to mock zero-vector embeddings when both providers fail."""
    mock_settings.embedding_provider = "gemini"
    mock_settings.gemini_api_key = "valid_key"
    mock_settings.gemini_base_url = "https://generativelanguage.googleapis.com/v1beta/openai"
    mock_settings.gemini_embedding_model = "models/gemini-embedding-2"
    mock_settings.gemini_embedding_dimensions = 1024
    mock_settings.jina_api_key = "jina_valid_key"
    mock_settings.jina_embedding_model = "jina-embeddings-v3"
    mock_settings.jina_embedding_dimensions = 1024

    mock_client_instance = AsyncMock()
    mock_client_instance.post.side_effect = Exception("General failure")
    mock_client.return_value.__aenter__.return_value = mock_client_instance

    texts = ["test"]
    vecs, model = await embed_batch(texts)

    assert model == "mock-zero-vector"
    assert len(vecs) == 1
    assert len(vecs[0]) == 1024
    assert vecs[0][0] == 0.0


@pytest.mark.asyncio
@patch("src.agents.embeddings.settings")
@patch("httpx.AsyncClient")
async def test_embed_text(mock_client, mock_settings):
    """Test the single text wrapper embed_text."""
    mock_settings.embedding_provider = "gemini"
    mock_settings.gemini_api_key = "valid_key"
    mock_settings.gemini_base_url = "https://generativelanguage.googleapis.com/v1beta/openai"
    mock_settings.gemini_embedding_model = "models/gemini-embedding-2"
    mock_settings.gemini_embedding_dimensions = 1024

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "embeddings": [{"values": [0.9] * 1024}]
    }

    mock_client_instance = AsyncMock()
    mock_client_instance.post.return_value = mock_response
    mock_client.return_value.__aenter__.return_value = mock_client_instance

    vec = await embed_text("hello")
    assert len(vec) == 1024
    assert vec[0] == 0.9
