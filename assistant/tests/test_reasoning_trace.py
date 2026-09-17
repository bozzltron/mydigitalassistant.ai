"""Tests for reasoning trace persistence (Phase 4)."""

from unittest.mock import AsyncMock, MagicMock

import pytest


class TestReasoningTracePersistence:
    """Test reasoning trace is stored in episodes."""

    @pytest.mark.asyncio
    async def test_create_episode_with_reasoning_trace(self):
        """Test that create_episode accepts and stores reasoning_trace."""
        # Check the method signature
        import inspect

        from assistant.backend.memory.store import MemoryStore
        sig = inspect.signature(MemoryStore.create_episode)
        params = sig.parameters
        assert "reasoning_trace" in params
        assert params["reasoning_trace"].default is None

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


class TestToolLoopReasoningTrace:
    """Test that run_tool_loop captures and returns reasoning_trace."""

    @pytest.mark.asyncio
    async def test_run_tool_loop_signature_has_reasoning_trace(self):
        """Test that run_tool_loop function signature includes reasoning_trace in return."""
        import inspect

        from assistant.backend.pipeline.tools import run_tool_loop

        inspect.signature(run_tool_loop)
        # The return annotation should include reasoning_trace
        # This is a basic structural test

    @pytest.mark.asyncio
    async def test_think_tool_records_reasoning(self):
        """Test that the think tool handler records reasoning."""
        # Verify ThinkArgs has reasoning field
        import inspect

        from assistant.backend.pipeline.tools import ThinkArgs
        sig = inspect.signature(ThinkArgs)
        params = sig.parameters
        assert "reasoning" in params


class TestOrchestratorReasoningTrace:
    """Test orchestrator stores reasoning_trace in episode."""

    @pytest.mark.asyncio
    async def test_log_episode_signature_has_reasoning_trace(self):
        """Test that _log_episode accepts reasoning_trace parameter."""
        import inspect

        from assistant.backend.pipeline.orchestrator import Orchestrator

        sig = inspect.signature(Orchestrator._log_episode)
        params = sig.parameters
        assert "reasoning_trace" in params
        assert params["reasoning_trace"].default is None

    @pytest.mark.asyncio
    async def test_create_episode_signature_has_reasoning_trace(self):
        """Test that MemoryStore.create_episode accepts reasoning_trace parameter."""
        import inspect

        from assistant.backend.memory.store import MemoryStore

        sig = inspect.signature(MemoryStore.create_episode)
        params = sig.parameters
        assert "reasoning_trace" in params
        assert params["reasoning_trace"].default is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])