"""Tests for think flag logic fix (Phase 2)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestSupportsThinking:
    """Test supports_thinking method."""

    @pytest.mark.asyncio
    async def test_supports_thinking_returns_true_for_thinking_model(self):
        from assistant.backend.pipeline.llm_client import OllamaClient

        client = OllamaClient()
        client.model_capabilities = AsyncMock(return_value=["thinking", "tools"])

        result = await client.supports_thinking("qwen3:27b")
        assert result is True

    @pytest.mark.asyncio
    async def test_supports_thinking_returns_false_for_non_thinking_model(self):
        from assistant.backend.pipeline.llm_client import OllamaClient

        client = OllamaClient()
        client.model_capabilities = AsyncMock(return_value=["tools", "completion"])

        result = await client.supports_thinking("qwen2.5:7b")
        assert result is False

    @pytest.mark.asyncio
    async def test_supports_thinking_returns_false_on_error(self):
        from assistant.backend.pipeline.llm_client import OllamaClient

        client = OllamaClient()
        client.model_capabilities = AsyncMock(side_effect=Exception("API error"))

        result = await client.supports_thinking("some_model")
        assert result is False


class TestChatThinkFlag:
    """Test think flag handling in chat method."""

    @pytest.mark.asyncio
    async def test_think_false_when_model_does_not_support_thinking(self):
        from assistant.backend.pipeline.llm_client import ChatMessage, OllamaClient

        client = OllamaClient()
        client._get_client = AsyncMock()
        mock_http_client = AsyncMock()
        client._get_client.return_value = mock_http_client

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "model": "qwen2.5:7b",
            "message": {"content": "Hello", "thinking": ""},
            "done": True,
        }
        mock_response.raise_for_status = MagicMock()
        mock_http_client.post = AsyncMock(return_value=mock_response)

        client.model_capabilities = AsyncMock(return_value=["tools", "completion"])

        await client.chat(
            messages=[ChatMessage(role="user", content="Hello")],
            model="qwen2.5:7b",
            think=True,
        )

        call_args = mock_http_client.post.call_args
        payload = call_args[1]["json"]
        assert "think" not in payload or payload.get("think") is False

    @pytest.mark.asyncio
    async def test_think_true_when_model_supports_thinking(self):
        from assistant.backend.pipeline.llm_client import ChatMessage, OllamaClient

        client = OllamaClient()
        client._get_client = AsyncMock()
        mock_http_client = AsyncMock()
        client._get_client.return_value = mock_http_client

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "model": "qwen3:27b",
            "message": {"content": "Hello", "thinking": "thinking..."},
            "done": True,
        }
        mock_response.raise_for_status = MagicMock()
        mock_http_client.post = AsyncMock(return_value=mock_response)

        client.model_capabilities = AsyncMock(return_value=["thinking", "tools", "completion"])

        await client.chat(
            messages=[ChatMessage(role="user", content="Hello")],
            model="qwen3:27b",
            think=True,
        )

        call_args = mock_http_client.post.call_args
        payload = call_args[1]["json"]
        assert payload.get("think") is True


class TestOrchestratorThinkLogic:
    """Test orchestrator think flag logic."""

    @staticmethod
    def _setup_orchestrator(chat_model: str, supports_thinking: bool, plan_think: bool):
        from assistant.backend.pipeline.orchestrator import Orchestrator

        store = MagicMock()
        retriever = MagicMock()
        llm_client = MagicMock()
        llm_client.chat_model = chat_model
        llm_client.supports_thinking = AsyncMock(return_value=supports_thinking)
        llm_client.math_model = ""
        llm_client.chat = AsyncMock(return_value=MagicMock(content="test", thinking=""))
        search_tool = MagicMock()

        deps = MagicMock()
        deps.store = store
        deps.retriever = retriever
        deps.llm_client = llm_client
        deps.search_tool = search_tool

        orchestrator = Orchestrator(deps)

        store.create_episode = AsyncMock(return_value=MagicMock(id=1))
        store.get_episodes_for_session = AsyncMock(return_value=[])
        retriever.retrieve = AsyncMock(
            return_value=MagicMock(formatted="", retrieved_frames=[], recent_episodes=[])
        )

        plan = MagicMock()
        plan.think = plan_think
        plan.search_needed = False
        plan.action = MagicMock(value="answer")

        # Use individual patches instead of a list for easier management
        patches = {}
        patches["classify_intent"] = patch(
            "assistant.backend.pipeline.orchestrator.classify_intent",
            return_value=plan,
        )
        patches["route"] = patch(
            "assistant.backend.pipeline.orchestrator.route",
            return_value=MagicMock(
                task_type=MagicMock(value="functional"),
                wants_search=False,
                search_query=None,
            ),
        )
        patches["store_turn_memory"] = patch(
            "assistant.backend.pipeline.orchestrator.store_turn_memory",
            return_value={},
        )
        patches["format_plan_for_prompt"] = patch(
            "assistant.backend.pipeline.orchestrator.format_plan_for_prompt",
            return_value="",
        )
        patches["build_system_prompt"] = patch(
            "assistant.backend.pipeline.orchestrator.build_system_prompt",
            return_value="system",
        )
        patches["settings"] = patch("assistant.backend.pipeline.orchestrator.settings")

        mock_settings = patches["settings"].start()
        mock_settings.tools_enabled = True
        mock_settings.max_system_prompt_chars = 12000
        mock_settings.chat_think_default = True  # Default to True for thinking models
        mock_settings.think_num_predict_cap = 4096
        mock_settings.max_search_results_in_prompt = 3
        mock_settings.brave_search_min_relevance = 0.20
        mock_settings.search_min_relevance = 0.30

        start_keys = [
            "classify_intent",
            "route",
            "store_turn_memory",
            "format_plan_for_prompt",
            "build_system_prompt",
        ]
        for key in start_keys:
            patches[key].start()

        return orchestrator, patches

    @pytest.mark.asyncio
    async def test_think_disabled_for_non_thinking_chat_model(self):
        orchestrator, patches = self._setup_orchestrator("qwen2.5:7b", False, True)

        # Create a mock response object with content attribute
        mock_response = MagicMock()
        mock_response.content = "test"
        mock_response.thinking = ""

        with patch(
            "assistant.backend.pipeline.orchestrator.run_tool_loop",
            new_callable=AsyncMock,
        ) as mock_loop:
            mock_loop.return_value = mock_response

            try:
                request = MagicMock()
                request.user_id = 1
                request.message = "test message"
                request.session_id = "test_session"
                request.attached_files = []
                request.search_consent = False

                await orchestrator.chat(request)

                call_args = mock_loop.call_args
                assert call_args is not None
                assert call_args[1].get("think") is False
            finally:
                stop_keys = [
                    "classify_intent",
                    "route",
                    "store_turn_memory",
                    "format_plan_for_prompt",
                    "build_system_prompt",
                    "settings",
                ]
                for key in stop_keys:
                    patches[key].stop()

    @pytest.mark.asyncio
    async def test_think_enabled_for_thinking_chat_model(self):
        orchestrator, patches = self._setup_orchestrator("qwen3:27b", True, True)

        mock_response = MagicMock()
        mock_response.content = "test"
        mock_response.thinking = ""

        with patch(
            "assistant.backend.pipeline.orchestrator.run_tool_loop",
            new_callable=AsyncMock,
        ) as mock_loop:
            mock_loop.return_value = mock_response

            try:
                request = MagicMock()
                request.user_id = 1
                request.message = "test message"
                request.session_id = "test_session"
                request.attached_files = []
                request.search_consent = False

                await orchestrator.chat(request)

                call_args = mock_loop.call_args
                assert call_args is not None
                assert call_args[1].get("think") is True
            finally:
                stop_keys = [
                    "classify_intent",
                    "route",
                    "store_turn_memory",
                    "format_plan_for_prompt",
                    "build_system_prompt",
                    "settings",
                ]
                for key in stop_keys:
                    patches[key].stop()

    @pytest.mark.asyncio
    async def test_think_defaults_to_false_when_not_in_plan(self):
        orchestrator, patches = self._setup_orchestrator("qwen3:27b", True, False)

        mock_response = MagicMock()
        mock_response.content = "test"
        mock_response.thinking = ""

        with patch(
            "assistant.backend.pipeline.orchestrator.run_tool_loop",
            new_callable=AsyncMock,
        ) as mock_loop:
            mock_loop.return_value = mock_response

            try:
                request = MagicMock()
                request.user_id = 1
                request.message = "test message"
                request.session_id = "test_session"
                request.attached_files = []
                request.search_consent = False

                await orchestrator.chat(request)

                call_args = mock_loop.call_args
                assert call_args is not None
                assert call_args[1].get("think") is True
            finally:
                stop_keys = [
                    "classify_intent",
                    "route",
                    "store_turn_memory",
                    "format_plan_for_prompt",
                    "build_system_prompt",
                    "settings",
                ]
                for key in stop_keys:
                    patches[key].stop()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])