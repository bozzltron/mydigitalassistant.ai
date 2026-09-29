"""Regression: capability caching and the streaming think flag.

Two silent-failure bugs in `OllamaClient`:

1. `model_capabilities` cached a *failed* probe as `[]`. One transient
   `/api/show` error (Ollama restarting) then disabled `think=True` and
   reported `supports_tools() == False` for the rest of the process's life.
2. `chat_stream` only sent `think` when it was truthy, so `think=False` never
   reached the server. A thinking-capable model then ran its default reasoning
   pass anyway, which under `format="json"` is the documented empty-content bug.
"""

from __future__ import annotations

import json

import httpx
import pytest

from assistant.backend.pipeline.llm_client import ChatMessage, OllamaClient


class _FakeResponse:
    def __init__(self, caps: list[str]) -> None:
        self._caps = caps

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return {"capabilities": self._caps}


class _FakeHTTP:
    """Scripted stand-in for the httpx client's `/api/show` probe."""

    def __init__(self, seq: list) -> None:
        self.seq = list(seq)
        self.calls = 0

    async def post(self, url: str, json: dict | None = None):  # noqa: ANN001
        self.calls += 1
        item = self.seq.pop(0)
        if isinstance(item, Exception):
            raise item
        return _FakeResponse(item)


@pytest.mark.asyncio
async def test_failed_capability_probe_is_not_cached():
    client = OllamaClient()
    fake = _FakeHTTP([httpx.ConnectError("ollama restarting"), ["thinking"]])

    async def _get_client():
        return fake

    client._get_client = _get_client  # type: ignore[method-assign]

    # First probe fails: empty for this call, but must not be remembered.
    assert await client.model_capabilities("m") == []
    # Second probe succeeds and is now honoured.
    assert await client.model_capabilities("m") == ["thinking"]
    assert fake.calls == 2


@pytest.mark.asyncio
async def test_successful_capability_probe_is_cached():
    client = OllamaClient()
    fake = _FakeHTTP([["thinking"], ["tools"]])

    async def _get_client():
        return fake

    client._get_client = _get_client  # type: ignore[method-assign]

    assert await client.model_capabilities("m") == ["thinking"]
    assert await client.model_capabilities("m") == ["thinking"]
    assert fake.calls == 1


@pytest.mark.asyncio
async def test_chat_stream_sends_explicit_think_false():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        line = json.dumps({"message": {"content": "hi"}, "done": True})
        return httpx.Response(200, content=line + "\n")

    transport = httpx.MockTransport(handler)
    client = OllamaClient()
    client._client = httpx.AsyncClient(transport=transport, base_url=client.base_url)

    chunks = [
        c
        async for c in client.chat_stream(
            [ChatMessage(role="user", content="x")], think=False
        )
    ]

    assert chunks[-1].done
    assert captured["payload"]["think"] is False


@pytest.mark.asyncio
async def test_chat_sends_explicit_think_false():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200, json={"model": "m", "message": {"content": "hi"}, "done": True}
        )

    transport = httpx.MockTransport(handler)
    client = OllamaClient()
    client._client = httpx.AsyncClient(transport=transport, base_url=client.base_url)

    await client.chat([ChatMessage(role="user", content="x")], think=False)

    assert captured["payload"]["think"] is False
