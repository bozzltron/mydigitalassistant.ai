"""Regression tests from the 2026-08-25 full memory+pipeline review.

Locks the seam bugs found when auditing module integration:
- Feedback targets the last turn that touched MEMORY (the latest episode of a
  session is the assistant turn, which carries no frame_ids).
- search_similar_frames returns SIMILARITY (higher=better); docstring contract.
- Forgotten frames (priority=0, not tombstoned) stay out of semantic search.
- Graph walk never resurrects GC-tombstoned neighbors into context.
- Scheduled-task interactions pair user + assistant episodes like every path.
- 'resume' restores the task's original prompt instead of corrupting it.
- GC purges vectors of tombstoned frames (candidate set + bounded vec table).
"""

from unittest.mock import AsyncMock

import pytest

from assistant.backend.memory.gc import run_gc
from assistant.backend.memory.retrieval import Retriever
from assistant.backend.memory.store import MemoryStore
from assistant.backend.pipeline.orchestrator import ChatRequest, Orchestrator, OrchestratorDeps
from assistant.backend.pipeline.search import WebSearchTool

from .conftest import StubLLMClient, add_embedding_cluster


async def _seed_turn_with_memory(store: MemoryStore, uid: int, session: str) -> int:
    """User turn linked to a frame, then bare assistant turn (real shape)."""
    frame = await store.create_frame("fender_stratocaster", "entity")
    await store.upsert_slot(frame.id, "strings", "6")
    user_ep = await store.create_episode(
        user_id=uid,
        session_id=session,
        role="user",
        content="my strat has six strings",
        frame_ids=[frame.id],
    )
    await store.create_episode(
        user_id=uid,
        session_id=session,
        role="assistant",
        content="Got it — six strings on the Strat.",
    )
    return user_ep.id


@pytest.mark.asyncio
async def test_feedback_targets_last_memory_touching_turn(store):
    """Thumbs-up used to read the newest episode ([] frame_ids) → silent no-op."""
    alice = await store.create_user("alice")
    await _seed_turn_with_memory(store, alice.id, "s1")

    # The UI passes the session id in the episode_id field (legacy naming).
    bumped = await store.apply_positive_feedback("s1")
    assert bumped >= 1


class ScheduledRouterStub(StubLLMClient):
    async def chat(self, messages, **kwargs):
        if "classify" in messages[0].content.lower():
            from assistant.backend.pipeline.llm_client import ChatResponse

            return ChatResponse(
                content='{"task_type": "scheduled", "wants_search": false}',
                model=self.utility_model,
                done=True,
            )
        return await super().chat(messages, **kwargs)


def _orch(store, llm) -> Orchestrator:
    return Orchestrator(
        deps=OrchestratorDeps(
            store=store,
            retriever=Retriever(store=store, llm_client=llm),
            llm_client=llm,
            search_tool=WebSearchTool(enabled=False),
        )
    )


@pytest.mark.asyncio
async def test_scheduled_task_turn_pairs_user_and_assistant_episodes(
    store, monkeypatch
):
    alice = await store.create_user("alice")
    orch = _orch(store, ScheduledRouterStub())
    monkeypatch.setattr(
        "assistant.backend.pipeline.extractor.extract_scheduled_task_fields",
        AsyncMock(return_value={"intent": "list"}),
    )

    resp = await orch.chat(ChatRequest(user_id=alice.id, message="list my tasks"))

    assert resp.task_type == "scheduled"
    eps = await store.get_episodes_for_session(resp.session_id)
    assert [e.role for e in eps] == ["user", "assistant"]


@pytest.mark.asyncio
async def test_resume_restores_original_prompt(store, monkeypatch):
    """'resume X' must not overwrite prompt/description with the literal request."""
    alice = await store.create_user("alice")
    orch = _orch(store, ScheduledRouterStub())
    await store.upsert_scheduled_task(
        name="weather_check",
        description="morning weather",
        schedule_cron="daily",
        prompt="check today's weather and summarize",
        enabled=False,
        owner_user_id=alice.id,
    )
    monkeypatch.setattr(
        "assistant.backend.pipeline.extractor.extract_scheduled_task_fields",
        AsyncMock(return_value={"intent": "resume", "task_name": "weather_check"}),
    )

    resp = await orch.chat(ChatRequest(user_id=alice.id, message="resume weather_check"))

    assert resp.task_type == "scheduled"
    tasks = await store.get_scheduled_tasks(owner_user_id=alice.id)
    task = next(t for t in tasks if t["name"] == "weather_check")
    assert task["enabled"] == 1
    assert task["prompt"] == "check today's weather and summarize"


@pytest.mark.asyncio
async def test_semantic_search_similarity_convention(store):
    """Identical embedding → similarity ≈ 1.0; higher is better (docstring)."""
    add_embedding_cluster("guitar")
    alice = await store.create_user("bob")
    frame = await store.create_frame("guitar_notes", "entity")
    emb = (await StubLLMClient().embed("guitar")).embedding
    await store.store_frame_embedding(frame.id, emb)
    hits = await store.search_similar_frames(emb, user_id=alice.id, limit=5)
    assert hits and hits[0][2] == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_forgotten_frame_excluded_from_semantic_search(store, stub_llm):
    """forget_frame sets priority=0 without tombstoning — still invisible."""
    add_embedding_cluster("guitar")
    alice = await store.create_user("carol")
    frame = await store.create_frame("secret_guitar", "entity")
    await store.forget_frame(frame.id)
    emb = (await stub_llm.embed("guitar")).embedding
    await store.store_frame_embedding(frame.id, emb)

    hits = await store.search_similar_frames(emb, user_id=alice.id, limit=5)
    assert frame.id not in [f.id for f, _s, _sim in hits]


@pytest.mark.asyncio
async def test_graph_walk_skips_tombstoned_neighbors(store, stub_llm):
    """GC'd frames keep their edges but must never re-enter context."""
    add_embedding_cluster("guitar")
    alice = await store.create_user("dave")
    hub = await store.create_frame("guitar_hub", "entity")
    dead = await store.create_frame("forgotten_amp", "entity")
    await store.create_association(hub.id, dead.id, "related_to")

    emb = (await stub_llm.embed("guitar")).embedding
    await store.store_frame_embedding(hub.id, emb)
    await store.store_frame_embedding(dead.id, emb)

    # Tombstone via direct SQL on THIS store's file (GC's exact effect).

    from assistant.backend.db.sqlcipher import aiosqlite_connect

    async def tombstone():
        async with aiosqlite_connect(store.db_path) as db:
            await db.execute(
                "UPDATE frames SET deleted_at = datetime('now') WHERE name = ?",
                ("forgotten_amp",),
            )
            await db.commit()

    await tombstone()

    retriever = Retriever(store=store, llm_client=stub_llm)
    ctx = await retriever.retrieve(query="guitar", user_id=alice.id)
    names = [rf.frame.name for rf in ctx.retrieved_frames]
    assert "guitar_hub" in names
    assert "forgotten_amp" not in names


@pytest.mark.asyncio
async def test_gc_purges_vectors_of_tombstoned_frames(tmp_path):
    """Tombstoning must delete the vector row too (bounded candidate set)."""
    from pathlib import Path as _Path

    db_path = str(_Path(tmp_path) / "gc.db")

    from assistant.backend.db.schema import init_db
    from assistant.backend.db.sqlcipher import aiosqlite_connect

    await init_db(db_path)
    s = MemoryStore(db_path)
    f = await s.create_frame("stale_thing", "entity")
    await s.store_frame_embedding(f.id, [1.0] * 8)

    async with aiosqlite_connect(db_path) as db:
        await db.execute("UPDATE frames SET priority = 0.05 WHERE id = ?", (f.id,))
        await db.commit()

    report = await run_gc(db_path, dry_run=False)
    assert report.frames_soft_deleted == 1

    async with aiosqlite_connect(db_path) as db:
        rows = await db.execute_fetchall(
            "SELECT * FROM frame_embeddings WHERE frame_id = ?", (f.id,)
        )
    assert rows == []
