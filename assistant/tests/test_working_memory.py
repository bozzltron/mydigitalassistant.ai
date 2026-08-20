"""Tests for the WorkingMemory module.

Key behaviours tested:
- Frames are inserted and access_count bumps on repeat access.
- LRU eviction fires when size > max_size, removing oldest entries.
- Boost map and get_entries reflect current working memory state.
- clear() removes all entries.

Note: datetime('now') has second-level resolution in SQLite. Rapid operations
may share the same last_accessed_at timestamp, making LRU ordering under
 simultaneous/tight updates non-deterministic. Tests are designed accordingly.
"""

import pytest_asyncio

from assistant.backend.db.schema import init_db
from assistant.backend.memory.working_memory import (
    DEFAULT_BOOST,
    DEFAULT_MAX_SIZE,
    WorkingMemory,
)


@pytest_asyncio.fixture
async def wm(tmp_path):
    db_path = str(tmp_path / "wm_test.db")
    await init_db(db_path)
    return WorkingMemory(db_path=db_path, max_size=5, boost=2.0)


@pytest_asyncio.fixture
async def wm_default(tmp_path):
    db_path = str(tmp_path / "wm_default_test.db")
    await init_db(db_path)
    return WorkingMemory(db_path=db_path)


class TestTouchFrame:
    async def test_touch_new_frame_inserts_entry(self, wm):
        await wm.touch_frame(42)
        assert await wm.size() == 1
        entry = (await wm.get_entries())[0]
        assert entry.frame_id == 42
        assert entry.access_count == 1

    async def test_touch_existing_frame_bumps_count(self, wm):
        await wm.touch_frame(42)
        await wm.touch_frame(42)
        await wm.touch_frame(42)
        assert await wm.size() == 1
        entry = (await wm.get_entries())[0]
        assert entry.frame_id == 42
        assert entry.access_count == 3

    async def test_touch_multiple_distinct_frames(self, wm):
        await wm.touch_frame(1)
        await wm.touch_frame(2)
        await wm.touch_frame(3)
        assert await wm.size() == 3
        assert await wm.get_frame_ids() == {1, 2, 3}


class TestTouchFrames:
    async def test_touch_frames_batch_inserts_all(self, wm):
        await wm.touch_frames([10, 20, 30])
        assert await wm.size() == 3
        assert await wm.get_frame_ids() == {10, 20, 30}

    async def test_touch_frames_empty_list_noop(self, wm):
        await wm.touch_frames([])
        assert await wm.size() == 0

    async def test_touch_frames_updates_existing(self, wm):
        await wm.touch_frame(1)
        await wm.touch_frame(2)
        await wm.touch_frames([1, 2, 3])
        entries = await wm.get_entries()
        counts = {e.frame_id: e.access_count for e in entries}
        assert counts[1] == 2
        assert counts[2] == 2
        assert counts[3] == 1


class TestIsInWorkingMemory:
    async def test_returns_false_for_unknown_frame(self, wm):
        await wm.touch_frame(99)
        assert await wm.is_in_working_memory(1) is False

    async def test_returns_true_for_known_frame(self, wm):
        await wm.touch_frame(77)
        assert await wm.is_in_working_memory(77) is True


class TestGetBoostMap:
    async def test_boost_map_contains_all_frames(self, wm):
        await wm.touch_frames([5, 6])
        boost_map = await wm.get_boost_map()
        assert boost_map == {5: 2.0, 6: 2.0}

    async def test_empty_when_no_frames(self, wm):
        boost_map = await wm.get_boost_map()
        assert boost_map == {}


class TestGetEntries:
    async def test_entries_ordered_by_last_accessed_desc(self, wm):
        await wm.touch_frame(1)
        await wm.touch_frame(2)
        await wm.touch_frame(3)
        entries = await wm.get_entries()
        ids = [e.frame_id for e in entries]
        assert ids == [3, 2, 1]

    async def test_empty_when_no_frames(self, wm):
        entries = await wm.get_entries()
        assert entries == []


class TestGetFrameIds:
    async def test_returns_set_of_ids(self, wm):
        await wm.touch_frames([100, 200, 300])
        result = await wm.get_frame_ids()
        assert result == {100, 200, 300}


class TestSize:
    async def test_returns_current_count(self, wm):
        assert await wm.size() == 0
        await wm.touch_frame(1)
        assert await wm.size() == 1
        await wm.touch_frames([2, 3])
        assert await wm.size() == 3


class TestLRUEviction:
    async def test_evicts_lru_when_over_max_size(self, wm):
        assert wm.max_size == 5
        for i in range(7):
            await wm.touch_frame(i)
        assert await wm.size() == 5

    async def test_eviction_removes_oldest(self, wm):
        for i in range(5):
            await wm.touch_frame(i)
        await wm.touch_frame(99)
        ids = await wm.get_frame_ids()
        assert 99 in ids
        assert await wm.size() == 5
        assert 0 not in ids

    async def test_re_touch_prevents_eviction_of_that_frame(self, wm):
        await wm.touch_frames([1, 2, 3, 4])
        assert await wm.size() == 4
        await wm.touch_frames([5, 6, 7, 8, 9])
        ids = await wm.get_frame_ids()
        assert await wm.size() == 5
        assert 1 not in ids
        assert 2 not in ids
        assert 3 not in ids
        assert 4 not in ids

    async def test_batch_touch_respects_max_size(self, wm):
        await wm.touch_frames([1, 2])
        await wm.touch_frames([3, 4, 5, 6, 7])
        ids = await wm.get_frame_ids()
        assert await wm.size() == 5
        assert 1 not in ids
        assert 2 not in ids


class TestClear:
    async def test_clear_removes_all_entries(self, wm):
        await wm.touch_frames([10, 20, 30])
        await wm.clear()
        assert await wm.size() == 0
        assert await wm.get_frame_ids() == set()


class TestBoostDefaults:
    async def test_default_max_size(self, wm_default):
        assert wm_default.max_size == DEFAULT_MAX_SIZE

    async def test_default_boost(self, wm_default):
        assert wm_default.boost == DEFAULT_BOOST
        await wm_default.touch_frame(1)
        boost_map = await wm_default.get_boost_map()
        assert boost_map == {1: DEFAULT_BOOST}
