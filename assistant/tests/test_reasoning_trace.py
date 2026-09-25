"""Behavioral test for reasoning trace persistence (Phase 4).

The remaining signature-inspection tests were dropped in the test audit —
they asserted on `inspect.signature()` output (brittle, no behavioral value;
one had no assertions at all). This test verifies the actual behavior: the
orchestrator forwards the reasoning trace to the episode store.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest


class TestReasoningTracePersistence:
    """Test reasoning trace is stored in episodes."""

    @pytest.mark.asyncio
    async def test_log_episode_passes_reasoning_trace(self):
        """Test that _log_episode passes reasoning_trace to create_episode."""
        from assistant.backend.pipeline.orchestrator import Orchestrator

        store = MagicMock()
        retriever = MagicMock()
        llm_client = MagicMock()
        search_tool = MagicMock()

        deps = MagicMock()
        deps.store = store
        deps.retriever = retriever
        deps.llm_client = llm_client
        deps.search_tool = search_tool

        orchestrator = Orchestrator(deps)

        store.create_episode = AsyncMock(return_value=MagicMock(id=1))
        store.store_episode_embedding = AsyncMock()

        await orchestrator._log_episode(
            user_id=1,
            session_id="test",
            role="assistant",
            content="test response",
            reasoning_trace="some reasoning",
        )

        # Verify create_episode was called with reasoning_trace
        call_args = store.create_episode.call_args
        assert call_args[1].get("reasoning_trace") == "some reasoning"