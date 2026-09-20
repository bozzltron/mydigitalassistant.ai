import json

import httpx

from assistant.backend.pipeline.llm_client import (
    ChatMessage,
    OllamaClient,
    build_system_prompt,
    split_thinking,
)


def test_build_system_prompt_functional():
    prompt = build_system_prompt("frame: guitar, slot: strings=6", "functional")
    assert "structured memory system" in prompt
    assert "guitar" in prompt
    assert "ground" not in prompt.lower() or "introspective" not in prompt.lower()


def test_build_system_prompt_introspective():
    prompt = build_system_prompt("frame: guitar", "introspective")
    assert (
        "ground" in prompt.lower()
        or "honest" in prompt.lower()
        or "do not hallucinate" in prompt.lower()
    )
    assert "guitar" in prompt
    # Introspective prompt should be more constrained
    assert "memory state" in prompt.lower() or "frames" in prompt.lower()


# --- split_thinking (Phase 6 M1: think-mode plumbing) ---


def test_split_thinking_no_tags():
    clean, thinking = split_thinking("Just an answer.")
    assert clean == "Just an answer."
    assert thinking == ""


def test_split_thinking_single_block():
    clean, thinking = split_thinking("<think>reasoning here</think>The answer is 4.")
    assert clean == "The answer is 4."
    assert thinking == "reasoning here"


def test_split_thinking_multiple_blocks():
    content = "<think>first</think>Partial<think>second</think>Final."
    clean, thinking = split_thinking(content)
    assert clean == "PartialFinal."
    assert "first" in thinking and "second" in thinking


def test_split_thinking_unclosed_block():
    """Model cut off mid-think: trailing content after <think> is reasoning."""
    clean, thinking = split_thinking("<think>started reasoning and then")
    assert clean == ""
    assert "started reasoning and then" in thinking


def test_split_thinking_multiline_preserved_in_thinking():
    content = "<think>line1\nline2</think>\n\nAnswer"
    _, thinking = split_thinking(content)
    assert "line1\nline2" in thinking


# --- chat(): think flag + structured thinking field ---


def _client_with_transport(handler, **kwargs) -> OllamaClient:
    client = OllamaClient(**kwargs)
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url=client.base_url
    )
    return client


async def test_chat_passes_think_flag_and_uses_structured_field():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        # Handle /api/show for supports_thinking check
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["thinking"]})
        captured["payload"] = json.loads(request.content)
        return httpx.Response(200, json={
            "model": "chat-model",
            "done": True,
            "message": {
                "role": "assistant",
                "content": "The answer.",
                "thinking": "Step by step...",
            },
        })

    client = _client_with_transport(handler)
    resp = await client.chat(
        [ChatMessage(role="user", content="hi")], think=True, num_predict=512
    )
    assert captured["payload"]["think"] is True
    assert captured["payload"]["options"]["num_predict"] == 512
    assert resp.content == "The answer."
    assert resp.thinking == "Step by step..."
    await client.close()


async def test_chat_without_think_omits_flag_and_parses_inline_tags():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        return httpx.Response(200, json={
            "model": "chat-model",
            "done": True,
            "message": {
                "role": "assistant",
                "content": "<think>hidden chain</think>Visible reply.",
            },
        })

    client = _client_with_transport(handler)
    resp = await client.chat([ChatMessage(role="user", content="hi")])
    assert "think" not in captured["payload"]
    assert resp.content == "Visible reply."
    assert resp.thinking == "hidden chain"
    await client.close()


async def test_chat_think_false_omits_key():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["thinking"]})
        captured["payload"] = json.loads(request.content)
        return httpx.Response(200, json={
            "model": "m", "done": True,
            "message": {"role": "assistant", "content": "ok"},
        })

    client = _client_with_transport(handler)
    await client.chat([ChatMessage(role="user", content="hi")], think=False)
    # think=False should omit the key (default behavior)
    assert "think" not in captured["payload"]
    await client.close()


# --- capabilities probe ---


async def test_model_capabilities_caches_per_model():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content)["model"])
        return httpx.Response(200, json={"capabilities": ["thinking", "tools"]})

    client = _client_with_transport(handler)
    caps1 = await client.model_capabilities("some-model")
    caps2 = await client.model_capabilities("some-model")
    assert caps1 == ["thinking", "tools"]
    assert caps2 == ["thinking", "tools"]
    assert calls == ["some-model"]  # second call served from cache
    assert await client.supports_thinking("some-model") is True
    await client.close()


async def test_model_capabilities_fails_soft():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("ollama down")

    client = _client_with_transport(handler)
    assert await client.model_capabilities("missing") == []
    assert await client.supports_thinking("missing") is False
    await client.close()


async def test_chat_num_ctx_auto_by_role():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.setdefault("num_ctx", []).append(
            json.loads(request.content)["options"]["num_ctx"]
        )
        return httpx.Response(200, json={
            "model": "m", "done": True,
            "message": {"role": "assistant", "content": "ok"},
        })

    client = OllamaClient(
        chat_model="chat-m", utility_model="util-m",
        chat_num_ctx=8192, utility_num_ctx=4096,
    )
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url=client.base_url
    )
    await client.chat([ChatMessage(role="user", content="hi")])
    await client.chat([ChatMessage(role="user", content="hi")], model="util-m")
    assert captured["num_ctx"] == [8192, 4096]
    await client.close()


async def test_chat_num_ctx_explicit_overrides_and_zero_disables():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        opts = json.loads(request.content)["options"]
        captured.setdefault("num_ctx", []).append(opts.get("num_ctx"))
        return httpx.Response(200, json={
            "model": "m", "done": True,
            "message": {"role": "assistant", "content": "ok"},
        })

    client = OllamaClient(chat_num_ctx=8192, utility_num_ctx=4096)
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url=client.base_url
    )
    await client.chat([ChatMessage(role="user", content="hi")], num_ctx=16384)
    await client.chat([ChatMessage(role="user", content="hi")], num_ctx=0)
    assert captured["num_ctx"] == [16384, None]
    await client.close()


async def test_keep_alive_sent_on_chat_and_embed():
    """Models stay warm: keep_alive rides on every inference payload."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured[request.url.path] = json.loads(request.content)
        if request.url.path == "/api/embeddings":
            return httpx.Response(200, json={"embedding": [0.1, 0.2]})
        return httpx.Response(200, json={
            "model": "chat-model",
            "done": True,
            "message": {"role": "assistant", "content": "ok"},
        })

    client = _client_with_transport(handler, keep_alive="45m")
    await client.chat([ChatMessage(role="user", content="hi")])
    await client.embed("hello")
    assert captured["/api/chat"]["keep_alive"] == "45m"
    assert captured["/api/embeddings"]["keep_alive"] == "45m"
    await client.close()


# --- keep_alive normalization (regression: bare "-1" string made Ollama 400) ---


def test_keep_alive_bare_negative_int_sent_as_number():
    client = OllamaClient(keep_alive="-1")
    assert client._keep_alive_param() == -1


def test_keep_alive_zero_and_positive_bare_ints_get_seconds_unit():
    assert OllamaClient(keep_alive="0")._keep_alive_param() == "0s"
    assert OllamaClient(keep_alive="600")._keep_alive_param() == "600s"


def test_keep_alive_duration_strings_pass_through():
    assert OllamaClient(keep_alive="30m")._keep_alive_param() == "30m"
    assert OllamaClient(keep_alive="-1s")._keep_alive_param() == "-1s"


async def test_embed_sends_normalized_keep_alive():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        return httpx.Response(200, json={"embedding": [0.1, 0.2]})

    client = _client_with_transport(handler, keep_alive="-1")
    await client.embed("hello")
    assert captured["payload"]["keep_alive"] == -1
