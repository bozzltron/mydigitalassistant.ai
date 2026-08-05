import pytest
from assistant.backend.memory.retrieval import Retriever
from assistant.backend.pipeline.orchestrator import ChatRequest, Orchestrator, OrchestratorDeps


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


@pytest.mark.asyncio
async def test_orchestrator_chat_returns_response(orchestrator, store):
    user = await store.create_user("alice")
    request = ChatRequest(user_id=user.id, message="Hello!")

    response = await orchestrator.chat(request)

    assert response.response  # non-empty
    assert response.session_id
    assert response.task_type in ("functional", "introspective")


@pytest.mark.asyncio
async def test_orchestrator_logs_user_and_assistant_episodes(orchestrator, store):
    user = await store.create_user("alice")
    request = ChatRequest(user_id=user.id, message="Hi there")

    await orchestrator.chat(request)

    episodes = await store.get_episodes_for_user(user.id)
    assert len(episodes) == 2
    assert any(ep.role == "user" and ep.content == "Hi there" for ep in episodes)
    assert any(ep.role == "assistant" for ep in episodes)


@pytest.mark.asyncio
async def test_orchestrator_uses_provided_session_id(orchestrator, store):
    user = await store.create_user("alice")
    request = ChatRequest(user_id=user.id, message="Hi", session_id="my-session")

    response = await orchestrator.chat(request)
    assert response.session_id == "my-session"


@pytest.mark.asyncio
async def test_orchestrator_generates_session_id_if_not_provided(orchestrator, store):
    user = await store.create_user("alice")
    request = ChatRequest(user_id=user.id, message="Hi")

    response = await orchestrator.chat(request)
    assert response.session_id  # non-empty UUID


@pytest.mark.asyncio
async def test_orchestrator_classifies_introspective(orchestrator, store):
    """Heuristic should classify introspective queries without LLM call."""
    user = await store.create_user("alice")
    request = ChatRequest(user_id=user.id, message="What do you remember about dogs?")

    response = await orchestrator.chat(request)
    assert response.task_type == "introspective"


@pytest.mark.asyncio
async def test_orchestrator_classifies_functional(orchestrator, store):
    user = await store.create_user("alice")
    request = ChatRequest(user_id=user.id, message="How does a guitar amplifier work?")

    response = await orchestrator.chat(request)
    assert response.task_type == "functional"


@pytest.mark.asyncio
async def test_orchestrator_includes_memory_context_in_response(orchestrator, store):
    """The response should include the memory context for trace mode."""
    user = await store.create_user("alice")
    request = ChatRequest(user_id=user.id, message="Tell me about guitars")

    response = await orchestrator.chat(request)
    assert response.memory_context  # non-empty


@pytest.mark.asyncio
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
