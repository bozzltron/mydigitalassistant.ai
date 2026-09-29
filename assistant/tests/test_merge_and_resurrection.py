"""Regression: consolidation merges must survive shared edges and tombstones.

Two bugs in `MemoryStore`, both of which only surface on shapes the test suite
happened to avoid:

1. `merge_frames` repointed the loser's edges *before* deduplicating them.
   `associations` is UNIQUE(from_frame_id, to_frame_id, relation_type) and the
   constraint is statement-level, so a collision aborted the entire merge and
   rolled back the slot moves already done. Two frames pointing at the same
   neighbour with the *same* relation type is the most common consolidation
   merge candidate, so this was the normal case, not an edge case. The existing
   test (test_memory_store.py::test_merge_associations_are_preserved_for_primary)
   used different relation types and so never reached the collision.

2. `create_frame`'s tombstone path set only `deleted_at = NULL`, discarding the
   type, confidence, and source the caller asked for. Retrieval keys off `type`,
   so a resurrected frame came back mis-typed with no error raised.

Note for reproducers: `forget_frame` only zeroes `priority`; it never sets
`deleted_at`. Tombstoning the way GC and consolidation do requires the explicit
UPDATE, which is what `_tombstone` does here.
"""

from __future__ import annotations

import pytest

from assistant.backend.memory.store import MemoryStore


async def _tombstone(store: MemoryStore, frame_id: int) -> None:
    """Soft-delete the way GC and consolidation do (via `deleted_at`)."""
    async with store._connect() as db:
        await db.execute(
            "UPDATE frames SET deleted_at = datetime('now') WHERE id = ?", (frame_id,)
        )
        await db.commit()


class TestMergeSharedEdges:
    """`merge_frames` with edges that collide on the UNIQUE constraint."""

    @pytest.mark.asyncio
    async def test_merge_survives_same_relation_type_to_shared_neighbour(self, store):
        """Both frames point at `hub` with relation `part_of` -- the normal case."""
        hub = await store.create_frame("hub", "entity")
        a = await store.create_frame("a", "entity")
        b = await store.create_frame("b", "entity")
        await store.create_association(a.id, hub.id, "part_of")
        await store.create_association(b.id, hub.id, "part_of")

        merged = await store.merge_frames(a.id, b.id)

        assert merged.id == a.id

    @pytest.mark.asyncio
    async def test_merge_keeps_one_edge_per_shared_neighbour(self, store):
        """The colliding edge collapses to exactly one, not two and not zero."""
        hub = await store.create_frame("hub", "entity")
        a = await store.create_frame("a", "entity")
        b = await store.create_frame("b", "entity")
        await store.create_association(a.id, hub.id, "part_of")
        await store.create_association(b.id, hub.id, "part_of")

        await store.merge_frames(a.id, b.id)

        edges = [
            (r.from_frame_id, r.to_frame_id, r.relation_type)
            for r in await store.get_all_associations()
        ]
        assert (a.id, hub.id, "part_of") in edges
        assert len([e for e in edges if e[1] == hub.id]) == 1

    @pytest.mark.asyncio
    async def test_merge_survives_collision_on_the_to_frame_side(self, store):
        """The reverse direction has the same constraint and needs its own guard."""
        hub = await store.create_frame("hub", "entity")
        a = await store.create_frame("a", "entity")
        b = await store.create_frame("b", "entity")
        # Both are targets of an edge from hub.
        await store.create_association(hub.id, a.id, "has")
        await store.create_association(hub.id, b.id, "has")

        merged = await store.merge_frames(a.id, b.id)

        assert merged.id == a.id
        edges = [(r.from_frame_id, r.to_frame_id) for r in await store.get_all_associations()]
        assert (hub.id, a.id) in edges
        assert (hub.id, b.id) not in edges

    @pytest.mark.asyncio
    async def test_merge_preserves_non_colliding_edges_from_the_loser(self, store):
        """Deduplication must not discard the loser's unrelated edges."""
        keep = await store.create_frame("keep", "entity")
        hub = await store.create_frame("hub", "entity")
        a = await store.create_frame("a", "entity")
        b = await store.create_frame("b", "entity")
        await store.create_association(a.id, hub.id, "part_of")  # collides
        await store.create_association(b.id, hub.id, "part_of")  # collides
        await store.create_association(keep.id, b.id, "related")  # must survive

        await store.merge_frames(a.id, b.id)

        edges = [(r.from_frame_id, r.to_frame_id) for r in await store.get_all_associations()]
        assert (keep.id, a.id) in edges, "the loser's unrelated edge was dropped"

    @pytest.mark.asyncio
    async def test_merge_drops_self_loop_created_by_repointing(self, store):
        """A (secondary, secondary) edge becomes (primary, secondary) on merge."""
        a = await store.create_frame("a", "entity")
        b = await store.create_frame("b", "entity")
        await store.create_association(b.id, b.id, "related")

        merged = await store.merge_frames(a.id, b.id)

        assert all(
            r.from_frame_id != r.to_frame_id for r in await store.get_all_associations()
        ), f"self-loop survived the merge onto {merged.id}"

    @pytest.mark.asyncio
    async def test_merge_still_moves_slots_and_records_history(self, store):
        """The collision guard must not cost us the slot move or its audit row."""
        a = await store.create_frame("a", "entity")
        b = await store.create_frame("b", "entity")
        await store.upsert_slot(b.id, "bk", "bv")
        hub = await store.create_frame("hub", "entity")
        await store.create_association(a.id, hub.id, "part_of")
        await store.create_association(b.id, hub.id, "part_of")

        await store.merge_frames(a.id, b.id)

        slots = {s.key: s.value for s in await store.get_slots_for_frame(a.id)}
        assert slots.get("bk") == "bv", "slot did not move to the survivor"
        moved = await store.get_slot(a.id, "bk")
        history = await store.get_slot_history(moved.id)
        assert any(h["reason"] == "merge" for h in history), "merge not audited"


class TestResurrection:
    """`create_frame` on a tombstoned name must honour the caller's arguments."""

    @pytest.mark.asyncio
    async def test_resurrection_applies_new_type_and_confidence(self, store):
        old = await store.create_frame(
            "mystery", "person", confidence=0.2, source_type="chat"
        )
        await _tombstone(store, old.id)

        new = await store.create_frame(
            "mystery", "book", confidence=0.95, source_type="user",
            source_reliability=0.8,
        )

        assert new.id == old.id, "expected the same row to be resurrected"
        assert new.type == "book", f"stale type survived: {new.type!r}"
        assert new.confidence == 0.95, f"stale confidence survived: {new.confidence}"
        assert new.source_type == "user", f"stale source survived: {new.source_type!r}"
        assert new.source_reliability == 0.8

    @pytest.mark.asyncio
    async def test_resurrection_updates_essential_and_priority(self, store):
        old = await store.create_frame("mystery", "entity", essential=0, priority=0.1)
        await _tombstone(store, old.id)

        new = await store.create_frame(
            "mystery", "entity", essential=1, priority=0.9
        )

        assert new.essential == 1, "essential flag was not restored"
        assert new.priority == 0.9, f"stale priority survived: {new.priority}"

    @pytest.mark.asyncio
    async def test_resurrection_preserves_existing_slots(self, store):
        """Slots are not part of the resurrect UPDATE; they must be left alone.

        The fix restores the frame's own columns. Slots are a separate table and
        this test pins that the fix did not start clearing them.
        """
        old = await store.create_frame("mystery", "person")
        await store.upsert_slot(old.id, "old_fact", "still here")
        await _tombstone(store, old.id)

        new = await store.create_frame("mystery", "book", confidence=0.95)

        slots = {s.key: s.value for s in await store.get_slots_for_frame(new.id)}
        assert slots.get("old_fact") == "still here"

    @pytest.mark.asyncio
    async def test_resurrection_does_not_cross_user_boundary(self, store):
        """A user must not resurrect another user's tombstoned frame."""
        alice = await store.create_user("alice")
        bob = await store.create_user("bob")
        frame = await store.create_frame("shared_name", "entity", owner_user_id=alice.id)
        await _tombstone(store, frame.id)

        # Bob creating the same name must not revive Alice's row.
        bobs = await store.create_frame("shared_name", "entity", owner_user_id=bob.id)

        assert bobs.id != frame.id, "resurrected another user's tombstoned frame"
        assert bobs.owner_user_id == bob.id
