"""Regression: a slot write and its audit row are one belief change.

`upsert_slot` used to commit twice in its insert path: the `slots` row first,
then the `slot_history` audit row. `slot_history` is the belief-revision audit
trail -- the thing the conflict UI and "what changed" history are built from --
so a failure between the two commits left a live, unaudited slot in memory. It
was also two fsyncs on the hottest write path where the database only needs one.

The pending-conflict path had the same split. The tests below force the audit
insert to fail with a SQLite trigger and assert the whole write rolls back.
"""

from __future__ import annotations

import sqlite3

import pytest

from assistant.backend.memory.store import MemoryStore


async def _fail_audit_writes(store: MemoryStore) -> None:
    """Make the next `slot_history` insert abort, at the database level.

    A trigger is the only way to fail the *second* statement of a transaction
    without also touching the first, which is exactly the window the old code
    committed inside. Installed through the store's own connection so it works
    on the encrypted database the suite runs against.
    """
    async with store._connect() as db:
        await db.execute(
            "CREATE TRIGGER audit_write_fails BEFORE INSERT ON slot_history "
            "BEGIN SELECT RAISE(ABORT, 'audit write failed'); END"
        )
        await db.commit()


class TestSlotInsertIsAtomic:
    @pytest.mark.asyncio
    async def test_a_failed_audit_write_leaves_no_slot(self, store: MemoryStore):
        user = await store.create_user("alice")
        frame = await store.create_frame("guitar", "thing", owner_user_id=user.id)
        await _fail_audit_writes(store)

        with pytest.raises(sqlite3.IntegrityError):
            await store.upsert_slot(frame.id, "model", "Stratocaster")

        # Pre-fix the slot was committed before the audit insert, so it survived
        # here with no history row explaining where it came from.
        assert await store.get_slot(frame.id, "model") is None

    @pytest.mark.asyncio
    async def test_a_successful_insert_writes_exactly_one_history_row(
        self, store: MemoryStore
    ):
        user = await store.create_user("alice")
        frame = await store.create_frame("guitar", "thing", owner_user_id=user.id)

        slot, _ = await store.upsert_slot(frame.id, "model", "Stratocaster")

        history = await store.get_slot_history(slot.id)
        assert [h["new_value"] for h in history] == ["Stratocaster"]


class TestConflictWriteIsAtomic:
    @pytest.mark.asyncio
    async def test_a_failed_audit_write_leaves_no_pending_conflict(
        self, store: MemoryStore
    ):
        user = await store.create_user("alice")
        frame = await store.create_frame("guitar", "thing", owner_user_id=user.id)

        # Establish a strong existing value, then reinforce it so the next
        # contradicting write loses the confidence ladder and lands as 'pending'
        # rather than auto-resolving.
        await store.upsert_slot(frame.id, "strings", "6")
        await store.upsert_slot(frame.id, "strings", "6")

        await _fail_audit_writes(store)

        with pytest.raises(sqlite3.IntegrityError):
            await store.upsert_slot(frame.id, "strings", "12")

        conflicts = await store.get_conflicts(status="pending")
        assert [c for c in conflicts if c.slot_key == "strings"] == []
