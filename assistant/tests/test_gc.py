"""Tests for the priority decay garbage collector."""

import pytest

from assistant.backend.memory.gc import (
    DECAY_AFTER_DAYS,
    DECAY_RATE,
    SOFT_DELETE_THRESHOLD,
    compute_decayed_priority,
    run_gc,
)


class TestComputeDecayedPriority:
    def test_priority_zero_returns_none(self):
        assert compute_decayed_priority(0.0, "2020-01-01T00:00:00") is None

    def test_priority_negative_returns_none(self):
        assert compute_decayed_priority(-0.1, "2020-01-01T00:00:00") is None

    def test_priority_at_threshold_05_returns_none(self):
        assert compute_decayed_priority(0.5, "2020-01-01T00:00:00") is None

    def test_priority_above_threshold_returns_none(self):
        assert compute_decayed_priority(0.6, "2020-01-01T00:00:00") is None
        assert compute_decayed_priority(0.99, "2020-01-01T00:00:00") is None

    def test_no_last_strengthened_at_returns_none(self):
        assert compute_decayed_priority(0.4, None) is None

    def test_within_decay_window_returns_none(self):
        from datetime import UTC, datetime, timedelta
        recent = (datetime.now(UTC) - timedelta(days=DECAY_AFTER_DAYS - 1)).isoformat()
        assert compute_decayed_priority(0.4, recent) is None

    def test_past_decay_window_decays(self):
        from datetime import UTC, datetime, timedelta
        past = (datetime.now(UTC) - timedelta(days=DECAY_AFTER_DAYS + 7)).isoformat()
        new_priority = compute_decayed_priority(0.4, past)
        assert new_priority is not None
        assert 0 < new_priority < 0.4

    def test_decay_rate_applied_per_week(self):
        from datetime import UTC, datetime, timedelta
        past = (datetime.now(UTC) - timedelta(days=DECAY_AFTER_DAYS + 7)).isoformat()
        priority = 0.4
        new_priority = compute_decayed_priority(priority, past)
        expected = priority * (DECAY_RATE ** 1)
        assert new_priority is not None
        assert abs(new_priority - expected) < 0.0001

    def test_soft_delete_when_below_threshold(self):
        from datetime import UTC, datetime, timedelta
        past = (datetime.now(UTC) - timedelta(days=DECAY_AFTER_DAYS + 500)).isoformat()
        result = compute_decayed_priority(0.1, past)
        assert result == 0.0

    def test_multiple_weeks_decay(self):
        from datetime import UTC, datetime, timedelta
        past = (datetime.now(UTC) - timedelta(days=DECAY_AFTER_DAYS + 28)).isoformat()
        priority = 0.4
        new_priority = compute_decayed_priority(priority, past)
        assert new_priority is not None
        assert 0 < new_priority < priority
        assert new_priority >= SOFT_DELETE_THRESHOLD


class TestRunGc:
    @pytest.mark.asyncio
    async def test_no_eligible_slots(self, store):
        user = await store.create_user("Alice")
        await store.create_frame(
            name="test_frame", type="entity", owner_user_id=user.id
        )
        report = await run_gc(store.db_path)
        assert report.scanned == 0
        assert report.decayed == 0
        assert report.soft_deleted == 0
        assert report.errors == 0

    @pytest.mark.asyncio
    async def test_non_decay_eligible_slot_not_scanned(self, store):
        user = await store.create_user("Alice")
        frame = await store.create_frame(
            name="test_frame", type="entity", priority=0.5, owner_user_id=user.id
        )
        await store.upsert_slot(
            frame_id=frame.id, key="color", value="blue", source_type="user"
        )
        report = await run_gc(store.db_path)
        assert report.scanned == 0

    @pytest.mark.asyncio
    async def test_decayed_slot_reports_correctly(self, store):
        user = await store.create_user("Alice")
        frame = await store.create_frame(
            name="test_frame", type="entity", priority=0.3, owner_user_id=user.id
        )
        from datetime import UTC, datetime, timedelta
        old_date = (datetime.now(UTC) - timedelta(days=DECAY_AFTER_DAYS + 14)).isoformat()
        await store.upsert_slot(
            frame_id=frame.id, key="color", value="blue", source_type="user"
        )
        from assistant.backend.db.sqlcipher import aiosqlite_connect
        async with aiosqlite_connect(store.db_path) as db:
            await db.execute(
                "UPDATE slots SET last_strengthened_at = ?, priority = 0.4 WHERE frame_id = ?",
                (old_date, frame.id),
            )
            await db.commit()
        report = await run_gc(store.db_path)
        assert report.scanned == 1
        assert report.decayed == 1
        assert report.soft_deleted == 0

    @pytest.mark.asyncio
    async def test_dry_run_does_not_write(self, store):
        user = await store.create_user("Alice")
        frame = await store.create_frame(
            name="test_frame", type="entity", priority=0.3, owner_user_id=user.id
        )
        from datetime import UTC, datetime, timedelta
        old_date = (datetime.now(UTC) - timedelta(days=DECAY_AFTER_DAYS + 14)).isoformat()
        await store.upsert_slot(
            frame_id=frame.id, key="color", value="blue", source_type="user"
        )
        from assistant.backend.db.sqlcipher import aiosqlite_connect
        async with aiosqlite_connect(store.db_path) as db:
            await db.execute(
                "UPDATE slots SET last_strengthened_at = ?, priority = 0.4 WHERE frame_id = ?",
                (old_date, frame.id),
            )
            await db.commit()
        await run_gc(store.db_path, dry_run=True)
        async with aiosqlite_connect(store.db_path) as db:
            row = await db.execute_fetchall(
                "SELECT priority FROM slots WHERE frame_id = ?", (frame.id,)
            )
            assert row[0][0] == 0.4
