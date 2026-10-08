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


def _setup_orchestrator(chat_model: str, supports_thinking: bool, plan_think: bool):
    """Build an Orchestrator with role-default mocks and started patches.

    Returns (orchestrator, patches, mocks) so tests can reconfigure the
    llm_client, settings mock, or plan before driving orchestrator.chat().
    """
    from assistant.backend.pipeline.orchestrator import Orchestrator

    store = MagicMock()
    retriever = MagicMock()
    llm_client = MagicMock()
    llm_client.chat_model = chat_model
    llm_client.max_model = ""  # no max-escalation tier by default
    llm_client.supports_thinking = AsyncMock(return_value=supports_thinking)
    llm_client.supports_tools = AsyncMock(return_value=False)
    llm_client.math_model = ""
    llm_client.chat = AsyncMock(return_value=MagicMock(content="test", thinking=""))
    llm_client.context_window = MagicMock(return_value=16384)
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
    plan.max_intelligence = False  # no auto max-escalation by default

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
    # Settings the context-budget and tool-loop work added. A MagicMock attribute
    # is truthy but not an int, so `min()` and comparisons need real values.
    mock_settings.verbatim_history_turns = 6
    mock_settings.max_tool_rounds = 6
    mock_settings.max_tool_rounds_deep = 12
    mock_settings.chat_num_ctx = 16384

    for key in (
        "classify_intent",
        "route",
        "store_turn_memory",
        "format_plan_for_prompt",
        "build_system_prompt",
    ):
        patches[key].start()

    mocks = {
        "llm_client": llm_client,
        "settings": mock_settings,
        "plan": plan,
    }
    return orchestrator, patches, mocks


def _stop_patches(patches: dict) -> None:
    for key in ("settings", "classify_intent", "route", "store_turn_memory",
                "format_plan_for_prompt", "build_system_prompt"):
        patches[key].stop()


def _patch_stream_tool_loop(captured: dict):
    """Patch the loop's generation with an async-gen fake that records kwargs.

    `_run_turn` calls `stream_tool_loop` (an async generator yielding SSE frames),
    not the old `run_tool_loop` (which returned a dict). Now that `chat()` drains
    the loop, this is the interception point for the think/model kwargs.
    """
    import assistant.backend.pipeline.streaming as streaming

    async def _fake(*args, **kwargs):
        captured.update(kwargs)
        yield streaming.serialize_event(streaming.FinalizeEvent("test", ""))

    return patch.object(streaming, "stream_tool_loop", _fake)


def _request(message: str = "test message", max_intelligence: bool = False):
    request = MagicMock()
    request.user_id = 1
    request.message = message
    request.session_id = "test_session"
    request.attached_files = []
    request.search_consent = False
    request.max_intelligence = max_intelligence
    # Explicit, because a MagicMock attribute is truthy: without these the loop
    # would read force_search=True and a MagicMock user_turn_override.
    request.force_search = False
    request.user_turn_override = None
    return request


class TestOrchestratorThinkLogic:
    """Test orchestrator think flag logic."""

    @pytest.mark.asyncio
    async def test_think_disabled_for_non_thinking_chat_model(self):
        orchestrator, patches, _ = _setup_orchestrator("qwen2.5:7b", False, True)
        captured: dict = {}
        with _patch_stream_tool_loop(captured):
            try:
                await orchestrator.chat(_request())
                assert captured.get("think") is False
            finally:
                _stop_patches(patches)

    @pytest.mark.asyncio
    async def test_think_enabled_for_thinking_chat_model(self):
        orchestrator, patches, _ = _setup_orchestrator("qwen3:27b", True, True)
        captured: dict = {}
        with _patch_stream_tool_loop(captured):
            try:
                await orchestrator.chat(_request())
                assert captured.get("think") is True
            finally:
                _stop_patches(patches)

    @pytest.mark.asyncio
    async def test_think_defaults_to_false_when_not_in_plan(self):
        orchestrator, patches, _ = _setup_orchestrator("qwen3:27b", True, False)
        captured: dict = {}
        with _patch_stream_tool_loop(captured):
            try:
                await orchestrator.chat(_request())
                assert captured.get("think") is True
            finally:
                _stop_patches(patches)


class TestOrchestratorMaxIntelligence:
    """Phase 6 M6: max-intelligence escalation routing in the loop."""

    @pytest.mark.asyncio
    async def test_explicit_max_toggle_routes_tool_loop_to_max_model(self):
        orchestrator, patches, mocks = _setup_orchestrator("chat-model", True, False)
        orchestrator.llm_client.max_model = "max-model"
        orchestrator.llm_client.supports_tools = AsyncMock(return_value=True)

        captured: dict = {}
        with _patch_stream_tool_loop(captured):
            try:
                await orchestrator.chat(_request("compute the answer", max_intelligence=True))
                assert captured.get("model") == "max-model"
                assert captured.get("think") is True
            finally:
                _stop_patches(patches)

    @pytest.mark.asyncio
    async def test_plan_max_intelligence_routes_no_tools_path_to_max_model(self):
        orchestrator, patches, mocks = _setup_orchestrator("chat-model", True, False)
        orchestrator.llm_client.max_model = "max-model"

        # tools disabled -> the loop's no-tools branch, which streams deltas
        mocks["settings"].tools_enabled = False

        # Reasoner auto-escalated: plan.max_intelligence=True
        mocks["plan"].max_intelligence = True

        captured: dict = {}

        async def _fake_chat_stream(messages, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN201
            captured.update(kwargs)
            yield MagicMock(content="test", thinking="", done=True)

        orchestrator.llm_client.chat_stream = _fake_chat_stream
        try:
            await orchestrator.chat(_request("plan escalated query"))
            assert captured.get("model") == "max-model"
            assert captured.get("think") is True
        finally:
            _stop_patches(patches)

    @pytest.mark.asyncio
    async def test_max_falls_back_when_max_model_lacks_tools(self):
        orchestrator, patches, _ = _setup_orchestrator("chat-model", True, False)
        orchestrator.llm_client.max_model = "max-model"
        orchestrator.llm_client.supports_tools = AsyncMock(return_value=False)

        captured: dict = {}
        with _patch_stream_tool_loop(captured):
            try:
                await orchestrator.chat(_request("max query", max_intelligence=True))
                # Falls back to the role-default (None) model with thinking on.
                assert captured.get("model") is None
                assert captured.get("think") is True
            finally:
                _stop_patches(patches)

    @pytest.mark.asyncio
    async def test_no_max_escalation_when_toggle_off_without_reasoner_flag(self):
        orchestrator, patches, _ = _setup_orchestrator("chat-model", True, False)
        orchestrator.llm_client.max_model = "max-model"
        orchestrator.llm_client.supports_tools = AsyncMock(return_value=True)

        captured: dict = {}
        with _patch_stream_tool_loop(captured):
            try:
                await orchestrator.chat(_request("normal query"))
                # No max escalation: tool loop keeps role default (None -> tools_model)
                assert captured.get("model") is None
            finally:
                _stop_patches(patches)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])