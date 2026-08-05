from unittest.mock import AsyncMock

import pytest
from assistant.backend.db.schema import init_db
from assistant.backend.memory.retrieval import Retriever
from assistant.backend.memory.store import MemoryStore
from assistant.backend.pipeline.llm_client import EmbeddingResponse


@pytest.mark.asyncio
async def test_learn_fact_then_recall_in_future_session(tmp_path):
    db_path = str(tmp_path / "test.db")
    await init_db(db_path)
    store = MemoryStore(db_path)
    user = await store.create_user("alice")

    # Turn 1: learn about the guitar (manual extraction simulation).
    guitar = await store.create_frame("guitar", "entity")
    await store.upsert_slot(guitar.id, "brand", "Fender")
    await store.upsert_slot(guitar.id, "model", "Stratocaster")
    await store.upsert_slot(guitar.id, "strings", "6")
    await store.upsert_slot(guitar.id, "neck", "maple")
    await store.store_frame_embedding(guitar.id, [1.0] + [0.0] * 767)

    await store.create_episode(
        user.id,
        "session-1",
        "user",
        "I just bought a Fender Stratocaster with 6 strings and a maple neck",
        [guitar.id],
    )
    await store.create_episode(
        user.id,
        "session-1",
        "assistant",
        "Nice — that's a great guitar.",
        [guitar.id],
    )

    # New session, fresh in-memory context: ask about the guitar.
    mock_llm = AsyncMock()
    mock_llm.embed.return_value = EmbeddingResponse(
        embedding=[1.0] + [0.0] * 767,
        model="nomic-embed-text",
    )
    retriever = Retriever(store, mock_llm, min_relevance=0.5)

    ctx = await retriever.retrieve("What do you know about my guitar?", user.id)

    assert any(rf.frame.name == "guitar" for rf in ctx.retrieved_frames)

    guitar_frame = next(rf for rf in ctx.retrieved_frames if rf.frame.name == "guitar")
    values = {s.key: s.value for s in guitar_frame.slots}
    assert values["brand"] == "Fender"
    assert values["model"] == "Stratocaster"
    assert values["strings"] == "6"
    assert values["neck"] == "maple"

    contents = [ep.content for ep in ctx.recent_episodes]
    assert any("Stratocaster" in c for c in contents)
    assert any("guitar" in c.lower() for c in contents)
