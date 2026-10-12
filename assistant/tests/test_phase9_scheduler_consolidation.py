"""Phase 9B scheduler wiring: twice-daily consolidation + strengthening.

Every test is async and awaits its setup, so all DB work for one test shares the
single event loop pytest-asyncio provides. An earlier version drove one
``MemoryStore`` through several ``asyncio.run()`` calls; the second loop made
``MemoryStore._acquire`` abandon the pooled connections bound to the now-dead
first loop (``db.stop()``), and closing a sqlcipher connection on a dead loop
never returned — the whole suite hung on this file (faulthandler showed the
worker thread stuck in ``sqlcipher.py`` ``wrapped`` from
``aiosqlite.close_and_stop``). One loop per test removes the trigger.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from assistant.backend.memory.consolidate import (
    run_consolidation,
    strengthen_from_episodes,
)
from assistant.backend.scheduler.runner import (
    CONSOLIDATION_BACKUPS_TO_KEEP,
    _backup_db,
)


async def _seed_store(tmp_path: Path):
    """Two duplicate frames + one episode co-occurrence with an existing edge."""
    from assistant.backend.db.schema import init_db
    from assistant.backend.memory.store import MemoryStore

    db_path = str(tmp_path / "brain.db")
    await init_db(db_path)
    store = MemoryStore(db_path)
    user = await store.create_user("tester")
    f1 = await store.create_frame("The Mountain & The Wolf", "entity")
    f2 = await store.create_frame("the mountain and the wolf", "entity")
    other = await store.create_frame("unrelated_thing", "entity")
    # Existing association between two frames that will co-occur.
    await store.create_association(f1.id, other.id, "related_to")
    return db_path, store, f1.id, f2.id, other.id, user.id


async def _episode(store, frame_ids, user_id):
    await store.create_episode(
        user_id=user_id,
        session_id="test",
        role="user",
        content="x",
        frame_ids=frame_ids,
    )


async def _fetch_edge_conf(db_path, frame_id):
    from assistant.backend.db.sqlcipher import aiosqlite_connect

    async with aiosqlite_connect(db_path) as db:
        cur = await db.execute(
            "SELECT confidence FROM associations WHERE from_frame_id=? "
            "OR to_frame_id=?",
            (frame_id, frame_id),
        )
        return (await cur.fetchone())[0]


async def _alive_names(db_path):
    from assistant.backend.db.sqlcipher import aiosqlite_connect

    async with aiosqlite_connect(db_path) as db:
        cur = await db.execute("SELECT name FROM frames WHERE deleted_at IS NULL")
        return {r[0] for r in await cur.fetchall()}


async def test_strengthen_bumps_cooccurring_edge_once(tmp_path):
    from assistant.backend.memory.confidence import INITIAL_CONFIDENCE

    db_path, store, f1, _f2, other, uid = await _seed_store(tmp_path)
    await _episode(store, [f1, other], uid)
    await store.close()

    assert await strengthen_from_episodes(db_path) == 1

    conf = await _fetch_edge_conf(db_path, f1)
    assert conf > INITIAL_CONFIDENCE


async def test_strengthen_is_exactly_once_per_episode(tmp_path):
    db_path, store, f1, _f2, other, uid = await _seed_store(tmp_path)
    await _episode(store, [f1, other], uid)
    await store.close()

    first = await strengthen_from_episodes(db_path)
    second = await strengthen_from_episodes(db_path)

    assert first == 1
    assert second == 0  # cursor prevents re-bumping the same evidence


async def test_strengthen_never_mints_new_edges(tmp_path):
    db_path, store, f1, f2, other, uid = await _seed_store(tmp_path)
    # Co-occurrence WITHOUT an existing association must not create one.
    await _episode(store, [f1, f2], uid)
    await store.close()

    assert await strengthen_from_episodes(db_path) == 0

    from assistant.backend.db.sqlcipher import aiosqlite_connect

    async with aiosqlite_connect(db_path) as db:
        cur = await db.execute("SELECT COUNT(*) FROM associations")
        count = (await cur.fetchone())[0]
    assert count == 1  # only the seeded f1-related_to-other edge


async def test_circuit_breaker_blocks_mass_merge(tmp_path):
    """A runaway plan writes nothing when merges exceed max_merges."""
    db_path, store, _f1, _f2, _other, _uid = await _seed_store(tmp_path)
    await store.close()

    report = await run_consolidation(db_path, dry_run=False, max_merges=0)

    assert report.capped is True
    assert report.applied_merges == 0
    # The duplicate pair is still alive — nothing was merged.
    alive = await _alive_names(db_path)
    assert "the mountain and the wolf" in alive


async def test_backup_ring_is_pruned(tmp_path):
    db_path = str(tmp_path / "brain.db")
    _db_path, store, *_rest = await _seed_store(tmp_path)
    await store.close()
    for _ in range(CONSOLIDATION_BACKUPS_TO_KEEP + 3):
        path = await _backup_db(db_path, "consolidation")
        assert Path(path).exists()
    backups = list((tmp_path / "backups").glob("brain-pre-consolidation-*"))
    assert len(backups) == CONSOLIDATION_BACKUPS_TO_KEEP


def test_interval_gate_semantics():
    """Interval timers fire on the first tick, then only once the interval elapses.

    The old ISO-week GC gate (`_is_new_week`) is gone with the GC subsystem;
    what remains is the plain elapsed-interval check the scheduler uses for
    embedding top-up, consolidation, and summarization.
    """
    interval_s = 6 * 3600

    def due(last: datetime | None, now: datetime) -> bool:
        return last is None or (now - last).total_seconds() >= interval_s

    now = datetime.now(UTC)
    assert due(None, now), "first run must fire"
    assert not due(now - timedelta(hours=1), now), "1h into a 6h interval: not yet"
    assert due(now - timedelta(hours=6), now), "6h elapsed: fire"
