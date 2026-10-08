"""Episode-level semantic recall: embeddings over raw conversation turns.

Locks:
- store_episode_embedding / search_similar_episodes roundtrip (distance →
  similarity conversion, best-first ordering).
- Strict owner scoping: user B never recalls user A's turns, even with
  identical embeddings.
- min_distance gate + current-session exclusion (those turns are already
  verbatim in chat history).
- embed_missing_episodes backfill (cap respected, failures skipped).
- Retriever e2e: past conversations from OTHER sessions surface in the
  formatted context under "Related past conversations"; the empty-frames
  branch still recalls episodes.
- FK cascade: deleting an episode removes its vector.
"""

import asyncio

import pytest

from assistant.backend.config import settings
from assistant.backend.memory.retrieval import Retriever
from assistant.backend.memory.store import MemoryStore
from assistant.tests.conftest import add_embedding_cluster


def _run(coro):
    return asyncio.run(coro)


def _embed(text: str) -> list[float]:
    from assistant.tests.conftest import deterministic_embedding

    return deterministic_embedding(text)


@pytest.fixture
def seeded_store(tmp_path):
    """Store with two users and one clustered episode each."""
    from assistant.backend.db.schema import init_db

    db_path = str(tmp_path / "episodes.db")

    async def seed():
        await init_db(db_path)
        s = MemoryStore(db_path)
        alice = (await s.create_user("alice")).id
        bob = (await s.create_user("bob")).id
        ep_a = await s.create_episode(
            user_id=alice,
            session_id="s-old",
            role="user",
            content="we talked about the garden project and how rushed it felt",
        )
        await s.store_episode_embedding(
            ep_a.id, _embed("garden project urgency"), settings.embedding_model
        )
        ep_b = await s.create_episode(
            user_id=bob,
            session_id="s-old",
            role="user",
            content="we talked about the garden project and how rushed it felt",
        )
        await s.store_episode_embedding(
            ep_b.id, _embed("garden project urgency"), settings.embedding_model
        )
        return s, alice, bob, ep_a.id

    return asyncio.run(seed())


def test_roundtrip_returns_best_first(seeded_store):
    s, alice, _bob, ep_a = seeded_store
    hits = _run(
        s.search_similar_episodes(
            embedding=_embed("garden project urgency"),
            user_id=alice,
            limit=5,
            min_distance=0.7,
        embedding_model=settings.embedding_model,
        )
    )
    assert [e.id for e, _sim in hits] == [ep_a]
    assert hits[0][1] == pytest.approx(1.0)


def test_owner_isolation(seeded_store):
    s, _alice, bob, ep_a = seeded_store
    hits = _run(
        s.search_similar_episodes(
            embedding=_embed("garden project urgency"),
            user_id=bob,
            limit=10,
            min_distance=0.0,
        embedding_model=settings.embedding_model,
        )
    )
    ids = [e.id for e, _sim in hits]
    assert ep_a not in ids  # alice's turn never leaks to bob
    assert all(e.user_id == bob for e, _sim in hits)


def test_min_distance_filters_unrelated(seeded_store):
    s, alice, _bob, _ep_a = seeded_store
    hits = _run(
        s.search_similar_episodes(
            embedding=_embed("totally unrelated quantum tax forms"),
            user_id=alice,
            limit=5,
            min_distance=0.3,
        embedding_model=settings.embedding_model,
        )
    )
    assert hits == []


def test_excludes_current_session(seeded_store):
    s, alice, _bob, _ep_a = seeded_store
    fresh = _run(
        s.create_episode(
            user_id=alice,
            session_id="s-now",
            role="user",
            content="garden project came up again right now",
        )
    )
    await_emb = _embed("garden project urgency")
    _run(s.store_episode_embedding(fresh.id, await_emb, settings.embedding_model))
    hits = _run(
        s.search_similar_episodes(
            embedding=await_emb,
            user_id=alice,
            limit=10,
            min_distance=0.7,
            exclude_session_ids=["s-now"],
        embedding_model=settings.embedding_model,
        )
    )
    assert fresh.id not in [e.id for e, _sim in hits]


def test_backfill_and_cap(tmp_path):
    from assistant.backend.db.schema import init_db

    db_path = str(tmp_path / "backfill.db")

    async def scenario():
        await init_db(db_path)
        s = MemoryStore(db_path)
        uid = (await s.create_user("carol")).id
        for i in range(4):
            await s.create_episode(
                user_id=uid, session_id=f"s{i}", role="assistant", content=f"turn {i}"
            )
        assert len(await s.get_episodes_for_user(uid, limit=10)) == 4

        async def embed(text: str) -> list[float]:
            return _embed(text)

        # Cap respected.
        done = await s.embed_missing_episodes(
            embed, cap=2, embedding_model=settings.embedding_model
        )
        assert done == 2
        remaining = await s.embed_missing_episodes(
            embed, cap=None, embedding_model=settings.embedding_model
        )
        assert remaining == 2
        # Idempotent once complete.
        assert await s.embed_missing_episodes(embed, embedding_model=settings.embedding_model) == 0
        return s, uid

    asyncio.run(scenario())


def test_fk_cascade_removes_vector(tmp_path):
    from assistant.backend.db.schema import init_db

    db_path = str(tmp_path / "cascade.db")

    async def scenario():
        from assistant.backend.db.sqlcipher import aiosqlite_connect

        await init_db(db_path)
        s = MemoryStore(db_path)
        uid = (await s.create_user("dave")).id
        ep = await s.create_episode(
            user_id=uid, session_id="s1", role="user", content="gone soon"
        )
        await s.store_episode_embedding(ep.id, _embed("gone soon"), settings.embedding_model)

        async with aiosqlite_connect(db_path) as db:
            await db.execute("PRAGMA foreign_keys = ON")
            await db.execute("DELETE FROM episodes WHERE id = ?", (ep.id,))
            await db.commit()
            rows = await db.execute_fetchall("SELECT * FROM episode_embeddings")
        assert rows == []

    asyncio.run(scenario())


@pytest.mark.asyncio
async def test_retriever_surfaces_past_conversations(store, stub_llm):
    add_embedding_cluster("garden project")
    alice = await store.create_user("erin")
    old = await store.create_episode(
        user_id=alice.id,
        session_id="past-session",
        role="user",
        content="tell me about the garden project again",
    )
    emb = (await stub_llm.embed("tell me about the garden project again")).embedding
    await store.store_episode_embedding(old.id, emb, settings.embedding_model)

    retriever = Retriever(store, stub_llm)
    ctx = await retriever.retrieve(
        query="what did we decide about the garden project", user_id=alice.id, session_id="live-now"
    )
    assert any(e.id == old.id for e, _sim in ctx.past_conversations)
    assert "Related past conversations" in ctx.formatted
    assert "tell me about the garden project again" in ctx.formatted


@pytest.mark.asyncio
async def test_retriever_empty_frames_still_recalls(store, stub_llm):
    add_embedding_cluster("garden project")
    alice = await store.create_user("frank")
    old = await store.create_episode(
        user_id=alice.id, session_id="old", role="assistant", content="garden project notes"
    )
    emb = (await stub_llm.embed("garden project notes")).embedding
    await store.store_episode_embedding(old.id, emb, settings.embedding_model)

    retriever = Retriever(store, stub_llm)
    ctx = await retriever.retrieve(query="garden project", user_id=alice.id, session_id="new")
    assert ctx.retrieved_frames == []
    assert [e.id for e, _sim in ctx.past_conversations] == [old.id]


def test_excludes_named_episodes(seeded_store):
    """`exclude_episode_ids` drops specific turns (the verbatim window)."""
    s, alice, _bob, ep_a = seeded_store
    hits = _run(
        s.search_similar_episodes(
            embedding=_embed("garden project urgency"),
            user_id=alice,
            limit=10,
            min_distance=0.7,
            embedding_model=settings.embedding_model,
            exclude_episode_ids=[ep_a],
        )
    )
    assert ep_a not in [e.id for e, _sim in hits]


@pytest.mark.asyncio
async def test_retriever_recalls_current_session_beyond_the_window(store, stub_llm):
    """A long conversation can reach its own middle.

    Turns older than the verbatim window are recalled even though they are in the
    current session; the turns inside the window are not (already in the prompt).
    Previously the whole current session was excluded, so a thread could not
    recall its own older turns.
    """
    add_embedding_cluster("garden project")
    alice = await store.create_user("gina")
    session = "long-thread"

    emb = (await stub_llm.embed("garden project launch")).embedding
    old = await store.create_episode(
        user_id=alice.id,
        session_id=session,
        role="user",
        content="we decided the garden project launches in May",
    )
    await store.store_episode_embedding(old.id, emb, settings.embedding_model)

    recent_ids = []
    for i in range(settings.verbatim_history_turns + 1):
        ep = await store.create_episode(
            user_id=alice.id,
            session_id=session,
            role="user",
            content=f"recent garden turn {i}",
        )
        recent_ids.append(ep.id)
        await store.store_episode_embedding(ep.id, emb, settings.embedding_model)

    retriever = Retriever(store, stub_llm)
    ctx = await retriever.retrieve(
        query="when does the garden project launch",
        user_id=alice.id,
        session_id=session,
    )
    ids = [e.id for e, _sim in ctx.past_conversations]
    assert old.id in ids, "the current session's older turn must be recallable"
    assert not (set(recent_ids) & set(ids)), "verbatim-window turns must be excluded"
