"""Tests for the shared transient-failure retry helper.

The assistant is multi-model and tool-heavy; a blip on any idempotent hop
(Ollama, search, fetch) should not fail the turn. These tests pin the policy:
retry transport failures and 429/502/503/504, never 4xx client errors, and give
up after a bounded number of attempts.
"""

import httpx
import pytest

from assistant.backend.pipeline.llm_client import ChatMessage, OllamaClient
from assistant.backend.retry import (
    is_transient_error_message,
    is_transient_http_error,
    retry_transient,
)


def _status_error(code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "http://example.test/")
    return httpx.HTTPStatusError("boom", request=request, response=httpx.Response(code))


class TestIsTransientHttpError:
    def test_connect_error_is_transient(self):
        assert is_transient_http_error(httpx.ConnectError("refused"))

    def test_timeout_is_transient(self):
        assert is_transient_http_error(httpx.ReadTimeout("slow"))

    def test_service_unavailable_is_transient(self):
        assert is_transient_http_error(_status_error(503))

    def test_rate_limit_is_transient(self):
        assert is_transient_http_error(_status_error(429))

    def test_bad_request_is_not_transient(self):
        assert not is_transient_http_error(_status_error(400))

    def test_not_found_is_not_transient(self):
        assert not is_transient_http_error(_status_error(404))

    def test_non_http_error_is_not_transient(self):
        assert not is_transient_http_error(ValueError("nope"))


class TestRetryTransient:
    async def test_returns_first_success_without_sleeping(self):
        calls = []

        async def fn():
            calls.append(1)
            return "ok"

        assert await retry_transient(fn, base_delay=0) == "ok"
        assert len(calls) == 1

    async def test_retries_until_success(self):
        calls = []

        async def fn():
            calls.append(1)
            if len(calls) < 3:
                raise httpx.ConnectError("refused")
            return "ok"

        assert await retry_transient(fn, base_delay=0) == "ok"
        assert len(calls) == 3

    async def test_gives_up_after_attempts(self):
        calls = []

        async def fn():
            calls.append(1)
            raise httpx.ConnectError("refused")

        with pytest.raises(httpx.ConnectError):
            await retry_transient(fn, attempts=3, base_delay=0)
        assert len(calls) == 3

    async def test_non_transient_error_is_not_retried(self):
        calls = []

        async def fn():
            calls.append(1)
            raise _status_error(400)

        with pytest.raises(httpx.HTTPStatusError):
            await retry_transient(fn, base_delay=0)
        assert len(calls) == 1


async def test_ollama_chat_retries_a_503_then_succeeds():
    """A model runner reloading mid-request must not fail the turn."""
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(503, json={"error": "model is loading"})
        return httpx.Response(
            200,
            json={
                "model": "m",
                "done": True,
                "message": {"role": "assistant", "content": "recovered"},
            },
        )

    client = OllamaClient()
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url=client.base_url
    )

    resp = await client.chat([ChatMessage(role="user", content="hi")])

    assert resp.content == "recovered"
    assert len(calls) == 2
    await client.close()


async def test_ollama_chat_does_not_retry_a_400():
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(400, json={"error": "bad request"})

    client = OllamaClient()
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url=client.base_url
    )

    with pytest.raises(httpx.HTTPStatusError):
        await client.chat([ChatMessage(role="user", content="hi")])

    assert len(calls) == 1
    await client.close()


class TestIsTransientErrorMessage:
    def test_disk_io_error_is_transient(self):
        assert is_transient_error_message("disk I/O error")

    def test_locked_is_transient(self):
        assert is_transient_error_message("database is locked")

    def test_service_unavailable_is_transient(self):
        assert is_transient_error_message("Server error '503 Service Unavailable'")

    def test_not_found_is_not_transient(self):
        assert not is_transient_error_message("File not found: nope.csv")

    def test_empty_is_not_transient(self):
        assert not is_transient_error_message("")


def _stub_tool(name, func, monkeypatch):
    """Point a registered tool at ``func`` for the duration of a test."""
    from assistant.backend.pipeline import tool_executor as te

    te._register_builtin_tools()
    monkeypatch.setitem(
        te.TOOL_REGISTRY,
        name,
        {
            "args_class": te.TOOL_REGISTRY[name]["args_class"],
            "func": func,
            "timeout": 5.0,
        },
    )


async def test_read_only_tool_retries_a_transient_failure(monkeypatch):
    """A read-only tool is idempotent, so a transient blip is retried."""
    from assistant.backend.pipeline import tool_executor as te
    from assistant.backend.pipeline.tool_executor import ToolResult

    calls: list[int] = []

    async def flaky(args, user_id, session_id):
        calls.append(1)
        if len(calls) == 1:
            return ToolResult(success=False, error="disk I/O error")
        return ToolResult(success=True, data={"ok": True})

    _stub_tool("recall", flaky, monkeypatch)

    result = await te.execute_tool(
        "recall", {"query": "x"}, user_id="1", session_id="s"
    )

    assert result.success
    assert len(calls) == 2


async def test_read_only_tool_gives_up_after_attempts(monkeypatch):
    from assistant.backend.pipeline import tool_executor as te
    from assistant.backend.pipeline.tool_executor import ToolResult

    calls: list[int] = []

    async def always_transient(args, user_id, session_id):
        calls.append(1)
        return ToolResult(success=False, error="database is locked")

    _stub_tool("recall", always_transient, monkeypatch)

    result = await te.execute_tool(
        "recall", {"query": "x"}, user_id="1", session_id="s"
    )

    assert not result.success
    assert len(calls) == te._TOOL_RETRY_ATTEMPTS


async def test_side_effecting_tool_is_not_retried(monkeypatch):
    """A write must not be repeated behind the model's back."""
    from assistant.backend.pipeline import tool_executor as te
    from assistant.backend.pipeline.tool_executor import ToolResult

    calls: list[int] = []

    async def flaky(args, user_id, session_id):
        calls.append(1)
        return ToolResult(success=False, error="disk I/O error")

    _stub_tool("write_file", flaky, monkeypatch)

    result = await te.execute_tool(
        "write_file",
        {"path": "a.txt", "content": "hi"},
        user_id="1",
        session_id="s",
    )

    assert not result.success
    assert len(calls) == 1


async def test_read_only_tool_does_not_retry_a_permanent_error(monkeypatch):
    from assistant.backend.pipeline import tool_executor as te
    from assistant.backend.pipeline.tool_executor import ToolResult

    calls: list[int] = []

    async def not_found(args, user_id, session_id):
        calls.append(1)
        return ToolResult(success=False, error="File not found: nope.csv")

    _stub_tool("read_file", not_found, monkeypatch)

    result = await te.execute_tool(
        "read_file", {"path": "nope.csv"}, user_id="1", session_id="s"
    )

    assert not result.success
    assert len(calls) == 1
