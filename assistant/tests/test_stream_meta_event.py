"""Regression tests: chat_stream's final SSE meta event.

The stream must deliver the same transparency fields the non-streaming
ChatResponse carries — task type, extraction/search summaries, search info —
and surface Brave search-consent requests so the UI's consent dialog opens.

1. A normal searched turn ends with a meta event carrying session_id,
   task_type, extraction_summary, search_extraction_summary, search_info.
2. A consent-required search emits a meta event with
   task_type='search_consent_required' and the search_info record right
   before the finalize with the consent question — no other events after.
"""

import json

from assistant.backend.memory.retrieval import Retriever
from assistant.backend.pipeline.llm_client import ChatResponse
from assistant.backend.pipeline.orchestrator import ChatRequest, Orchestrator, OrchestratorDeps
from assistant.backend.pipeline.search import (
    QuerySensitivity,
    SearchInfo,
    SearchResult,
    SensitivityResult,
    WebSearchTool,
)

from .conftest import add_embedding_cluster


async def _collect(agen):
    """Collect every SSE payload as parsed JSON."""
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


def _install_answer_chat(stub_llm):
    """Tie the stub LLM to deterministic classification/extraction/answer.

    Returns the original chat so callers can restore it in a finally block.
    """
    async def chat_fn(messages, model=None, temperature=0.7, format=None,
                      stream=False, **kwargs):
        system = messages[0].content.lower()
        if "classify" in system:
            return ChatResponse(
                content='{"task_type": "search", "wants_search": true, '
                        '"search_query": "capital of texas"}',
                model=stub_llm.utility_model,
                done=True,
            )
        if "extract" in system and "search" in system:
            return ChatResponse(
                content=json.dumps({
                    "slots": [
                        {"frame_name": "texas", "frame_type": "location",
                         "key": "capital", "value": "Austin"},
                    ],
                    "associations": [],
                }),
                model=stub_llm.utility_model,
                done=True,
            )
        if "extract" in system:
            return ChatResponse(
                content='{"slots": [], "associations": []}',
                model=stub_llm.utility_model,
                done=True,
            )
        return ChatResponse(
            content="Austin is the capital of Texas.",
            model=stub_llm.chat_model,
            done=True,
        )

    original_chat = stub_llm.chat
    stub_llm.chat = chat_fn

    # No real-Ollama network probes during the streamed turn.
    async def no_think(model=None):
        return False

    stub_llm.supports_thinking = no_think
    return original_chat


async def test_chat_stream_ends_with_meta_event(store, stub_llm):
    """A searched streamed turn ends with a meta event carrying the extras."""
    add_embedding_cluster("capital", "texas", "austin")

    async def fake_search_with_info(query, num_results=5, llm_client=None, user_consent=False):
        results = [
            SearchResult(
                title="Capital of Texas",
                url="https://en.wikipedia.org/wiki/Texas",
                snippet="Austin is the capital of Texas",
                engine="wikipedia",
            ),
        ]
        return results, SearchInfo(backend="test", query=query, results=results)

    stub_search = WebSearchTool(enabled=True)
    stub_search.search_with_info = fake_search_with_info

    orchestrator = _build_orchestrator(store, stub_llm, stub_search)
    user = await store.create_user("alice")
    original_chat = _install_answer_chat(stub_llm)

    try:
        events = await _collect(
            orchestrator.chat_stream(
                ChatRequest(
                    user_id=user.id,
                    message="What is the capital of Texas?",
                    session_id="s-meta-1",
                )
            )
        )
    finally:
        stub_llm.chat = original_chat

    metas = [e for e in events if e["type"] == "meta"]
    assert len(metas) == 1
    meta = metas[0]
    assert meta["session_id"] == "s-meta-1"
    assert isinstance(meta["task_type"], str) and meta["task_type"]
    assert meta["search_info"] is not None
    assert meta["search_info"]["backend"] == "test"
    assert meta["search_info"]["query"]
    assert isinstance(meta["extraction_summary"], dict)
    assert isinstance(meta["search_extraction_summary"], dict)
    # The meta event is the last thing the client receives.
    assert events[-1]["type"] == "meta"


async def test_chat_stream_consent_required_emits_meta_before_finalize(store, stub_llm):
    """A consent-required Brave search surfaces consent data, then the question."""
    add_embedding_cluster("capital", "texas", "austin")

    async def fake_search_with_info(query, num_results=5, llm_client=None, user_consent=False):
        return [], SearchInfo(
            backend="brave",
            query=query,
            results=[],
            sensitivity=SensitivityResult(
                level=QuerySensitivity.SENSITIVE,
                reason="mentions a medical condition",
                categories=["medical"],
            ),
            consent_required=True,
        )

    stub_search = WebSearchTool(enabled=True)
    stub_search.search_with_info = fake_search_with_info

    orchestrator = _build_orchestrator(store, stub_llm, stub_search)
    user = await store.create_user("alice")
    original_chat = _install_answer_chat(stub_llm)

    try:
        events = await _collect(
            orchestrator.chat_stream(
                ChatRequest(
                    user_id=user.id,
                    message="Find treatment for my medical condition.",
                    session_id="s-consent-1",
                )
            )
        )
    finally:
        stub_llm.chat = original_chat

    metas = [e for e in events if e["type"] == "meta"]
    assert len(metas) == 1
    meta = metas[0]
    assert meta["task_type"] == "search_consent_required"
    assert meta["search_info"]["backend"] == "brave"
    assert meta["search_info"]["consent_required"] is True
    # The sensitivity enum must arrive as its string value, not an Enum repr.
    assert meta["search_info"]["sensitivity"]["level"] == "sensitive"

    finalize_events = [e for e in events if e["type"] == "finalize"]
    assert len(finalize_events) == 1
    assert "Do you want to proceed?" in finalize_events[0]["answer"]

    # The consent meta arrives immediately before the finalize, and the
    # stream ends there (no answer/extraction events follow the question).
    assert events[-2]["type"] == "meta"
    assert events[-1]["type"] == "finalize"