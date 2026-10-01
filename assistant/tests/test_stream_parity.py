"""Regression: `/chat/stream` must behave like `chat()`.

The streaming path is the one the frontend actually uses, and it had drifted
from `chat()` in several verified ways. Each test here pins one of them and
fails against the pre-fix code:

1. `filter_relevant` had no timeout, so a stalled embedder hung the stream.
2. A generation failure escaped the generator instead of degrading to a message.
3. A `compute` result was shown to the user and never stored in memory.
4. The `**Sources:**` footer was never appended.
5. Learning reaches the user on both paths, and raises no alert.
"""

from __future__ import annotations

import json
import time

import pytest

import assistant.backend.pipeline.search as search_mod
import assistant.backend.pipeline.streaming as streaming
from assistant.backend.config import settings
from assistant.backend.memory.retrieval import Retriever
from assistant.backend.pipeline.llm_client import ChatResponse
from assistant.backend.pipeline.orchestrator import (
    _GENERATION_FAILURE_MESSAGE,
    ChatRequest,
    Orchestrator,
    OrchestratorDeps,
)
from assistant.backend.pipeline.search import SearchInfo, SearchResult, WebSearchTool

from .conftest import add_embedding_cluster


async def _collect(agen):
    events = []
    async for line in agen:
        if line.startswith("data: "):
            events.append(json.loads(line[len("data: "):].strip()))
    return events


def _build_orchestrator(store, stub_llm, search_tool):
    retriever = Retriever(store=store, llm_client=stub_llm)
    return Orchestrator(
        deps=OrchestratorDeps(
            store=store,
            retriever=retriever,
            llm_client=stub_llm,
            search_tool=search_tool,
        )
    )


def _install_llm(stub_llm, *, task_type="search", wants_search=True,
                 search_query="capital of texas", extraction=None, answer="Austin."):
    """Deterministic routing/extraction/answer at the LLM boundary."""
    extraction = extraction if extraction is not None else {"slots": [], "associations": []}

    async def chat_fn(messages, model=None, temperature=0.7, format=None,
                      stream=False, **kwargs):
        system = messages[0].content.lower()
        if "classify" in system:
            return ChatResponse(
                content=json.dumps({
                    "task_type": task_type,
                    "wants_search": wants_search,
                    "search_query": search_query,
                }),
                model=stub_llm.utility_model,
                done=True,
            )
        if "extract" in system and "search" in system:
            return ChatResponse(
                content=json.dumps(extraction),
                model=stub_llm.utility_model,
                done=True,
            )
        if "extract" in system:
            return ChatResponse(
                content='{"slots": [], "associations": []}',
                model=stub_llm.utility_model,
                done=True,
            )
        return ChatResponse(content=answer, model=stub_llm.chat_model, done=True)

    original = stub_llm.chat
    stub_llm.chat = chat_fn

    async def no_think(model=None):
        return False

    stub_llm.supports_thinking = no_think
    return original


def _search_tool(results: list[SearchResult]) -> WebSearchTool:
    async def fake_search_with_info(query, num_results=5, llm_client=None, user_consent=False):
        return results, SearchInfo(backend="test", query=query, results=results)

    tool = WebSearchTool(enabled=True)
    tool.search_with_info = fake_search_with_info
    return tool


# 1. Relevance gate is bounded -------------------------------------------------

@pytest.mark.asyncio
async def test_streamed_search_does_not_hang_when_the_relevance_gate_stalls(
    store, stub_llm, monkeypatch
):
    add_embedding_cluster("capital", "texas", "austin")

    async def hang(*args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        import asyncio

        await asyncio.sleep(30)
        return []

    monkeypatch.setattr(search_mod, "filter_relevant", hang)
    monkeypatch.setattr(settings, "search_timeout", 0.05)

    results = [SearchResult(title="T", url="https://example.com/a", snippet="s", engine="e")]
    orchestrator = _build_orchestrator(store, stub_llm, _search_tool(results))
    user = await store.create_user("alice")
    original = _install_llm(stub_llm)

    started = time.monotonic()
    try:
        events = await _collect(
            orchestrator.chat_stream(
                ChatRequest(user_id=user.id, message="What is the capital of Texas?",
                            session_id="s-hang")
            )
        )
    finally:
        stub_llm.chat = original
    elapsed = time.monotonic() - started

    # Pre-fix, the gate had no timeout, so the turn waited out the whole sleep
    # (30s). Post-fix it is cut off at settings.search_timeout and the turn ends.
    assert any(e["type"] == "finalize" for e in events)
    assert elapsed < 5, f"stream waited {elapsed:.1f}s on a stalled relevance gate"


# 2. Generation failure degrades ----------------------------------------------

@pytest.mark.asyncio
async def test_streamed_generation_failure_yields_a_readable_message(
    store, stub_llm, monkeypatch
):
    async def boom(*args, **kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("ollama down")
        yield  # pragma: no cover - makes this an async generator

    monkeypatch.setattr(streaming, "stream_tool_loop", boom)

    orchestrator = _build_orchestrator(store, stub_llm, _search_tool([]))
    user = await store.create_user("alice")
    original = _install_llm(stub_llm, task_type="functional", wants_search=False)

    try:
        events = await _collect(
            orchestrator.chat_stream(
                ChatRequest(user_id=user.id, message="Hello", session_id="s-boom")
            )
        )
    finally:
        stub_llm.chat = original

    answers = [e["answer"] for e in events if e["type"] == "finalize"]
    assert answers
    assert answers[-1] == _GENERATION_FAILURE_MESSAGE


# 3. A computed result is remembered ------------------------------------------

@pytest.mark.asyncio
async def test_streamed_computation_is_stored_in_memory(store, stub_llm, monkeypatch):
    async def always_math(query):  # noqa: ANN001, ANN201
        return True

    async def fake_execute_python(code, timeout=30):  # noqa: ANN001, ANN201
        return "The answer is 42."

    orchestrator = _build_orchestrator(store, stub_llm, _search_tool([]))
    user = await store.create_user("alice")
    monkeypatch.setattr(orchestrator, "_detect_math_intent", always_math)
    monkeypatch.setattr(stub_llm, "math_model", "fake-math")
    monkeypatch.setattr(stub_llm, "execute_python", fake_execute_python)
    original = _install_llm(stub_llm, task_type="functional", wants_search=False)

    try:
        await _collect(
            orchestrator.chat_stream(
                ChatRequest(user_id=user.id, message="calculate 6*7", session_id="s-math")
            )
        )
    finally:
        stub_llm.chat = original

    frames = await store.list_frames(owner_user_id=user.id)
    computation_frames = [f for f in frames if f.name.startswith("computation_")]
    assert computation_frames, "streamed computation was not stored"
    slots = await store.get_slots_for_frame(computation_frames[0].id)
    assert any(s.value == "The answer is 42." for s in slots)


# 4. Sources footer ------------------------------------------------------------

@pytest.mark.asyncio
async def test_streamed_search_answer_carries_its_sources(store, stub_llm):
    add_embedding_cluster("capital", "texas", "austin")
    results = [
        SearchResult(
            title="Capital of Texas",
            url="https://en.wikipedia.org/wiki/Texas",
            snippet="Austin is the capital of Texas",
            engine="wikipedia",
        )
    ]
    orchestrator = _build_orchestrator(store, stub_llm, _search_tool(results))
    user = await store.create_user("alice")
    original = _install_llm(stub_llm)

    try:
        events = await _collect(
            orchestrator.chat_stream(
                ChatRequest(user_id=user.id, message="What is the capital of Texas?",
                            session_id="s-sources")
            )
        )
    finally:
        stub_llm.chat = original

    answers = "".join(e["answer"] for e in events if e["type"] == "finalize")
    assert "**Sources:**" in answers
    assert "https://en.wikipedia.org/wiki/Texas" in answers


# 5. Learning on both paths ----------------------------------------------------

@pytest.mark.asyncio
async def test_streamed_search_learning_reaches_the_user_without_an_alert(
    store, stub_llm, monkeypatch
):
    """Both orchestrator paths surface what was learned, and neither rings the bell.

    This test used to assert the opposite — that a `search_result`/`conflict` alert
    was raised — because the original bug was that the streaming path raised none at
    all, so a web-UI user was never told anything. That was the right fix for the
    wrong signal.

    Phase C.4 removed these alerts from *both* paths: a fact learned while the user
    is watching, in the conversation they are having, is not something they were
    absent for. "New facts learned from search" is news to nobody who just asked for
    the search.

    Parity still matters, so it is asserted on the channel that survived: the
    summaries in the response, which `Message.tsx` renders as "What I learned" and
    "Found from search", itemised per slot. If that reached one path and not the
    other, the UI would silently stop reporting learning on the web client — which
    is the original bug, in the other direction.
    """
    add_embedding_cluster("capital", "texas", "austin")
    results = [
        SearchResult(title="T", url="https://example.com/a", snippet="Austin", engine="e")
    ]
    extraction = {
        "slots": [
            {"frame_name": "texas", "frame_type": "location",
             "key": "capital", "value": "Austin"},
        ],
        "associations": [],
    }
    orchestrator = _build_orchestrator(store, stub_llm, _search_tool(results))
    user = await store.create_user("alice")
    original = _install_llm(stub_llm, extraction=extraction)

    alerts: list[dict] = []
    real_create_alert = store.create_alert

    async def recording_create_alert(**kwargs):
        alerts.append(kwargs)
        return await real_create_alert(**kwargs)

    monkeypatch.setattr(store, "create_alert", recording_create_alert)

    try:
        events = await _collect(
            orchestrator.chat_stream(
                ChatRequest(user_id=user.id, message="What is the capital of Texas?",
                            session_id="s-alert")
            )
        )
    finally:
        stub_llm.chat = original

    # The information reached the client.
    meta = [e for e in events if e["type"] == "meta"]
    assert meta, "the stream produced no meta event for the UI to render"
    summary = meta[-1].get("extraction_summary") or {}
    search_summary = meta[-1].get("search_extraction_summary") or {}
    assert summary or search_summary, (
        "no extraction summary on the streaming path — the web UI would report "
        "nothing learned"
    )

    # And no bell entry was raised for it: the user was present.
    learning_alerts = [
        a for a in alerts if a.get("type") in ("search_result", "conflict")
    ]
    assert learning_alerts == [], (
        f"learning alerts were raised mid-conversation: {learning_alerts}"
    )


# 6. Streaming corrections do not double the turn -----------------------------

@pytest.mark.asyncio
async def test_streamed_correction_logs_the_user_turn_once(store, stub_llm):
    """A correction on `/chat/stream` used to re-enter `chat()`.

    `chat()` logs the user episode and runs routing/extraction itself, so the
    streaming correction path logged the user's message twice and paid two
    redundant LLM calls before producing the answer. The session history is the
    observable symptom: the user turn appeared twice.
    """
    orchestrator = _build_orchestrator(store, stub_llm, _search_tool([]))
    user = await store.create_user("alice")

    # Route to correction, then let the correction parser fail gracefully so the
    # branch completes without touching memory: we are testing the plumbing, not
    # the correction semantics.
    original = _install_llm(stub_llm, task_type="correction", wants_search=False)

    async def failing_extract(*args, **kwargs):  # noqa: ANN002, ANN003
        return None

    from assistant.backend.pipeline import extractor as extractor_mod

    real_extract = extractor_mod.extract_correction
    extractor_mod.extract_correction = failing_extract
    try:
        events = await _collect(
            orchestrator.chat_stream(
                ChatRequest(
                    user_id=user.id,
                    message="actually the guitar has 12 strings",
                    session_id="s-correction",
                )
            )
        )
    finally:
        stub_llm.chat = original
        extractor_mod.extract_correction = real_extract

    assert any(e["type"] == "finalize" for e in events)

    episodes = await store.get_episodes_for_session("s-correction", user_id=user.id)
    user_turns = [e for e in episodes if e.role == "user"]
    assistant_turns = [e for e in episodes if e.role == "assistant"]
    assert len(user_turns) == 1, f"user turn logged {len(user_turns)}x"
    assert len(assistant_turns) == 1, f"assistant turn logged {len(assistant_turns)}x"
