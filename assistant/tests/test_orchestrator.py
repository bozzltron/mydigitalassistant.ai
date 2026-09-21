import json
from unittest.mock import AsyncMock

import httpx
import pytest

from assistant.backend.memory.retrieval import Retriever
from assistant.backend.pipeline.llm_client import ChatResponse
from assistant.backend.pipeline.orchestrator import ChatRequest, Orchestrator, OrchestratorDeps
from assistant.backend.pipeline.search import WebSearchTool

from .conftest import StubLLMClient


class StorageTurnStub(StubLLMClient):
    """Router verdict: functional storage turn, no external lookup wanted."""

    async def chat(self, messages, **kwargs):
        if "classify" in messages[0].content.lower():
            return ChatResponse(
                content='{"task_type": "functional", "wants_search": false}',
                model=self.utility_model,
                done=True,
            )
        return await super().chat(messages, **kwargs)


class SearchSpy:
    """Records search calls; never returns results."""

    def __init__(self) -> None:
        self.queries: list[str] = []
        self.enabled = False

    async def search(self, query: str, num_results: int = 5):
        self.queries.append(query)
        return []

    async def search_with_info(
        self, query: str, num_results: int = 5, llm_client=None, user_consent=False
    ):
        self.queries.append(query)
        from assistant.backend.pipeline.search import SearchInfo
        return [], SearchInfo(backend="test", query=query, results=[])

    def close(self):
        pass


async def test_storage_turn_vetoes_search(store):
    """Empty memory makes the reasoner want search; router's wants_search=false wins."""
    llm = StorageTurnStub()
    llm.set_extraction_result(
        slots=[{"frame_name": "fender_stratocaster", "frame_type": "entity",
                "key": "strings", "value": "6"}]
    )
    spy = SearchSpy()
    orchestrator = Orchestrator(
        deps=OrchestratorDeps(
            store=store,
            retriever=Retriever(store=store, llm_client=llm),
            llm_client=llm,
            search_tool=spy,
        )
    )
    user = await store.create_user("alice")
    request = ChatRequest(
        user_id=user.id,
        message="Remember that my Fender Stratocaster guitar has 6 strings.",
    )

    response = await orchestrator.chat(request)

    assert spy.queries == []
    assert response.task_type == "functional"
    assert response.extraction_summary is not None
    assert response.extraction_summary["slots_applied"] == 1


async def test_stored_facts_injected_into_system_prompt(store):
    """Extraction runs before generation so the reply can acknowledge truthfully."""
    llm = StorageTurnStub()
    llm.set_extraction_result(
        slots=[{"frame_name": "fender_stratocaster", "frame_type": "entity",
                "key": "strings", "value": "6"}]
    )
    orchestrator = Orchestrator(
        deps=OrchestratorDeps(
            store=store,
            retriever=Retriever(store=store, llm_client=llm),
            llm_client=llm,
            search_tool=SearchSpy(),
        )
    )
    user = await store.create_user("alice")
    request = ChatRequest(user_id=user.id, message="My Fender Stratocaster has 6 strings.")

    await orchestrator.chat(request)

    generation_prompts = [
        p for p in llm.system_prompts if "classify" not in p.lower() and "extract" not in p.lower()
    ]
    assert generation_prompts
    assert "Facts you just stored this turn" in generation_prompts[-1]
    assert "fender_stratocaster.strings = 6" in generation_prompts[-1]


@pytest.fixture
async def orchestrator(store, stub_llm, stub_search):
    retriever = Retriever(store=store, llm_client=stub_llm)
    return Orchestrator(
        deps=OrchestratorDeps(
            store=store,
            retriever=retriever,
            llm_client=stub_llm,
            search_tool=stub_search,
        )
    )


async def test_orchestrator_chat_returns_response(orchestrator, store):
    user = await store.create_user("alice")
    request = ChatRequest(user_id=user.id, message="Hello!")

    response = await orchestrator.chat(request)

    assert isinstance(response.response, str) and len(response.response) > 0
    assert response.session_id
    assert response.task_type in ("functional", "introspective")


async def test_orchestrator_logs_user_and_assistant_episodes(orchestrator, store):
    user = await store.create_user("alice")
    request = ChatRequest(user_id=user.id, message="Hi there")

    await orchestrator.chat(request)

    episodes = await store.get_episodes_for_user(user.id)
    assert len(episodes) == 2
    assert any(ep.role == "user" and ep.content == "Hi there" for ep in episodes)
    assert any(ep.role == "assistant" for ep in episodes)


async def test_orchestrator_uses_provided_session_id(orchestrator, store):
    user = await store.create_user("alice")
    request = ChatRequest(user_id=user.id, message="Hi", session_id="my-session")

    response = await orchestrator.chat(request)
    assert response.session_id == "my-session"


async def test_orchestrator_generates_session_id_if_not_provided(orchestrator, store):
    user = await store.create_user("alice")
    request = ChatRequest(user_id=user.id, message="Hi")

    response = await orchestrator.chat(request)
    assert response.session_id  # non-empty UUID


async def test_orchestrator_classifies_introspective(orchestrator, store):
    """Heuristic should classify introspective queries without LLM call."""
    user = await store.create_user("alice")
    request = ChatRequest(user_id=user.id, message="What do you remember about dogs?")

    response = await orchestrator.chat(request)
    assert response.task_type == "introspective"


async def test_orchestrator_injects_search_failure_when_no_results(orchestrator, store, stub_llm):
    """When search returns nothing, the LLM must be told not to fabricate current data."""
    user = await store.create_user("alice")
    request = ChatRequest(
        user_id=user.id,
        message="What are today's headlines from apnews.com?",
        session_id="s-search-fail",
    )

    await orchestrator.chat(request)

    main_prompts = [
        p for p in stub_llm.system_prompts if "structured memory system" in p
    ]
    assert main_prompts
    main_prompt = main_prompts[0]
    assert "Search Status" in main_prompt
    assert "MUST NOT fabricate" in main_prompt
    assert "Recent Search Results" not in main_prompt


async def test_orchestrator_classifies_functional(orchestrator, store):
    user = await store.create_user("alice")
    request = ChatRequest(user_id=user.id, message="How does a guitar amplifier work?")

    response = await orchestrator.chat(request)
    assert response.task_type == "functional"


async def test_orchestrator_includes_memory_context_in_response(orchestrator, store):
    """The response should include the memory context for trace mode."""
    user = await store.create_user("alice")
    request = ChatRequest(user_id=user.id, message="Tell me about guitars")

    response = await orchestrator.chat(request)
    assert isinstance(response.memory_context, str) and len(response.memory_context) > 0


async def test_orchestrator_full_learn_then_recall_cycle(orchestrator, store, stub_llm):
    """The most important test: learn a fact in turn 1, recall it in turn 2.

    This is the project's core value proposition.
    """
    user = await store.create_user("alice")

    # Turn 1: User mentions their guitar
    stub_llm.set_extraction_result(
        slots=[
            {"frame_name": "guitar", "frame_type": "entity", "key": "strings", "value": "6"}
        ],
        associations=[],
    )

    r1 = await orchestrator.chat(
        ChatRequest(user_id=user.id, message="I have a guitar with 6 strings")
    )
    assert r1.task_type == "functional"

    import asyncio

    await asyncio.sleep(0.1)

    frame = await store.get_frame_by_name("guitar")
    assert frame is not None
    slots = await store.get_slots_for_frame(frame.id)
    assert any(s.key == "strings" and s.value == "6" for s in slots)

    # Refresh embedding so retrieval can find the guitar again.
    await orchestrator.retriever.embed_frame(frame, slots)

    # Turn 2: New session, ask about guitar — should retrieve from memory
    stub_llm.set_extraction_result(slots=[], associations=[])

    r2 = await orchestrator.chat(
        ChatRequest(
            user_id=user.id,
            message="What do you remember about my guitar?",
            session_id="different-session",  # new session
        )
    )

    assert "guitar" in r2.memory_context.lower()
    assert "strings" in r2.memory_context.lower() or "6" in r2.memory_context
    assert r2.task_type == "introspective"


async def test_no_search_triggered_when_memory_sufficient(store, stub_llm):
    from assistant.backend.memory.retrieval import Retriever
    from assistant.backend.pipeline.orchestrator import ChatRequest, Orchestrator, OrchestratorDeps
    from assistant.backend.pipeline.search import WebSearchTool

    stub_search = WebSearchTool(enabled=True)
    stub_search.search = AsyncMock(return_value=[])

    retriever = Retriever(store=store, llm_client=stub_llm)
    orchestrator = Orchestrator(
        deps=OrchestratorDeps(
            store=store,
            retriever=retriever,
            llm_client=stub_llm,
            search_tool=stub_search,
        )
    )

    user = await store.create_user("alice")

    guitar = await store.create_frame("guitar", "entity")
    await store.upsert_slot(guitar.id, "strings", "6")
    await store.store_frame_embedding(guitar.id, [1.0] * 768)
    await store.create_episode(
        user.id, "session-1", "user", "I have a guitar with 6 strings", [guitar.id]
    )

    stub_llm.set_extraction_result(slots=[], associations=[])

    request = ChatRequest(user_id=user.id, message="What do you remember about my guitar?")
    await orchestrator.chat(request)

    assert stub_search.search.call_count == 0


async def test_search_failure_does_not_crash(store, stub_llm):
    from assistant.backend.memory.retrieval import Retriever
    from assistant.backend.pipeline.orchestrator import ChatRequest, Orchestrator, OrchestratorDeps
    from assistant.backend.pipeline.search import WebSearchTool

    stub_search = WebSearchTool(enabled=True)
    stub_search.search_with_info = AsyncMock(side_effect=RuntimeError("Search unavailable"))

    retriever = Retriever(store=store, llm_client=stub_llm)
    orchestrator = Orchestrator(
        deps=OrchestratorDeps(
            store=store,
            retriever=retriever,
            llm_client=stub_llm,
            search_tool=stub_search,
        )
    )

    user = await store.create_user("alice")
    request = ChatRequest(user_id=user.id, message="What are capybaras?")

    response = await orchestrator.chat(request)

    assert response.response
    assert response.task_type == "functional"


async def test_orchestrator_handles_correction(store, stub_llm):
    """When user says 'that's wrong', orchestrator parses correction and updates slot."""
    from assistant.backend.pipeline.llm_client import ChatResponse

    async def smart_chat(
        messages, model=None, temperature=0.7, format=None, stream=False, **kwargs
    ):
        system = messages[0].content.lower()
        if "classify" in system:
            return ChatResponse(
                content='{"task_type": "correction"}',
                model="qwen2.5:3b",
                done=True,
            )
        if "correct" in system:
            return ChatResponse(
                content=json.dumps({
                    "frame_name": "guitar",
                    "slot_key": "strings",
                    "new_value": "12",
                }),
                model="qwen2.5:3b",
                done=True,
            )
        return ChatResponse(content="Got it.", model="qwen2.5:3b", done=True)

    user = await store.create_user("alice")

    guitar = await store.create_frame("guitar", "entity")
    await store.upsert_slot(guitar.id, "strings", "6")
    await store.store_frame_embedding(guitar.id, [1.0] * 768)

    retriever = Retriever(store=store, llm_client=stub_llm)
    stub_search = WebSearchTool(enabled=False)

    orchestrator = Orchestrator(
        deps=OrchestratorDeps(
            store=store,
            retriever=retriever,
            llm_client=stub_llm,
            search_tool=stub_search,
        )
    )

    original_chat = stub_llm.chat
    stub_llm.chat = smart_chat

    try:
        request = ChatRequest(
            user_id=user.id,
            message="Actually, the guitar has 12 strings, not 6",
            session_id="correction-session",
        )

        response = await orchestrator.chat(request)

        assert response.task_type == "correction"
        assert "12" in response.response

        updated_slot = await store.get_slot(guitar.id, "strings")
        assert updated_slot.value == "12"
    finally:
        stub_llm.chat = original_chat


async def test_orchestrator_correction_when_utility_returns_null(store):
    """When utility model returns all-null correction, chat model handles it naturally."""
    from unittest.mock import AsyncMock, MagicMock

    from assistant.backend.pipeline.llm_client import OllamaClient

    mock_llm = MagicMock(spec=OllamaClient)
    mock_llm.utility_model = "qwen2.5:3b"
    mock_llm.chat_model = "qwen2.5:7b"

    classify_response = MagicMock()
    classify_response.content = '{"task_type": "correction"}'

    extract_response = MagicMock()
    extract_response.content = '{"frame_name": null, "slot_key": null, "new_value": null}'

    chat_response = MagicMock()
    chat_response.content = (
        "I apologize — I may have gotten something wrong. "
        "Could you clarify which fact needs correcting?"
    )

    mock_llm.chat = AsyncMock(
        side_effect=[classify_response, extract_response, chat_response]
    )
    mock_llm.embed = AsyncMock(return_value=MagicMock(embedding=[0.1] * 768))

    user = await store.create_user("alice")
    retriever = Retriever(store=store, llm_client=mock_llm)
    stub_search = WebSearchTool(enabled=False)
    orchestrator = Orchestrator(
        deps=OrchestratorDeps(
            store=store,
            retriever=retriever,
            llm_client=mock_llm,
            search_tool=stub_search,
        )
    )

    request = ChatRequest(
        user_id=user.id,
        message="That's wrong",
        session_id="vague-correction",
    )

    response = await orchestrator.chat(request)

    assert response.task_type == "correction"
    assert response.response
    assert mock_llm.chat.call_count == 3
    assert "apologize" in response.response.lower() or "clarify" in response.response.lower()


async def test_generation_failure_degrades_gracefully(store):
    """Ollama timeouts/unavailability return a friendly response, not a 500."""
    llm = StorageTurnStub()

    async def boom(messages, **kwargs):
        raise httpx.ReadTimeout("timed out")

    llm.chat = boom
    orchestrator = Orchestrator(
        deps=OrchestratorDeps(
            store=store,
            retriever=Retriever(store=store, llm_client=llm),
            llm_client=llm,
            search_tool=SearchSpy(),
        )
    )
    user = await store.create_user("alice")
    response = await orchestrator.chat(
        ChatRequest(user_id=user.id, message="hello")
    )
    assert "try again" in response.response.lower()
    # The failure is still logged as an assistant episode for the transcript.
    episodes = await store.get_episodes_for_user(user_id=user.id, limit=1)
    assert any(e.role == "assistant" for e in episodes)


async def test_progress_stages_reported_in_order(store):
    """The chat UI's live stage display: stages arrive in pipeline order."""
    llm = StorageTurnStub()
    llm.set_extraction_result(
        slots=[{"frame_name": "orchid", "frame_type": "entity",
                "key": "color", "value": "purple"}]
    )
    orchestrator = Orchestrator(
        deps=OrchestratorDeps(
            store=store,
            retriever=Retriever(store=store, llm_client=llm),
            llm_client=llm,
            search_tool=SearchSpy(),
        )
    )
    user = await store.create_user("alice")
    stages: list[str] = []

    async def progress(stage: str, detail: str) -> None:
        stages.append(stage)

    await orchestrator.chat(
        ChatRequest(user_id=user.id, message="My orchid is purple."),
        progress=progress,
    )
    assert stages[0] == "routing"
    assert "recall" in stages
    assert "learning" in stages
    assert stages[-1] in ("responding", "reasoning")
    assert len(stages) == len(set(stages)), "no stage repeats"


async def test_orchestrator_correction_contradicted_flagged_not_applied(store):
    """When search contradicts a correction, the slot is NOT updated and response explains why."""
    from unittest.mock import AsyncMock, MagicMock

    from assistant.backend.pipeline.llm_client import ChatResponse, OllamaClient

    mock_llm = MagicMock(spec=OllamaClient)
    mock_llm.utility_model = "qwen2.5:3b"
    mock_llm.chat_model = "qwen2.5:7b"

    async def mock_chat(messages, **kwargs):
        system = messages[0].content.lower()
        if "classify" in system:
            return ChatResponse(content='{"task_type": "correction"}', model="fake", done=True)
        if "parse a user correction" in system:
            return ChatResponse(
                content='{"frame_name": "guitar", "slot_key": "strings", "new_value": "12"}',
                model="fake",
                done=True,
            )
        if "contradicts the new value" in system or "flagged for review" in system:
            return ChatResponse(
                content="I acknowledge your correction, but third-party sources suggest "
                        "the current value is still accurate. I've flagged this for review.",
                model="fake",
                done=True,
            )
        return ChatResponse(content="I see.", model="fake", done=True)

    mock_llm.chat = mock_chat
    mock_llm.embed = AsyncMock(return_value=MagicMock(embedding=[0.1] * 768))

    guitar = await store.create_frame("guitar", "entity")
    await store.upsert_slot(guitar.id, "strings", "6")
    await store.store_frame_embedding(guitar.id, [1.0] * 768)

    user = await store.create_user("alice")
    retriever = Retriever(store=store, llm_client=mock_llm)

    mock_search = AsyncMock()
    mock_search.search.return_value = (
        [
            MagicMock(url="https://example.com/strings", snippet="Most guitars have 6 strings"),
        ],
        [],
    )

    orchestrator = Orchestrator(
        deps=OrchestratorDeps(
            store=store,
            retriever=retriever,
            llm_client=mock_llm,
            search_tool=mock_search,
        )
    )

    request = ChatRequest(
        user_id=user.id,
        message="Actually, the guitar has 12 strings, not 6.",
        session_id="contradicted-correction",
    )

    response = await orchestrator.chat(request)

    assert response.task_type == "correction"
    assert "flagged" in response.response.lower() or "review" in response.response.lower()

    updated_slot = await store.get_slot(guitar.id, "strings")
    assert updated_slot.value == "6", "Slot must NOT be updated when correction is contradicted"
