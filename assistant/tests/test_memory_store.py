import aiosqlite
import pytest

from assistant.backend.memory.confidence import bump_confidence, initial_confidence
from assistant.backend.memory.store import MemoryStore


async def test_create_and_get_user(store: MemoryStore):
    user = await store.create_user("Alice")
    assert user.name == "Alice"
    got = await store.get_user(user.id)
    assert got is not None
    assert got.name == "Alice"


async def test_get_user_by_name(store: MemoryStore):
    await store.create_user("Bob")
    got = await store.get_user_by_name("Bob")
    assert got is not None
    assert got.name == "Bob"


async def test_list_users(store: MemoryStore):
    alice = await store.create_user("Alice")
    bob = await store.create_user("Bob")
    users = await store.list_users()
    assert [u.id for u in users] == [alice.id, bob.id]


async def test_unique_user_name(store: MemoryStore):
    await store.create_user("Carol")
    with pytest.raises(aiosqlite.IntegrityError):
        await store.create_user("Carol")


async def test_create_and_get_frame(store: MemoryStore):
    frame = await store.create_frame("Mars", "entity")
    assert frame.name == "Mars"
    assert frame.type == "entity"
    got = await store.get_frame(frame.id)
    assert got is not None
    assert got.name == "Mars"


async def test_update_frame(store: MemoryStore):
    frame = await store.create_frame("Mars", "entity", confidence=0.5)
    updated = await store.update_frame(frame.id, name="Mars 2.0", type="event", confidence=0.9)
    assert updated.name == "Mars 2.0"
    assert updated.type == "event"
    assert updated.confidence == pytest.approx(0.9)

    got = await store.get_frame(frame.id)
    assert got.name == "Mars 2.0"


async def test_get_frame_by_name(store: MemoryStore):
    await store.create_frame("Earth", "entity")
    got = await store.get_frame_by_name("Earth")
    assert got is not None
    assert got.type == "entity"


async def test_list_frames_filtered_by_type(store: MemoryStore):
    await store.create_frame("Sun", "entity")
    await store.create_frame("Eclipse", "event")
    entities = await store.list_frames("entity")
    assert len(entities) == 1
    assert entities[0].name == "Sun"


async def test_upsert_slot_new(store: MemoryStore):
    frame = await store.create_frame("Daisy", "entity")
    slot, conflict = await store.upsert_slot(frame.id, "color", "white")
    assert slot.value == "white"
    assert slot.confidence == initial_confidence()
    assert conflict is None


async def test_get_slot(store: MemoryStore):
    frame = await store.create_frame("Daisy", "entity")
    await store.upsert_slot(frame.id, "color", "white")
    slot = await store.get_slot(frame.id, "color")
    assert slot is not None
    assert slot.value == "white"
    assert await store.get_slot(frame.id, "missing") is None


async def test_upsert_slot_repeat_bumps_confidence(store: MemoryStore):
    frame = await store.create_frame("Daisy", "entity")
    await store.upsert_slot(frame.id, "color", "white")
    slot, conflict = await store.upsert_slot(frame.id, "color", "white")
    assert slot.confidence == bump_confidence(initial_confidence())
    assert conflict is None


async def test_upsert_slot_conflict_new_wins(store: MemoryStore):
    frame = await store.create_frame("Daisy", "entity")
    await store.upsert_slot(frame.id, "color", "white")
    slot, conflict = await store.upsert_slot(frame.id, "color", "yellow")
    assert slot.value == "yellow"
    assert conflict is not None
    assert conflict.status == "auto_resolved"


async def test_upsert_slot_conflict_existing_wins(store: MemoryStore):
    frame = await store.create_frame("Daisy", "entity")
    slot1, _ = await store.upsert_slot(frame.id, "color", "white")
    # Bump confidence to max-ish so existing wins.
    for _ in range(10):
        slot1, _ = await store.upsert_slot(frame.id, "color", "white")
    slot, conflict = await store.upsert_slot(frame.id, "color", "yellow")
    assert slot.value == "white"
    assert conflict is not None
    # The ladder *decided*: existing stands. This used to be recorded as 'pending',
    # which made a decision indistinguishable from a deferral -- measured on the live
    # brain, 256 of 279 'pending' rows were this exact case, the slot still holding
    # the existing value. `resolved_value` names the winner so the row says what was
    # decided rather than that nothing was.
    assert conflict.status == "auto_resolved"
    assert conflict.resolved_value == "white"
    # And the slot was deliberately not updated: existing standing is the outcome.
    assert slot.value == "white"


async def test_slot_history_preserved_on_conflict(store: MemoryStore):
    frame = await store.create_frame("Daisy", "entity")
    slot, _ = await store.upsert_slot(frame.id, "color", "white")
    await store.upsert_slot(frame.id, "color", "yellow")
    history = await store.get_slot_history(slot.id)
    assert len(history) == 2
    # First record: initial belief
    assert history[0]["reason"] == "initial"
    assert history[0]["old_value"] is None
    assert history[0]["new_value"] == "white"
    # Second record: revise (conflict resolved)
    assert history[1]["reason"] == "revise"
    assert history[1]["old_value"] == "white"
    assert history[1]["new_value"] == "yellow"


async def test_create_and_get_association(store: MemoryStore):
    f1 = await store.create_frame("Sun", "entity")
    f2 = await store.create_frame("Earth", "entity")
    assoc = await store.create_association(f1.id, f2.id, "orbits")
    assert assoc.from_frame_id == f1.id
    assert assoc.to_frame_id == f2.id
    assert assoc.relation_type == "orbits"


async def test_get_associations_directional(store: MemoryStore):
    f1 = await store.create_frame("Sun", "entity")
    f2 = await store.create_frame("Earth", "entity")
    await store.create_association(f1.id, f2.id, "orbits")

    from_assocs = await store.get_associations_from(f1.id)
    to_assocs = await store.get_associations_to(f2.id)
    assert len(from_assocs) == 1
    assert len(to_assocs) == 1
    assert from_assocs[0].to_frame_id == f2.id
    assert to_assocs[0].from_frame_id == f1.id

    assert await store.get_associations_from(f2.id) == []
    assert await store.get_associations_to(f1.id) == []


async def test_get_all_associations_both_directions(store: MemoryStore):
    f1 = await store.create_frame("Sun", "entity")
    f2 = await store.create_frame("Earth", "entity")
    await store.create_association(f1.id, f2.id, "orbits")
    all_assocs = await store.get_all_associations_for_frame(f2.id)
    assert len(all_assocs) == 1
    assert all_assocs[0].from_frame_id == f1.id


async def test_create_episode_with_frame_ids(store: MemoryStore):
    user = await store.create_user("Alice")
    frame = await store.create_frame("Planets", "concept")
    ep = await store.create_episode(
        user.id, "session-1", "user", "Tell me about planets", [frame.id]
    )
    assert ep.user_id == user.id
    assert ep.frame_ids == [frame.id]


async def test_get_episodes_for_user_isolated(store: MemoryStore):
    a = await store.create_user("A")
    b = await store.create_user("B")
    await store.create_episode(a.id, "s1", "user", "hi")
    await store.create_episode(b.id, "s2", "user", "hello")
    a_eps = await store.get_episodes_for_user(a.id)
    assert len(a_eps) == 1
    assert a_eps[0].content == "hi"


async def test_get_episodes_for_session(store: MemoryStore):
    user = await store.create_user("Alice")
    await store.create_episode(user.id, "session-x", "user", "hello", frame_ids=[])
    await store.create_episode(user.id, "session-x", "assistant", "hi", frame_ids=[])
    await store.create_episode(user.id, "session-y", "user", "bye", frame_ids=[])
    eps = await store.get_episodes_for_session("session-x")
    assert len(eps) == 2
    assert all(ep.session_id == "session-x" for ep in eps)


async def test_merge_frames_moves_slots_and_associations(store: MemoryStore):
    primary = await store.create_frame("Mars", "entity")
    secondary = await store.create_frame("The Red Planet", "entity")
    moon = await store.create_frame("Phobos", "entity")

    await store.upsert_slot(secondary.id, "color", "red")
    await store.create_association(secondary.id, moon.id, "has_moon")

    merged = await store.merge_frames(primary.id, secondary.id)
    assert merged.id == primary.id
    slots = await store.get_slots_for_frame(primary.id)
    assert len(slots) == 1
    assert slots[0].value == "red"

    primary_assocs = await store.get_all_associations_for_frame(primary.id)
    assert len(primary_assocs) == 1
    assert primary_assocs[0].to_frame_id == moon.id
    assert await store.get_frame(secondary.id) is None


async def test_merge_frames_logs_history(store: MemoryStore):
    primary = await store.create_frame("Mars", "entity")
    secondary = await store.create_frame("The Red Planet", "entity")
    slot, _ = await store.upsert_slot(secondary.id, "color", "red")
    await store.merge_frames(primary.id, secondary.id)
    history = await store.get_slot_history(slot.id)
    assert any(h["reason"] == "merge" for h in history)


async def test_upsert_slot_with_source_episode_id(store: MemoryStore):
    user = await store.create_user("Alice")
    episode = await store.create_episode(user.id, "s1", "user", "test", frame_ids=[])
    frame = await store.create_frame("Guitar", "entity")
    slot, conflict = await store.upsert_slot(
        frame.id, "strings", "6", source_episode_id=episode.id
    )
    assert conflict is None
    assert slot.source_episode_id == episode.id


async def test_frame_embedding_crud(store: MemoryStore):
    frame = await store.create_frame("Guitar", "entity")
    embedding = [1.0, 0.0, 0.0]

    await store.store_frame_embedding(frame.id, embedding, "nomic-embed-text")
    got = await store.get_frame_embedding(frame.id, "nomic-embed-text")
    assert got == embedding

    all_embeddings = await store.get_all_frame_embeddings("nomic-embed-text")
    assert len(all_embeddings) == 1
    assert all_embeddings[0][0] == frame.id
    assert all_embeddings[0][1] == embedding

    await store.clear_frame_embedding(frame.id, "nomic-embed-text")
    assert await store.get_frame_embedding(frame.id, "nomic-embed-text") is None


async def test_get_conflicts_and_get_conflicts_for_frame(store: MemoryStore):
    frame = await store.create_frame("Daisy", "entity")
    await store.upsert_slot(frame.id, "color", "white")
    _, conflict = await store.upsert_slot(frame.id, "color", "yellow")

    all_conflicts = await store.get_conflicts()
    assert len(all_conflicts) == 1
    assert all_conflicts[0].status == "auto_resolved"

    frame_conflicts = await store.get_conflicts_for_frame(frame.id)
    assert len(frame_conflicts) == 1
    assert frame_conflicts[0].id == conflict.id

    pending = await store.get_conflicts(status="pending")
    assert pending == []


async def test_manual_override_conflict(store: MemoryStore):
    frame = await store.create_frame("Daisy", "entity")
    await store.upsert_slot(frame.id, "color", "white")
    _, conflict = await store.upsert_slot(frame.id, "color", "yellow")
    slot = await store.manual_override_conflict(conflict.id, "pink")
    assert slot.value == "pink"
    history = await store.get_slot_history(slot.id)
    assert any(h["reason"] == "manual_override" for h in history)


async def test_set_frame_priority(store: MemoryStore):
    frame = await store.create_frame("Daisy", "entity")
    assert frame.priority == 0.5
    updated = await store.set_frame_priority(frame.id, 0.8)
    assert updated.priority == 0.8


async def test_forget_frame_soft_deletes(store: MemoryStore):
    frame = await store.create_frame("Daisy", "entity")
    assert frame.priority == 0.5
    updated = await store.forget_frame(frame.id)
    assert updated.priority == 0.0


async def test_forget_frame_preserves_essential(store: MemoryStore):
    frame = await store.create_frame("Daisy", "entity", essential=1)
    updated = await store.forget_frame(frame.id)
    assert updated.priority == 0.5


async def test_set_slot_priority(store: MemoryStore):
    frame = await store.create_frame("Daisy", "entity")
    slot, _ = await store.upsert_slot(frame.id, "color", "white")
    assert slot.priority == 0.5
    updated = await store.set_slot_priority(slot.id, 0.9)
    assert updated.priority == 0.9


async def test_forget_slot_soft_deletes(store: MemoryStore):
    frame = await store.create_frame("Daisy", "entity")
    slot, _ = await store.upsert_slot(frame.id, "color", "white")
    updated = await store.forget_slot(slot.id)
    assert updated.priority == 0.0


async def test_create_frame_with_owner_and_source(store: MemoryStore):
    user = await store.create_user("alice")
    frame = await store.create_frame(
        "apples",
        "food",
        priority=0.8,
        owner_user_id=user.id,
        source_type="search",
        source_url="https://example.com/apples",
        source_reliability=0.7,
    )
    assert frame.owner_user_id == user.id
    assert frame.priority == 0.8
    assert frame.source_type == "search"
    assert frame.source_url == "https://example.com/apples"
    assert frame.source_reliability == 0.7


async def test_upsert_slot_with_source(store: MemoryStore):
    frame = await store.create_frame("apples", "food")
    slot, _ = await store.upsert_slot(
        frame.id,
        "color",
        "red",
        source_type="search",
        source_url="https://example.com/red-apples",
        source_reliability=0.8,
    )
    assert slot.source_type == "search"
    assert slot.source_url == "https://example.com/red-apples"
    assert slot.source_reliability == 0.8


async def test_create_association_with_source(store: MemoryStore):
    f1 = await store.create_frame("a", "entity")
    f2 = await store.create_frame("b", "entity")
    assoc = await store.create_association(
        f1.id,
        f2.id,
        "related",
        priority=0.9,
        source_type="user",
        source_reliability=1.0,
    )
    assert assoc.priority == 0.9
    assert assoc.source_type == "user"
    assert assoc.source_reliability == 1.0


async def test_list_frames_filters_by_owner(store: MemoryStore):
    alice = await store.create_user("alice")
    bob = await store.create_user("bob")
    f1 = await store.create_frame("private_alice", "secret", owner_user_id=alice.id)
    f2 = await store.create_frame("private_bob", "secret", owner_user_id=bob.id)
    f3 = await store.create_frame("shared", "shared")

    alice_frames = await store.list_frames(owner_user_id=alice.id)
    assert f1 in alice_frames
    assert f2 not in alice_frames
    assert f3 in alice_frames

    bob_frames = await store.list_frames(owner_user_id=bob.id)
    assert f1 not in bob_frames
    assert f2 in bob_frames
    assert f3 in bob_frames


async def test_get_all_frame_embeddings_returns_all(store: MemoryStore):
    f1 = await store.create_frame("guitar", "entity")
    f2 = await store.create_frame("music", "concept")
    await store.store_frame_embedding(f1.id, [1.0] + [0.0] * 767, "nomic-embed-text")
    await store.store_frame_embedding(f2.id, [0.5] * 768, "nomic-embed-text")

    all_embs = await store.get_all_frame_embeddings("nomic-embed-text")
    assert len(all_embs) == 2
    frame_ids = {emb[0] for emb in all_embs}
    assert f1.id in frame_ids
    assert f2.id in frame_ids


async def test_embed_frames_skips_missing_frames(store: MemoryStore, stub_llm):
    valid = await store.create_frame("guitar", "entity")

    async def embed_fn(text: str) -> list[float]:
        resp = await stub_llm.embed(text)
        return resp.embedding

    await store.embed_frames([valid.id, 9999, 8888], embed_fn, "nomic-embed-text")

    emb = await store.get_frame_embedding(valid.id, "nomic-embed-text")
    assert emb is not None


async def test_merge_associations_are_preserved_for_primary(store: MemoryStore):
    p = await store.create_frame("primary", "entity")
    s = await store.create_frame("secondary", "entity")
    unrelated = await store.create_frame("other", "entity")

    await store.create_association(p.id, unrelated.id, "related")
    await store.create_association(s.id, unrelated.id, "also_related")

    await store.merge_frames(p.id, s.id)

    primary_assocs = await store.get_all_associations_for_frame(p.id)
    assert len(primary_assocs) == 2


async def test_search_similar_frames_with_mixed_dimension_models(store: MemoryStore):
    """Vectors from different models (768 vs 1024 dims) coexist during migration.

    search_similar_frames must filter by embedding_model BEFORE computing
    vec_distance_cosine, or row visitation order crashes the query.
    Regression test for the MATERIALIZED CTE fix.
    """
    f1 = await store.create_frame("guitar", "entity")
    f2 = await store.create_frame("synth", "entity")

    await store.store_frame_embedding(f1.id, [0.9] + [0.0] * 767, "nomic-embed-text")
    await store.store_frame_embedding(f2.id, [0.9] + [0.0] * 1023, "qwen3-embedding:0.6b")

    query_768 = [1.0] + [0.0] * 767
    results = await store.search_similar_frames(
        query_768, user_id=None, embedding_model="nomic-embed-text", limit=5
    )
    assert len(results) == 1
    assert results[0][0].id == f1.id

    query_1024 = [1.0] + [0.0] * 1023
    results = await store.search_similar_frames(
        query_1024, user_id=None, embedding_model="qwen3-embedding:0.6b", limit=5
    )
    assert len(results) == 1
    assert results[0][0].id == f2.id


async def test_search_similar_frames_interleaved_model_rows(store: MemoryStore):
    """Many interleaved rows of the wrong model must not break the right model's search."""
    frames = []
    for i in range(6):
        f = await store.create_frame(f"frame_{i}", "concept")
        frames.append(f)
        model = "nomic-embed-text" if i % 2 == 0 else "other-model"
        dim = 768 if i % 2 == 0 else 384
        await store.store_frame_embedding(f.id, [0.5] * dim, model)

    results = await store.search_similar_frames(
        [0.5] * 768, user_id=None, embedding_model="nomic-embed-text", limit=10
    )
    assert {fr.name for fr, _, _ in results} == {
        "frame_0", "frame_2", "frame_4",
    }


# ---------------------------------------------------------------------------
# Embedding-model split-brain
#
# The store used to default `embedding_model` to "nomic-embed-text" on every
# write and read. Four call sites omitted it, so qwen3 vectors were written under
# the nomic label. Searches filter on the label, so 1024-dim vectors sitting
# under a 768-dim label were not wrong, just unfindable: 1894 of 1895 live frames
# had no vector the retriever could reach, and nothing warned because the metadata
# key -- the only thing the startup check looked at -- held the configured model.
#
# The parameter is now required, so a mislabel is a TypeError at the call site.
# ---------------------------------------------------------------------------

async def _flat_embedding(text: str) -> list[float]:
    return [0.1] * 768


# Every write path that takes an embedding_model. Each one used to default it to
# "nomic-embed-text", which is what let four call sites mislabel silently.
EMBEDDING_WRITES = {
    "store_frame_embedding": (1, [0.1] * 768),
    "embed_frames": ([1], _flat_embedding),
    "embed_frames_batch": ([1], [[0.1] * 768]),
    "store_episode_embedding": (1, [0.1] * 768),
    "embed_missing_episodes": (_flat_embedding,),
    "search_similar_frames": ([0.1] * 768,),
    "search_similar_episodes": ([0.1] * 768,),
    "get_frame_embedding": (1,),
    "get_all_frame_embeddings": (),
}


@pytest.mark.parametrize("method", list(EMBEDDING_WRITES))
async def test_embedding_access_requires_an_explicit_model(store: MemoryStore, method):
    """Omitting the model must fail loudly, not silently pick a stale default.

    A default is the whole bug: it was correct when written, wrong after the
    model changed, and indistinguishable from an explicit correct value at every
    call site.
    """
    with pytest.raises(TypeError):
        await getattr(store, method)(*EMBEDDING_WRITES[method])


@pytest.mark.parametrize(
    "method", ["store_frame_embedding", "embed_frames", "embed_frames_batch"]
)
async def test_frame_embedding_writes_honour_an_explicit_model(store: MemoryStore, method):
    """The explicit model is the label the vector lands under."""
    frame = await store.create_frame("why_not", "entity")
    args = EMBEDDING_WRITES[method]
    if method == "embed_frames":
        args = ([frame.id], _flat_embedding)
    elif method == "embed_frames_batch":
        args = ([frame.id], [[0.1] * 768])
    else:
        args = (frame.id, [0.1] * 768)
    await getattr(store, method)(*args, "qwen3-embedding:0.6b")

    assert await store.get_frame_embedding(frame.id, "qwen3-embedding:0.6b") == [0.1] * 768
    assert await store.get_frame_embedding(frame.id, "nomic-embed-text") is None


async def test_mislabelled_vectors_are_unreachable_not_merely_wrong(store: MemoryStore):
    """The failure mode, stated directly: the row is there, the search cannot see it.

    This is what the agent experienced. Nothing raises, nothing looks corrupt --
    the frame simply never comes back.
    """
    frame = await store.create_frame("why_not", "entity")
    await store.upsert_slot(frame.id, "label", "Friend Music Records")
    # 1024-dim qwen3 vector, written under the nomic label -- exactly what the
    # four omitting call sites produced.
    await store.store_frame_embedding(frame.id, [1.0] + [0.0] * 1023, "nomic-embed-text")

    # The label the retriever asks for does not match the label in the row, so the
    # frame is unretrievable no matter how similar the vectors are.
    assert await store.search_similar_frames(
        [1.0] + [0.0] * 1023, user_id=None, embedding_model="qwen3-embedding:0.6b", limit=10
    ) == []

    # The row is real and intact -- just filed under the wrong name.
    rows = await store.get_all_frame_embeddings("nomic-embed-text")
    assert len(rows) == 1
    assert len(rows[0][1]) == 1024


async def test_prune_frames_by_source_type_cascades(store: MemoryStore):
    """Prune (hard-delete) csv_row frames AND everything attached to them —
    slots, associations, embeddings — while unrelated memory survives."""
    parent = await store.create_frame(
        "subscribers_active.csv", "entity",
        source_type="file_upload", source_reliability=0.8,
    )
    await store.upsert_slot(
        frame_id=parent.id, key="row_count", value="3",
        priority=0.5, source_type="file_upload", source_reliability=0.8,
    )

    row_ids = []
    for i in range(3):
        rf = await store.create_frame(
            f"file_subscribers_active.csv_row_{i+1}", "record", source_type="csv_row"
        )
        row_ids.append(rf.id)
        await store.upsert_slot(
            frame_id=rf.id, key="name", value=f"user{i}", source_type="csv_row"
        )
        await store.create_association(
            parent.id, rf.id, "part_of", source_type="file_upload"
        )
        await store.store_frame_embedding(rf.id, [0.25] * 768, "nomic-embed-text")

    # Control memory of other source types must survive the prune.
    control = await store.create_frame("some fact", "entity", source_type="search")
    await store.upsert_slot(
        frame_id=control.id, key="topic", value="csv", source_type="search"
    )
    await store.create_association(
        parent.id, control.id, "part_of", source_type="file_upload"
    )
    await store.store_frame_embedding(control.id, [0.5] * 768, "nomic-embed-text")

    removed = await store.prune_frames_by_source_type("csv_row")

    assert sorted(removed) == sorted(row_ids)
    for rf_id in row_ids:
        assert await store.get_frame(rf_id) is None

    # Parent file frame + compact metadata survive (the CSV on disk remains
    # the source of truth for row data via read_file).
    assert await store.get_frame(parent.id) is not None
    row_count = await store.get_slot(parent.id, "row_count")
    assert row_count is not None and row_count.value == "3"

    # Cascades removed the pruned frames' slots, embeddings, and associations.
    async with store._connect() as db:
        placeholders = ",".join("?" * len(row_ids))
        rows = list(row_ids)
        slot_count = (
            await db.execute_fetchall(
                f"SELECT COUNT(*) FROM slots WHERE frame_id IN ({placeholders})", rows
            )
        )[0][0]
        assert slot_count == 0
        embed_count = (
            await db.execute_fetchall(
                f"SELECT COUNT(*) FROM frame_embeddings WHERE frame_id IN ({placeholders})",
                rows,
            )
        )[0][0]
        assert embed_count == 0

    assocs = await store.get_all_associations_for_frame(parent.id)
    assert {a.to_frame_id for a in assocs} == {control.id}

    # Unrelated memory is untouched.
    assert await store.get_frame(control.id) is not None
    topic = await store.get_slot(control.id, "topic")
    assert topic is not None and topic.value == "csv"


async def test_prune_frames_by_id_cascades_only_targeted_frames(store: MemoryStore):
    """prune_frames([id]) removes exactly those frames + their attached rows."""
    parent = await store.create_frame("file_data.csv", "entity", source_type="file_upload")
    keep = await store.create_frame("file_data.csv_row_1", "record", source_type="csv_row")
    drop = await store.create_frame("file_data.csv_row_2", "record", source_type="csv_row")
    await store.create_association(parent.id, keep.id, "part_of", source_type="file_upload")
    await store.create_association(parent.id, drop.id, "part_of", source_type="file_upload")
    await store.upsert_slot(frame_id=drop.id, key="name", value="bot", source_type="csv_row")
    await store.store_frame_embedding(drop.id, [0.4] * 768, "nomic-embed-text")

    assert await store.prune_frames([]) == []
    removed = await store.prune_frames([drop.id])

    assert removed == [drop.id]
    assert await store.get_frame(drop.id) is None
    assert await store.get_frame(keep.id) is not None

    # drop's slot + embedding cascaded away.
    async with store._connect() as db:
        slot_count = (
            await db.execute_fetchall(
                "SELECT COUNT(*) FROM slots WHERE frame_id = ?", (drop.id,)
            )
        )[0][0]
        assert slot_count == 0
        embed_count = (
            await db.execute_fetchall(
                "SELECT COUNT(*) FROM frame_embeddings WHERE frame_id = ?", (drop.id,)
            )
        )[0][0]
        assert embed_count == 0

    # keep's part_of edge survives; drop's edge is gone.
    assocs = await store.get_all_associations_for_frame(parent.id)
    assert {a.to_frame_id for a in assocs} == {keep.id}


async def test_prune_file_frame_removes_rows_and_cascades(store: MemoryStore):
    """prune_file_frame removes a file frame + its CSV row frames in one shot."""
    parent = await store.create_frame("file_data.csv", "entity", source_type="file_upload")
    row1 = await store.create_frame("file_data.csv_row_1", "record", source_type="csv_row")
    row2 = await store.create_frame("file_data.csv_row_2", "record", source_type="csv_row")
    other = await store.create_frame("unrelated", "entity", source_type="chat")
    await store.create_association(parent.id, row1.id, "part_of", source_type="file_upload")
    await store.create_association(parent.id, row2.id, "part_of", source_type="file_upload")
    await store.create_association(other.id, parent.id, "mentions", source_type="chat")
    await store.upsert_slot(frame_id=row1.id, key="name", value="alice", source_type="csv_row")
    await store.upsert_slot(frame_id=other.id, key="topic", value="unrelated", source_type="chat")

    removed = await store.prune_file_frame(parent.id)

    assert removed == 3
    assert await store.get_frame(parent.id) is None
    assert await store.get_frame(row1.id) is None
    assert await store.get_frame(row2.id) is None

    # Slots and associations cascade away with the pruned frames.
    async with store._connect() as db:
        slot_count = (
            await db.execute_fetchall(
                "SELECT COUNT(*) FROM slots WHERE frame_id = ?", (row1.id,)
            )
        )[0][0]
        assert slot_count == 0
        assoc_count = (
            await db.execute_fetchall(
                "SELECT COUNT(*) FROM associations "
                "WHERE from_frame_id = ? OR to_frame_id = ?",
                (parent.id, parent.id),
            )
        )[0][0]
        assert assoc_count == 0

    # Unrelated memory is untouched.
    assert await store.get_frame(other.id) is not None
    topic = await store.get_slot(other.id, "topic")
    assert topic is not None and topic.value == "unrelated"
