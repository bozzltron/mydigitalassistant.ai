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
    assert conflict.status == "pending"


async def test_slot_history_preserved_on_conflict(store: MemoryStore):
    frame = await store.create_frame("Daisy", "entity")
    slot, _ = await store.upsert_slot(frame.id, "color", "white")
    await store.upsert_slot(frame.id, "color", "yellow")
    history = await store.get_slot_history(slot.id)
    assert len(history) == 1
    assert history[0]["old_value"] == "white"
    assert history[0]["new_value"] == "yellow"
    assert history[0]["reason"] == "conflict_resolved"


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


async def test_get_episodes_for_frame(store: MemoryStore):
    user = await store.create_user("Alice")
    frame = await store.create_frame("Mars", "entity")
    await store.create_episode(user.id, "s1", "user", "Mars is red", [frame.id])
    eps = await store.get_episodes_for_frame(frame.id)
    assert len(eps) == 1
    assert eps[0].content == "Mars is red"


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

    await store.store_frame_embedding(frame.id, embedding)
    got = await store.get_frame_embedding(frame.id)
    assert got == embedding

    all_embeddings = await store.get_all_frame_embeddings()
    assert len(all_embeddings) == 1
    assert all_embeddings[0][0] == frame.id
    assert all_embeddings[0][1] == embedding

    await store.clear_frame_embedding(frame.id)
    assert await store.get_frame_embedding(frame.id) is None


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
