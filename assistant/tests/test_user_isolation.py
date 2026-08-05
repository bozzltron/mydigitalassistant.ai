from unittest.mock import AsyncMock

import pytest
from assistant.backend.db.schema import init_db
from assistant.backend.memory.retrieval import Retriever
from assistant.backend.memory.store import MemoryStore
from assistant.backend.pipeline.llm_client import EmbeddingResponse


@pytest.mark.asyncio
async def test_multi_user_episodic_isolation_and_shared_household_frame(tmp_path):
    db_path = str(tmp_path / "test.db")
    await init_db(db_path)
    store = MemoryStore(db_path)

    alice = await store.create_user("alice")
    bob = await store.create_user("bob")

    alice_frame = await store.create_frame("alice-thing", "entity")
    bob_frame = await store.create_frame("bob-thing", "entity")
    household = await store.create_frame("our-household", "household")

    await store.store_frame_embedding(alice_frame.id, [0.1] * 768)
    await store.store_frame_embedding(bob_frame.id, [0.2] * 768)
    await store.store_frame_embedding(household.id, [1.0] + [0.0] * 767)

    await store.create_episode(
        alice.id, "alice-session", "user", "Alice says hi", [alice_frame.id, household.id]
    )
    await store.create_episode(
        bob.id, "bob-session", "user", "Bob says hello", [bob_frame.id, household.id]
    )

    alice_eps = await store.get_episodes_for_user(alice.id)
    bob_eps = await store.get_episodes_for_user(bob.id)

    assert len(alice_eps) == 1
    assert "Alice" in alice_eps[0].content
    assert len(bob_eps) == 1
    assert "Bob" in bob_eps[0].content

    mock_llm = AsyncMock()
    mock_llm.embed.return_value = EmbeddingResponse(
        embedding=[1.0] + [0.0] * 767,
        model="nomic-embed-text",
    )
    retriever = Retriever(store, mock_llm, min_relevance=0.5)

    alice_ctx = await retriever.retrieve("tell me about our household", alice.id)
    alice_recent = [ep.content for ep in alice_ctx.recent_episodes]
    assert "Alice" in alice_recent[0]
    assert all("Bob" not in c for c in alice_recent)
    alice_names = {rf.frame.name for rf in alice_ctx.retrieved_frames}
    assert "our-household" in alice_names
    assert "bob-thing" not in alice_names

    bob_ctx = await retriever.retrieve("tell me about our household", bob.id)
    bob_recent = [ep.content for ep in bob_ctx.recent_episodes]
    assert "Bob" in bob_recent[0]
    assert all("Alice" not in c for c in bob_recent)
    bob_names = {rf.frame.name for rf in bob_ctx.retrieved_frames}
    assert "our-household" in bob_names
    assert "alice-thing" not in bob_names
