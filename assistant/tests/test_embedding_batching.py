"""Tests for embedding batching (Phase 3)."""

from unittest.mock import AsyncMock, MagicMock

import pytest


class TestEmbedBatching:
    """Test batched embedding API."""

    @pytest.mark.asyncio
    async def test_embed_single_string(self):
        """Test embedding a single string still works."""
        from assistant.backend.pipeline.llm_client import OllamaClient

        client = OllamaClient()
        client._get_client = AsyncMock()
        mock_http_client = AsyncMock()
        client._get_client.return_value = mock_http_client

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "embedding": [0.1, 0.2, 0.3],
        }
        mock_response.raise_for_status = MagicMock()
        mock_http_client.post = AsyncMock(return_value=mock_response)

        result = await client.embed("test text")
        assert isinstance(result, list) is False
        assert result.embedding == [0.1, 0.2, 0.3]

    @pytest.mark.asyncio
    async def test_embed_list_of_strings(self):
        """Test embedding a list of strings returns list of responses.
        
        Ollama doesn't support batch prompts, so this makes sequential calls.
        """
        from assistant.backend.pipeline.llm_client import OllamaClient

        client = OllamaClient()
        client._get_client = AsyncMock()
        mock_http_client = AsyncMock()
        client._get_client.return_value = mock_http_client

        # Sequential responses for each text
        responses = [
            {"embedding": [0.1, 0.2, 0.3]},
            {"embedding": [0.4, 0.5, 0.6]},
            {"embedding": [0.7, 0.8, 0.9]},
        ]
        mock_response = MagicMock()
        mock_response.json.side_effect = responses
        mock_response.raise_for_status = MagicMock()
        mock_http_client.post = AsyncMock(return_value=mock_response)

        texts = ["text1", "text2", "text3"]
        results = await client.embed(texts)

        assert isinstance(results, list)
        assert len(results) == 3
        assert results[0].embedding == [0.1, 0.2, 0.3]
        assert results[1].embedding == [0.4, 0.5, 0.6]
        assert results[2].embedding == [0.7, 0.8, 0.9]
        # Should have made 3 sequential API calls
        assert mock_http_client.post.call_count == 3

    @pytest.mark.asyncio
    async def test_embed_batch_caching(self):
        """Test that cached embeddings are returned without API call."""
        from assistant.backend.pipeline.llm_client import OllamaClient

        client = OllamaClient()
        client._embed_cache = {
            f"{client.embedding_model}:cached text": [0.5, 0.5, 0.5],
        }

        # Mix of cached and uncached
        texts = ["cached text", "new text"]

        client._get_client = AsyncMock()
        mock_http_client = AsyncMock()
        client._get_client.return_value = mock_http_client

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "embedding": [0.1, 0.2, 0.3],
        }
        mock_response.raise_for_status = MagicMock()
        mock_http_client.post = AsyncMock(return_value=mock_response)

        results = await client.embed(texts)

        assert len(results) == 2
        assert results[0].embedding == [0.5, 0.5, 0.5]  # from cache
        assert results[1].embedding == [0.1, 0.2, 0.3]  # from API

        # Verify only one API call for the uncached text
        assert mock_http_client.post.call_count == 1

    @pytest.mark.asyncio
    async def test_embed_batch_all_cached(self):
        """Test that all cached embeddings return without API call."""
        from assistant.backend.pipeline.llm_client import OllamaClient

        client = OllamaClient()
        client._embed_cache = {
            f"{client.embedding_model}:text1": [0.1, 0.1, 0.1],
            f"{client.embedding_model}:text2": [0.2, 0.2, 0.2],
        }
        client._get_client = AsyncMock()

        texts = ["text1", "text2"]
        results = await client.embed(texts)

        assert len(results) == 2
        assert results[0].embedding == [0.1, 0.1, 0.1]
        assert results[1].embedding == [0.2, 0.2, 0.2]

        # Verify no API call
        assert client._get_client.call_count == 0

    @pytest.mark.asyncio
    async def test_embed_batch_empty_list(self):
        """Test embedding empty list returns empty list."""
        from assistant.backend.pipeline.llm_client import OllamaClient

        client = OllamaClient()
        results = await client.embed([])
        assert results == []

    @pytest.mark.asyncio
    async def test_embed_batch_cache_trim(self):
        """Test that cache is trimmed when it grows too large."""
        from assistant.backend.pipeline.llm_client import OllamaClient

        client = OllamaClient()
        client._cache_max_size = 4
        # Pre-fill cache
        for i in range(4):
            client._embed_cache[f"{client.embedding_model}:text{i}"] = [float(i)] * 3

        client._get_client = AsyncMock()
        mock_http_client = AsyncMock()
        client._get_client.return_value = mock_http_client

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "embedding": [0.9, 0.9, 0.9],
        }
        mock_response.raise_for_status = MagicMock()
        mock_http_client.post = AsyncMock(return_value=mock_response)

        # Add one more to trigger trim
        results = await client.embed(["new text"])

        # Cache should be trimmed (oldest entries removed)
        assert len(client._embed_cache) <= client._cache_max_size
        assert results[0].embedding == [0.9, 0.9, 0.9]


class TestEmbedFramesBatch:
    """Test batched frame embedding storage."""

    @pytest.mark.asyncio
    async def test_embed_frames_batch(self):
        """Test storing pre-computed embeddings for frames."""
        from assistant.backend.memory.store import MemoryStore

        # This would require a real database connection
        # For now, just test the method exists
        assert hasattr(MemoryStore, "embed_frames_batch")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])