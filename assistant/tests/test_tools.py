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
    names_with = {t.name for t in with_search}
    names_without = {t.name for t in without_search}
    assert "web_search" in names_with
    assert "web_search" not in names_without
    assert {"get_current_datetime", "calculate"} <= names_without


def test_tool_def_shape():
    tool = builtin_tools(WebSearchTool(enabled=False))[0]
    d = tool.to_def()
    assert d["type"] == "function"
    assert d["function"]["name"] == tool.name
    assert "parameters" in d["function"]


async def test_calculate_handler():
    tools = {t.name: t for t in builtin_tools(WebSearchTool(enabled=False))}
    result = await tools["calculate"].handler(expression="(2+3)*7")
    assert result == "(2+3)*7 = 35"
    bad = await tools["calculate"].handler(expression="__import__('os')")
    assert bad.startswith("Error")


async def test_datetime_handler():
    tools = {t.name: t for t in builtin_tools(WebSearchTool(enabled=False))}
    result = await tools["get_current_datetime"].handler()
    assert re.match(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2} \w+ \(", result)


async def test_run_tool_loop_executes_and_answers():
    scripted = [
        ChatResponse(
            content="",
            model="m",
            done=True,
            tool_calls=[ToolCall(name="calculate", arguments={"expression": "6*7"})],
        ),
        ChatResponse(content="It is 42.", model="m", done=True),
    ]
    llm = FakeToolLLM(scripted)
    content, _resp, tool_msgs = await _run(llm)
    assert content == "It is 42."
    assert tool_msgs[-1].role == "tool"
    assert "42" in tool_msgs[-1].content


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
    assert "unknown tool" in tool_msgs[-1].content


async def test_run_tool_loop_bounded_rounds():
    looping = ChatResponse(
        content="",
        model="m",
        done=True,
        tool_calls=[ToolCall(name="calculate", arguments={"expression": "1+1"})],
    )
    llm = FakeToolLLM([looping] * 2 + [ChatResponse(content="done", model="m", done=True)])
    content, resp, _ = await _run(llm, max_rounds=2)
    assert content == "done"
    assert len(llm.calls) == 3
    assert resp is not None


async def test_tool_results_not_in_thinking_chain():
    """§6.3 hygiene: thinking is returned for audit but never re-sent as input."""
    scripted = [
        ChatResponse(
            content="", model="m", done=True,
            thinking="pondering",
            tool_calls=[ToolCall(name="calculate", arguments={"expression": "1+1"})],
        ),
        ChatResponse(content="2", model="m", done=True),
    ]
    llm = FakeToolLLM(scripted)
    _, resp, _ = await _run(llm)
    assert resp.thinking == "pondering"
    second_call_messages = llm.calls[1]["messages"]
    assert all(m.content != "pondering" for m in second_call_messages)


async def _run(llm, max_rounds=3):
    """Helper: run the loop and return (content, response, convo_messages)."""
    from assistant.backend.pipeline.tools import MAX_TOOL_ROUNDS

    tools = builtin_tools(EnabledSearch())
    messages = [ChatMessage(role="user", content="q")]
    resp = await run_tool_loop(llm, messages, tools, max_rounds=max_rounds or MAX_TOOL_ROUNDS)
    last_llm_messages = llm.calls[-1]["messages"]
    return resp.content, resp, last_llm_messages


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
