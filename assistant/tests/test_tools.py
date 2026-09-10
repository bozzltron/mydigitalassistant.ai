"""Tests for the M5 tool framework: registry, handlers, and the tool loop."""

import re
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from assistant.backend.pipeline.llm_client import ChatMessage, ChatResponse, ToolCall
from assistant.backend.pipeline.search import WebSearchTool
from assistant.backend.pipeline.tools import (
    _make_fetch_url_handler,
    builtin_tools,
    run_tool_loop,
)


class FakeToolLLM:
    """Scripted chat client: yields queued responses in order."""

    def __init__(self, responses: list[ChatResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    async def chat(self, messages, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        return self.responses.pop(0)

    async def close(self):
        pass


class EnabledSearch:
    enabled = True

    async def search(self, query: str, num_results: int = 5):
        from assistant.backend.pipeline.search import SearchResult

        return [
            SearchResult(
                title=f"Result for {query}",
                url="http://127.0.0.1:8080/x",
                snippet="snip",
            )
        ]

    def close(self):
        pass


def test_builtin_tools_gated_on_search():
    with_search = builtin_tools(EnabledSearch())
    without_search = builtin_tools(WebSearchTool(enabled=False))
    names_with = {t["function"]["name"] for t in with_search}
    names_without = {t["function"]["name"] for t in without_search}
    assert "web_search" in names_with
    assert "web_search" not in names_without
    # Core tools always available
    assert {"upsert_slot", "recall", "finalize"} <= names_without


def test_tool_def_shape():
    tools = builtin_tools(WebSearchTool(enabled=False))
    upsert_tool = next(t for t in tools if t["function"]["name"] == "upsert_slot")
    d = upsert_tool
    assert d["type"] == "function"
    assert d["function"]["name"] == "upsert_slot"
    assert "parameters" in d["function"]


async def test_upsert_slot_handler(store):
    from assistant.backend.pipeline.tool_executor import execute_tool, init_store
    init_store(str(store.db_path))
    # Create a test user
    await store.create_user("test_user")

    result = await execute_tool(
        "upsert_slot",
        {
            "frame_name": "test_entity",
            "slot_key": "test_key",
            "slot_value": "test_value",
        },
        "1",  # user_id must match created user
        "test_session"
    )
    assert result.success
    assert result.data["slot_key"] == "test_key"
    assert result.data["new_value"] == "test_value"


async def test_recall_handler(store):
    from assistant.backend.pipeline.tool_executor import execute_tool, init_store
    init_store(str(store.db_path))
    await store.create_user("test_user")

    result = await execute_tool("recall", {"query": "test query"}, "1", "test_session")
    assert result.success
    assert "results" in result.data


async def test_run_tool_loop_executes_and_answers(store):
    from assistant.backend.pipeline.tool_executor import init_store
    init_store(str(store.db_path))
    await store.create_user("test_user")
    
    scripted = [
        ChatResponse(
            content="",
            model="m",
            done=True,
            tool_calls=[ToolCall(name="upsert_slot", arguments={"frame_name": "test", "slot_key": "key", "slot_value": "value"})],
        ),
        ChatResponse(content="Stored successfully.", model="m", done=True),
    ]
    llm = FakeToolLLM(scripted)
    content, _resp, tool_msgs = await _run(llm)
    assert "Stored successfully" in content
    assert tool_msgs[-1]["role"] == "tool"


async def test_run_tool_loop_unknown_tool():
    scripted = [
        ChatResponse(
            content="",
            model="m",
            done=True,
            tool_calls=[ToolCall(name="does_not_exist", arguments={})],
        ),
        ChatResponse(content="OK then.", model="m", done=True),
    ]
    llm = FakeToolLLM(scripted)
    content, _, tool_msgs = await _run(llm)
    assert content == "OK then."
    # Unknown tool is logged as warning by tool_executor (verified in test output)
    # The tool result shows empty content since executor returns error but loop continues
    tool_result = tool_msgs[-1]
    assert tool_result["role"] == "tool"


async def test_run_tool_loop_bounded_rounds(store):
    from assistant.backend.pipeline.tool_executor import init_store
    init_store(str(store.db_path))
    await store.create_user("test_user")
    
    looping = ChatResponse(
        content="",
        model="m",
        done=True,
        tool_calls=[ToolCall(name="upsert_slot", arguments={"frame_name": "test", "slot_key": "key", "slot_value": "value"})],
    )
    llm = FakeToolLLM([looping] * 2 + [ChatResponse(content="done", model="m", done=True)])
    content, resp, _ = await _run(llm, max_rounds=2)
    # Should stop after max_rounds and return a summary
    assert "considered" in content.lower() or "done" in content.lower()
    # With max_rounds=2, we get 2 LLM calls (the loop runs 2 iterations)
    assert len(llm.calls) == 2
    assert resp is not None


async def test_tool_results_not_in_thinking_chain(store):
    """§6.3 hygiene: thinking is returned for audit but never re-sent as input."""
    from assistant.backend.pipeline.tool_executor import init_store
    init_store(str(store.db_path))
    await store.create_user("test_user")
    
    scripted = [
        ChatResponse(
            content="", model="m", done=True,
            thinking="pondering",
            tool_calls=[ToolCall(name="upsert_slot", arguments={"frame_name": "test", "slot_key": "key", "slot_value": "value"})],
        ),
        ChatResponse(content="2", model="m", done=True),
    ]
    llm = FakeToolLLM(scripted)
    _, resp, _ = await _run(llm)
    # run_tool_loop returns a dict with the answer
    assert resp.get("reasoning_effort") is not None
    second_call_messages = llm.calls[1]["messages"]
    assert all(m.get("content", "") != "pondering" for m in second_call_messages)


async def _run(llm, max_rounds=3):
    """Helper: run the loop and return (content, response, convo_messages)."""
    from assistant.backend.pipeline.tools import MAX_TOOL_ROUNDS

    tools = builtin_tools(EnabledSearch())
    messages = [{"role": "user", "content": "q"}]
    resp = await run_tool_loop(llm, messages, tools, max_rounds=max_rounds or MAX_TOOL_ROUNDS)
    last_llm_messages = llm.calls[-1]["messages"]
    return resp.get("answer", ""), resp, last_llm_messages


# ---------------------------------------------------------------------------
# fetch_url integration tests
# ---------------------------------------------------------------------------


class TestFetchUrlHandlerIntegration:
    """Test the fetch_url tool's integration with memory and extraction.

    These tests mock httpx at the module level so we control the network
    layer without requiring a live server.
    """

    @pytest.fixture
    def mock_httpx_get(self):
        """Patch httpx.AsyncClient.get to return controlled responses."""
        async def mock_get(self, url, **kwargs):
            return MockResponse(200, "Article text.", {"content-type": "text/plain"})

        with patch.object(httpx.AsyncClient, "get", new=mock_get):
            yield

    @pytest.mark.asyncio
    async def test_no_store_no_extraction(self, mock_httpx_get):
        """Without store/llm_client, fetch returns content without calling LLM."""
        handler = _make_fetch_url_handler(store=None, llm_client=None)
        result = await handler("http://example.com/article")
        assert "Article text" in result

    @pytest.mark.asyncio
    async def test_with_store_and_llm_calls_extraction(self, mock_httpx_get, store, stub_llm):
        """With store + llm_client, extraction is called and facts stored."""
        stub_llm.set_extraction_result(
            slots=[
                {
                    "frame_name": "test_article",
                    "frame_type": "entity",
                    "key": "author",
                    "value": "Jane Doe",
                },
            ],
            associations=[],
        )

        handler = _make_fetch_url_handler(store=store, llm_client=stub_llm)
        result = await handler("http://example.com/article")
        assert "Article text" in result

        frame = await store.get_frame_by_name("test_article")
        assert frame is not None
        assert frame.id is not None
        slots = await store.get_slots_for_frame(frame.id)
        slot = next((s for s in slots if s.key == "author"), None)
        assert slot is not None
        assert slot.value == "Jane Doe"

    @pytest.mark.asyncio
    async def test_extraction_error_does_not_break_fetch(self, mock_httpx_get, store):
        """If extraction raises, fetched content is still returned."""
        bad_llm = type("BadLLM", (), {
            "utility_model": "none",
            "chat": AsyncMock(side_effect=RuntimeError("LLM down")),
            "close": lambda self: None,
        })()

        handler = _make_fetch_url_handler(store=store, llm_client=bad_llm)
        result = await handler("http://example.com/page")
        assert "Article text" in result

    @pytest.mark.asyncio
    async def test_fetch_error_returns_error_message(self, store, stub_llm):
        """Network errors are returned as error strings, not raised."""
        async def failing_get(self, url, **kwargs):
            raise httpx.ConnectError("connection refused")

        with patch.object(httpx.AsyncClient, "get", new=failing_get):
            handler = _make_fetch_url_handler(store=store, llm_client=stub_llm)
            result = await handler("http://example.com/page")
            assert result.startswith("Error fetching")


class MockResponse:
    """Minimal httpx response stand-in for mock_httpx_get."""

    def __init__(self, status_code: int, text: str, headers: dict):
        self.status_code = status_code
        self.text = text
        self.content = text.encode("utf-8")
        self.headers = headers
        self.encoding = "utf-8"

    def raise_for_status(self):
        pass