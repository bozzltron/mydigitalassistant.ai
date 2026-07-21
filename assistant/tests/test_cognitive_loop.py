import json

import pytest

from assistant.backend.db.schema import init_db
from assistant.backend.memory.retrieval import Retriever
from assistant.backend.memory.store import MemoryStore
from assistant.backend.pipeline.extractor import extract_and_apply
from assistant.backend.pipeline.llm_client import ChatMessage, build_system_prompt
from assistant.backend.pipeline.task_router import TaskType, classify


@pytest.mark.asyncio
async def test_full_cognitive_loop_learns_then_recalls(tmp_path, stub_llm):
    db_path = str(tmp_path / "test.db")
    await init_db(db_path)
    store = MemoryStore(db_path)

    user = await store.create_user("alice")
    session = "s1"

    # Turn 1: user shares a fact about their guitar.
    turn1_user = "I love my Fender Stratocaster, it has 6 strings"
    task1 = await classify(turn1_user, stub_llm)
    assert task1 == TaskType.FUNCTIONAL

    retriever = Retriever(store, stub_llm, min_relevance=0.5)
    ctx1 = await retriever.retrieve(turn1_user, user.id)
    assert ctx1.retrieved_frames == []

    system1 = build_system_prompt(ctx1.formatted, "functional")
    response1 = await stub_llm.chat(
        [ChatMessage(role="system", content=system1), ChatMessage(role="user", content=turn1_user)]
    )
    assert len(response1.content) > 0

    ep1 = await store.create_episode(user.id, session, "user", turn1_user, frame_ids=[])
    await extract_and_apply(turn1_user, response1.content, store, stub_llm, ep1.id)

    # Assert the agent learned the guitar frame with slots.
    frame = await store.get_frame_by_name("fender_stratocaster")
    assert frame is not None
    slots = await store.get_slots_for_frame(frame.id)
    slot_values = {s.key: s.value for s in slots}
    assert slot_values["brand"] == "Fender"
    assert slot_values["model"] == "Stratocaster"
    assert slot_values["strings"] == "6"
    assert any(s.source_episode_id == ep1.id for s in slots)

    # In a real deployment the frame's embedding is refreshed after extraction.
    await retriever.embed_frame(frame, await store.get_slots_for_frame(frame.id))

    # Turn 2: user asks what the assistant remembers about the guitar.
    turn2_user = "What do you remember about my guitar?"
    task2 = await classify(turn2_user, stub_llm)
    assert task2 == TaskType.INTROSPECTIVE

    ctx2 = await retriever.retrieve(turn2_user, user.id)
    assert any(rf.frame.name == "fender_stratocaster" for rf in ctx2.retrieved_frames)
    assert any(ep.content == turn1_user for ep in ctx2.recent_episodes)

    system2 = build_system_prompt(ctx2.formatted, "introspective")
    response2 = await stub_llm.chat(
        [ChatMessage(role="system", content=system2), ChatMessage(role="user", content=turn2_user)]
    )
    assert len(response2.content) > 0

    # No new facts should be extracted from an introspective recall query.
    json.loads(stub_llm._extraction_response(turn2_user.lower()))
    summary = await extract_and_apply(turn2_user, response2.content, store, stub_llm)
    assert summary == {"slots_applied": 0, "associations_created": 0, "conflicts_created": 0}
