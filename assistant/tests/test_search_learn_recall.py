"""Tier 3 integration tests: search → extract → store → recall.

These tests prove the core user promise:
- Turn 1: agent searches, extracts facts, stores them in memory
- Turn 2: agent recalls from memory WITHOUT re-searching
"""

import asyncio
import json

from assistant.backend.memory.retrieval import Retriever
from assistant.backend.pipeline.llm_client import ChatResponse
from assistant.backend.pipeline.orchestrator import ChatRequest, Orchestrator, OrchestratorDeps
from assistant.backend.pipeline.search import SearchResult, WebSearchTool


async def test_search_learn_stores_fact_in_memory(store, stub_llm):
    """Search results are extracted and stored in the memory store."""
    from .conftest import add_embedding_cluster

    # The relevance gate compares query vs result embeddings; make the stub
    # embedder treat this test's topic as one cluster.
    add_embedding_cluster("capital", "texas", "austin")

    retriever = Retriever(store=store, llm_client=stub_llm)

    async def fake_search(query, num_results=5):
        if "capital" in query.lower() and "texas" in query.lower():
            return [
                SearchResult(
                    title="Capital of Texas",
                    url="https://en.wikipedia.org/wiki/Texas",
                    snippet="Austin is the capital of Texas",
                    engine="wikipedia",
                ),
            ]
        return []

    stub_search = WebSearchTool(enabled=True)
    stub_search.search = fake_search

    orchestrator = Orchestrator(
        deps=OrchestratorDeps(
            store=store,
            retriever=retriever,
            llm_client=stub_llm,
            search_tool=stub_search,
        )
    )

    user = await store.create_user("alice")

    async def chat_fn(messages, model=None, temperature=0.7, format=None, stream=False, **kwargs):
        system = messages[0].content.lower()
        if "classify" in system:
            return ChatResponse(content='{"task_type": "search"}', model="qwen2.5:3b", done=True)
        if "extract" in system and "search" in system:
            return ChatResponse(
                content=json.dumps({
                    "slots": [
                        {"frame_name": "texas", "frame_type": "location",
                         "key": "capital", "value": "Austin"},
                    ],
                    "associations": [],
                }),
                model="qwen2.5:3b",
                done=True,
            )
        if "extract" in system:
            return ChatResponse(
                content='{"slots": [], "associations": []}',
                model="qwen2.5:3b",
                done=True,
            )
        return ChatResponse(
            content="Austin is the capital of Texas.",
            model="qwen2.5:7b",
            done=True,
        )

    original_chat = stub_llm.chat
    stub_llm.chat = chat_fn

    try:
        await orchestrator.chat(
            ChatRequest(user_id=user.id, message="What is the capital of Texas?", session_id="s1")
        )
        await asyncio.sleep(0.05)

        frame = await store.get_frame_by_name("texas")
        assert frame is not None
        slot = await store.get_slot(frame.id, "capital")
        assert slot.value == "Austin"
    finally:
        stub_llm.chat = original_chat


async def test_search_learn_then_recall_does_not_re_search(store, stub_llm):
    """Turn 1: search and learn. Turn 2: recall from memory — search call count stays the same.

    This is the project's core value proposition: learn once, remember without re-searching.
    """
    retriever = Retriever(store=store, llm_client=stub_llm)

    search_count = 0

    async def counting_search(query, num_results=5):
        nonlocal search_count
        search_count += 1
        if "capital" in query.lower() and "texas" in query.lower():
            return [
                SearchResult(
                    title="Capital of Texas",
                    url="https://en.wikipedia.org/wiki/Texas",
                    snippet="Austin is the capital of Texas",
                    engine="wikipedia",
                ),
            ]
        return []

    stub_search = WebSearchTool(enabled=True)
    stub_search.search = counting_search

    orchestrator = Orchestrator(
        deps=OrchestratorDeps(
            store=store,
            retriever=retriever,
            llm_client=stub_llm,
            search_tool=stub_search,
        )
    )

    user = await store.create_user("alice")

    async def chat_fn(messages, model=None, temperature=0.7, format=None, stream=False, **kwargs):
        system = messages[0].content.lower()
        if "classify" in system:
            user_msg = messages[1].content.lower()
            if "what do you know" in user_msg or "remember" in user_msg:
                return ChatResponse(
                    content='{"task_type": "introspective"}',
                    model="qwen2.5:3b",
                    done=True,
                )
            return ChatResponse(content='{"task_type": "search"}', model="qwen2.5:3b", done=True)
        if "extract" in system and "search" in system:
            return ChatResponse(
                content=json.dumps({
                    "slots": [
                        {"frame_name": "texas", "frame_type": "location",
                         "key": "capital", "value": "Austin"},
                    ],
                    "associations": [],
                }),
                model="qwen2.5:3b",
                done=True,
            )
        if "extract" in system:
            return ChatResponse(
                content='{"slots": [], "associations": []}',
                model="qwen2.5:3b",
                done=True,
            )
        return ChatResponse(
            content="Austin is the capital of Texas.",
            model="qwen2.5:7b",
            done=True,
        )

    original_chat = stub_llm.chat
    stub_llm.chat = chat_fn

    try:
        # Turn 1: ask the question — triggers search
        await orchestrator.chat(
            ChatRequest(user_id=user.id, message="What is the capital of Texas?", session_id="s1")
        )
        await asyncio.sleep(0.05)
        assert search_count == 1

        # Refresh embedding so retrieval can find the frame
        texas = await store.get_frame_by_name("texas")
        if texas:
            slots = await store.get_slots_for_frame(texas.id)
            await retriever.embed_frame(texas, slots)

        # Turn 2: ask again — should recall from memory, not re-search
        await orchestrator.chat(
            ChatRequest(user_id=user.id, message="What do you know about Texas?", session_id="s2")
        )
        await asyncio.sleep(0.05)

        # Search was NOT called again — the fact was recalled from memory
        assert search_count == 1
    finally:
        stub_llm.chat = original_chat


async def test_search_contradiction_triggers_auto_resolve_and_surfaces_conflict(store, stub_llm):
    """When search results contradict existing memory, the conflict is auto-resolved.

    The search_extraction_summary in the response should report conflicts_created > 0
    and individual slots should have conflict: true so the UI can surface this.
    """
    retriever = Retriever(store=store, llm_client=stub_llm)

    frame = await store.create_frame("guitar", "entity")
    await store.upsert_slot(frame.id, "strings", "6")

    search_results = [
        SearchResult(
            title="12-string guitar",
            url="https://example.com/12-string",
            snippet="Most acoustic guitars have 6 strings, but 12-string guitars are common.",
            engine="test",
        ),
    ]

    stub_search = WebSearchTool(enabled=True)

    orchestrator = Orchestrator(
        deps=OrchestratorDeps(
            store=store,
            retriever=retriever,
            llm_client=stub_llm,
            search_tool=stub_search,
        )
    )

    user = await store.create_user("alice")

    async def chat_fn(messages, model=None, temperature=0.7, format=None, stream=False, **kwargs):
        system_lower = messages[0].content.lower()
        if "classify" in system_lower:
            return ChatResponse(content='{"task_type": "search"}', model="qwen2.5:3b", done=True)
        if "extract" in system_lower and "search" in system_lower:
            return ChatResponse(
                content=json.dumps({
                    "slots": [
                        {"frame_name": "guitar", "frame_type": "entity",
                         "key": "strings", "value": "12"},
                    ],
                    "associations": [],
                }),
                model="qwen2.5:3b",
                done=True,
            )
        if "extract" in system_lower:
            return ChatResponse(
                content='{"slots": [], "associations": []}',
                model="qwen2.5:3b",
                done=True,
            )
        return ChatResponse(content="12-string guitars exist.", model="qwen2.5:7b", done=True)

    original_chat = stub_llm.chat
    stub_llm.chat = chat_fn

    # Mock the search method on the instance
    original_search = stub_search.search
    async def fake_search(q, num_results=5):
        return search_results
    stub_search.search = fake_search

    try:
        resp = await orchestrator.chat(
            ChatRequest(user_id=user.id, message="Tell me about guitars", session_id="s1")
        )

        # Search extraction should have created a conflict
        assert resp.search_extraction_summary is not None, (
            f"Expected search_extraction_summary to be set, got: {resp.search_extraction_summary}"
        )
        assert resp.search_extraction_summary.get("conflicts_created", 0) >= 1

        slots = resp.search_extraction_summary.get("slots", [])
        assert any(s.get("conflict") for s in slots), (
            f"Expected at least one slot with conflict=true, got: {slots}"
        )

        guitar = await store.get_frame_by_name("guitar")
        slot = await store.get_slot(guitar.id, "strings")
        assert slot.value == "12"

        conflicts = await store.get_conflicts_for_frame(guitar.id)
        assert any(c.status == "auto_resolved" and c.new_value == "12" for c in conflicts)
    finally:
        stub_llm.chat = original_chat
        stub_search.search = original_search
