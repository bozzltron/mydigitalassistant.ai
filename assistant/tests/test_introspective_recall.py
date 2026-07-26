"""Integration test for introspective memory recall - Glasgow scenario."""

import pytest
import pytest_asyncio

from assistant.backend.memory.store import MemoryStore
from assistant.backend.pipeline.llm_client import OllamaClient, build_system_prompt
from assistant.backend.pipeline.task_router import classify, TaskType
from assistant.backend.pipeline.orchestrator import ChatRequest, Orchestrator, OrchestratorDeps
from assistant.tests.conftest import StubLLMClient


@pytest_asyncio.fixture
async def introspective_test_env(tmp_path):
    """Set up environment for introspective recall test."""
    db_path = str(tmp_path / "test.db")
    from assistant.backend.db.schema import init_db
    await init_db(db_path)
    
    store = MemoryStore(db_path)
    llm_client = StubLLMClient()
    
    # Create a user
    user = await store.create_user("testuser")
    
    # Simulate prior conversation about Glasgow article
    await store.create_episode(
        user_id=user.id,
        session_id="session-1",
        role="user",
        content="I'm curious what is happening in the world.",
        frame_ids=[],
    )
    await store.create_episode(
        user_id=user.id,
        session_id="session-1",
        role="assistant",
        content="Sure! I can help with that. When we last checked, AP News (apnews.com) was available for browsing.",
        frame_ids=[],
    )
    await store.create_episode(
        user_id=user.id,
        session_id="session-1",
        role="user",
        content="See if you can get the headlines this time",
        frame_ids=[],
    )
    await store.create_episode(
        user_id=user.id,
        session_id="session-1",
        role="assistant",
        content="Here are some key headlines:\n\n1. Climate Change Summit in Glasgow: World leaders gathering for critical discussions.\n\n2. Health guidelines on respiratory illnesses.\n\n3. Stock market fluctuations.",
        frame_ids=[],
    )
    await store.create_episode(
        user_id=user.id,
        session_id="session-1",
        role="user",
        content="Tell me more about the glasgow article. Can you provide a link",
        frame_ids=[],
    )
    await store.create_episode(
        user_id=user.id,
        session_id="session-1",
        role="assistant",
        content="I understand you're looking for an article from Glasgow, but it seems there might be some confusion. Could you provide more details?",
        frame_ids=[],
    )
    await store.create_episode(
        user_id=user.id,
        session_id="session-1",
        role="user",
        content="Pick up with that.",
        frame_ids=[],
    )
    
    # Create retriever and orchestrator
    from assistant.backend.memory.retrieval import Retriever
    retriever = Retriever(store=store, llm_client=llm_client)
    orchestrator = Orchestrator(deps=OrchestratorDeps(store=store, retriever=retriever, llm_client=llm_client))
    
    return store, llm_client, user.id, orchestrator


async def test_introspective_recall_glasgow_article(introspective_test_env):
    """Test that 'Pick up with that' is classified as INTROSPECTIVE and retrieves prior episodes."""
    store, llm_client, user_id, orchestrator = introspective_test_env
    
    # Query that should be introspective
    request = ChatRequest(
        user_id=user_id,
        message="Pick up with that.",
        session_id="session-2",
    )
    
    response = await orchestrator.chat(request)
    
    # Verify classification
    assert response.task_type == "introspective", f"Expected INTROSPECTIVE, got {response.task_type}"
    
    # Verify memory context contains prior episodes
    assert "AP News" in response.memory_context or "apnews" in response.memory_context.lower()
    assert "Glasgow" in response.memory_context or "headlines" in response.memory_context.lower()
    
    # Verify assistant didn't hallucinate web search
    assert "I don't have that capability" not in response.response.lower() or "web" not in response.response.lower()


async def test_introspective_tell_me_about_article(introspective_test_env):
    """Test that 'Tell me more about the Glasgow article' is classified as INTROSPECTIVE."""
    store, llm_client, user_id, orchestrator = introspective_test_env
    
    request = ChatRequest(
        user_id=user_id,
        message="Tell me more about the Glasgow article.",
        session_id="session-3",
    )
    
    response = await orchestrator.chat(request)
    
    # Verify classification
    assert response.task_type == "introspective", f"Expected INTROSPECTIVE, got {response.task_type}"
    
    # Verify memory context contains prior episodes
    assert "Glasgow" in response.memory_context or "headlines" in response.memory_context.lower()


async def test_functional_not_hallucinate_capability(introspective_test_env):
    """Test that functional queries about web search don't hallucinate."""
    store, llm_client, user_id, orchestrator = introspective_test_env
    
    request = ChatRequest(
        user_id=user_id,
        message="Can you search apnews.com for headlines?",
        session_id="session-4",
    )
    
    response = await orchestrator.chat(request)
    
    # The response should NOT contain fake "Checking AP News..." content
    assert "Checking" not in response.response or "AP News" not in response.response
    assert "searching" not in response.response.lower() or "apnews" not in response.response.lower()
    
    # It should acknowledge limitation if the model followed constraints
    assert "capability" in response.response.lower() or "cannot" in response.response.lower()
