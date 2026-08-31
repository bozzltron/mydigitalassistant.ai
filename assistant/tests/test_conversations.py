"""
Regression tests for multi-conversation support.
Covers session creation, listing, switching, and title persistence.
"""

import pytest
from fastapi.testclient import TestClient

from assistant.backend.config import settings
from assistant.backend.main import _state, app, get_orchestrator, get_store
from assistant.backend.memory.retrieval import Retriever
from assistant.backend.pipeline.orchestrator import Orchestrator, OrchestratorDeps
from assistant.backend.memory.store import MemoryStore


@pytest.fixture
async def client(store, stub_llm, stub_search):
    """FastAPI TestClient with test dependencies wired in."""
    retriever = Retriever(store=store, llm_client=stub_llm)
    orchestrator = Orchestrator(
        deps=OrchestratorDeps(
            store=store,
            retriever=retriever,
            llm_client=stub_llm,
            search_tool=stub_search,
        )
    )

    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_orchestrator] = lambda: orchestrator

    _state["store"] = store
    _state["llm_client"] = stub_llm
    _state["retriever"] = retriever
    _state["orchestrator"] = orchestrator
    _state["search_tool"] = stub_search

    original_db_path = settings.database_path
    original_scheduler = settings.scheduler_enabled
    settings.database_path = store.db_path
    settings.scheduler_enabled = False
    try:
        with TestClient(app) as c:
            _state["llm_client"] = stub_llm
            _state["search_tool"] = stub_search
            yield c
    finally:
        settings.database_path = original_db_path
        settings.scheduler_enabled = original_scheduler
        app.dependency_overrides.clear()
        _state.clear()


@pytest.fixture
async def test_user(store):
    """Create a test user directly in the store and return it."""
    user = await store.create_user("testuser")
    return user


@pytest.mark.asyncio
async def test_create_session_appears_in_list(store: MemoryStore, test_user):
    """New session created via API appears in user's session list immediately."""
    # Create new session
    session_id = "test_conv_new_123"
    await store.create_session(session_id, test_user.id, title="Test Session")

    # List sessions - should include the new one
    sessions = await store.get_sessions_for_user(test_user.id)
    session_ids = [s["id"] for s in sessions]
    assert session_id in session_ids

    # Verify title is used as label
    session = next(s for s in sessions if s["id"] == session_id)
    assert session["last_message"] == "Test Session"
    assert session["episode_count"] == 0


@pytest.mark.asyncio
async def test_session_label_falls_back_to_first_message(store: MemoryStore, test_user):
    """Session with no title uses first user message as label."""
    session_id = "test_conv_fallback_456"
    await store.create_session(session_id, test_user.id, title=None)

    # Add an episode
    await store.create_episode(test_user.id, session_id, "user", "Hello world", frame_ids=[])
    await store.create_episode(test_user.id, session_id, "assistant", "Hi there", frame_ids=[])

    sessions = await store.get_sessions_for_user(test_user.id)
    session = next(s for s in sessions if s["id"] == session_id)

    assert session["episode_count"] == 2
    assert session["last_message"] == "Hello world"


@pytest.mark.asyncio
async def test_update_session_title(store: MemoryStore, test_user):
    """Session title can be updated and reflects in listing."""
    session_id = "test_conv_update_789"
    await store.create_session(session_id, test_user.id, title="Original Title")

    # Update title
    await store.update_session_title(session_id, test_user.id, "Updated Title")

    sessions = await store.get_sessions_for_user(test_user.id)
    session = next(s for s in sessions if s["id"] == session_id)
    assert session["last_message"] == "Updated Title"


@pytest.mark.asyncio
async def test_sessions_sorted_by_last_activity(store: MemoryStore, test_user):
    """Sessions are sorted by most recent activity first."""
    # Create multiple sessions with NO titles (so label falls back to first message)
    await store.create_session("sess_a", test_user.id, title=None)
    await store.create_session("sess_b", test_user.id, title=None)
    await store.create_session("sess_c", test_user.id, title=None)

    # Add activity to B (most recent)
    await store.create_episode(test_user.id, "sess_b", "user", "Activity in B", frame_ids=[])

    sessions = await store.get_sessions_for_user(test_user.id)
    # First session should be B (most recent activity)
    assert sessions[0]["id"] == "sess_b"
    assert sessions[0]["last_message"] == "Activity in B"


@pytest.mark.asyncio
async def test_conversation_api_create_and_list(client, test_user):
    """Full API test: create session via POST, verify it appears in GET /sessions."""
    user_id = test_user.id
    # Create new conversation
    resp = client.post(f"/conversations/new?user_id={user_id}")
    assert resp.status_code == 200
    data = resp.json()
    session_id = data["session_id"]
    assert session_id.startswith("conv_")

    # List sessions - should include the new one
    resp = client.get(f"/users/{user_id}/sessions")
    assert resp.status_code == 200
    sessions = resp.json()
    session_ids = [s["id"] for s in sessions]
    assert session_id in session_ids


@pytest.mark.asyncio
async def test_conversation_api_update_title(client, test_user):
    """Full API test: update session title via PATCH."""
    user_id = test_user.id
    # Create session
    resp = client.post(f"/conversations/new?user_id={user_id}")
    session_id = resp.json()["session_id"]

    # Update title
    resp = client.patch(
        f"/conversations/{session_id}/title",
        json={"user_id": user_id, "title": "API Updated Title"}
    )
    assert resp.status_code == 200
    assert resp.json()["title"] == "API Updated Title"

    # Verify in list
    resp = client.get(f"/users/{user_id}/sessions")
    sessions = resp.json()
    session = next(s for s in sessions if s["id"] == session_id)
    assert session["last_message"] == "API Updated Title"


@pytest.mark.asyncio
async def test_switch_conversation_loads_correct_history(store: MemoryStore):
    """Switching conversations loads the correct message history."""
    user = await store.get_user(1)
    if not user:
        pytest.skip("User 1 not found")

    # Create two sessions with different messages
    await store.create_session("sess_switch_1", user.id, title="First")
    await store.create_episode(user.id, "sess_switch_1", "user", "Message in first", frame_ids=[])
    await store.create_episode(user.id, "sess_switch_1", "assistant", "Reply to first", frame_ids=[])

    await store.create_session("sess_switch_2", user.id, title="Second")
    await store.create_episode(user.id, "sess_switch_2", "user", "Message in second", frame_ids=[])
    await store.create_episode(user.id, "sess_switch_2", "assistant", "Reply to second", frame_ids=[])

    # Verify each session has its own episodes
    eps_1 = await store.get_episodes_for_session("sess_switch_1")
    eps_2 = await store.get_episodes_for_session("sess_switch_2")

    assert len(eps_1) == 2
    assert eps_1[0].content == "Message in first"
    assert len(eps_2) == 2
    assert eps_2[0].content == "Message in second"