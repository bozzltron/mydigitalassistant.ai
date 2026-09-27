"""Tests for the store's connection pool.

SQLCipher derives its key lazily, on the first read of a fresh connection
(~57ms measured, against ~0.07ms on a warm connection). The store therefore
keeps a few connections warm instead of opening one per query. These pin the
three things that makes safe: warm reuse, no state or locks carried between
callers, and no wedge of interpreter shutdown.
"""

import asyncio
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from assistant.backend.db.schema import init_db
from assistant.backend.main import db_backup, db_restore
from assistant.backend.memory.backup import (
    create_encrypted_backup,
    export_portable_brain,
    restore_encrypted_backup,
    restore_portable_brain,
)
from assistant.backend.memory.store import _POOL_SIZE, MemoryStore

requires_db_key = pytest.mark.skipif(
    not os.environ.get("DB_KEY"), reason="DB_KEY not set"
)


async def _frames(store: MemoryStore) -> int:
    async with store._connect() as db:
        rows = await db.execute_fetchall("SELECT COUNT(*) FROM frames")
    return rows[0][0]


async def test_a_connection_is_reused_across_calls(store: MemoryStore):
    """The point of the pool: consecutive calls share one connection."""
    first = None
    for _ in range(3):
        async with store._connect() as db:
            if first is None:
                first = id(db)
            assert id(db) == first, "connection was not reused"


async def test_the_pool_is_bounded(store: MemoryStore):
    """Concurrent callers must not be able to grow the pool without limit."""
    async def hold() -> None:
        async with store._connect() as db:
            await db.execute_fetchall("SELECT 1")

    await asyncio.gather(*(hold() for _ in range(_POOL_SIZE * 3)))
    assert len(store._pool) <= _POOL_SIZE, f"pool grew past its cap: {len(store._pool)}"


async def test_reentrancy_opens_a_private_connection_instead_of_waiting(store: MemoryStore):
    """A nested call cannot deadlock: an empty pool means "open one", not "wait".

    Before the pool this was free (every call had its own connection). A pool
    that waited for a checkout would reintroduce a hang, so the overflow path is
    pinned here.
    """
    outer_ids: list[int] = []
    async with store._connect() as outer:
        outer_ids.append(id(outer))
        async with store._connect() as inner:
            inner_ids = [id(inner)]
    assert len(outer_ids) == 1 and len(inner_ids) == 1


async def test_uncommitted_work_does_not_leak_into_the_next_caller(store: MemoryStore):
    """A write that never committed must not become visible to the next caller.

    A per-call connection got this from close()'s implicit rollback. Pooling
    would otherwise hand a half-finished write to whoever checks out next.
    """
    async with store._connect() as db:
        await db.execute("INSERT INTO users (name) VALUES ('half_written')")

    async with store._connect() as db:
        rows = await db.execute_fetchall("SELECT name FROM users")
    assert "half_written" not in [r[0] for r in rows]


async def test_a_failed_write_keeps_the_pool_usable(store: MemoryStore):
    """An expected error (duplicate key) must not cost the pool its connections."""
    async with store._connect() as db:
        await db.execute("INSERT INTO users (name) VALUES ('dup')")
        await db.commit()

    for _ in range(2):
        with pytest.raises(sqlite3.IntegrityError):
            async with store._connect() as db:
                await db.execute("INSERT INTO users (name) VALUES ('dup')")

    assert len(store._pool) == 1, "connection was discarded over a normal error"
    assert await _frames(store) == 0


async def test_close_empties_the_pool_and_is_idempotent(store: MemoryStore):
    await _frames(store)
    assert store._pool, "precondition: a warm connection is pooled"

    await store.close()
    assert store._pool == []

    await store.close()  # must not raise

    assert await _frames(store) == 0, "store still works after close"


async def test_a_restored_brain_is_not_served_from_a_stale_connection(
    store: MemoryStore, tmp_path
):
    """Regression: overwriting the database file is not a restore.

    Two things go wrong when the restore copies a file over the database. The
    write-ahead log the replaced database left behind gets replayed over the
    file that took its place, so the restore silently undoes itself; and a warm
    connection goes on answering from pages it read before the copy. The restore
    therefore copies through SQLite's backup API, which invalidates cached pages
    and never leaves a foreign log behind.

    Driven by calling the endpoints directly: through TestClient the app runs on
    its own event loop, the pool never crosses over, and the test would pass
    without the fix.
    """
    await store.create_frame("guitar", "entity")
    backup = await db_backup(store=store)

    await store.create_frame("pasta", "entity")
    assert store._pool, "precondition: the connection is warm and holds both frames"

    await db_restore(backup_filename=backup["backup_filename"], store=store)

    names = {f.name for f in await store.list_frames()}
    assert names == {"guitar"}, f"read a brain that is no longer on disk: {names}"


@requires_db_key
async def test_an_export_includes_writes_that_are_still_in_the_write_ahead_log(
    store: MemoryStore, tmp_path
):
    """Regression: copying the database file exports a brain that is missing data.

    A raw read of the file sees only what has been checkpointed into it. Warm
    connections mean the log is normally not empty, so the most recent writes sit
    in the log — and a portable brain exported that way is a brain that has
    quietly forgotten the last conversation. The export snapshots through SQLite's
    backup API instead.
    """
    await store.create_frame("guitar", "entity")
    await store.create_frame("pasta", "entity")
    assert Path(str(store.db_path) + "-wal").exists(), "precondition: log is not empty"

    brain = str(tmp_path / "exported.assistant-brain")
    await export_portable_brain(brain, db_path=store.db_path)

    restored = str(tmp_path / "restored.db")
    await init_db(restored)
    await restore_portable_brain(brain, db_path=restored)

    names = {f.name for f in await MemoryStore(restored).list_frames()}
    assert {"guitar", "pasta"} <= names, f"export dropped recent writes: {names}"


@requires_db_key
async def test_importing_a_portable_brain_drops_the_log_of_the_brain_it_replaces(
    store: MemoryStore, tmp_path
):
    """Regression: SQLite replays `<db>-wal` over whatever file sits at that path.

    Importing a portable brain renames new bytes on top of the live database. The
    log left at that path describes the brain being replaced, so replaying it
    resurrects the frames the import was meant to remove. It has to go with the
    file it belonged to.
    """
    await store.create_frame("guitar", "entity")
    brain = str(tmp_path / "brain.assistant-brain")
    await export_portable_brain(brain, db_path=store.db_path)

    await store.create_frame("pasta", "entity")
    assert Path(str(store.db_path) + "-wal").exists(), "precondition: log is not empty"

    await restore_portable_brain(brain, db_path=store.db_path)
    await store.close()

    names = {f.name for f in await MemoryStore(store.db_path).list_frames()}
    assert names == {"guitar"}, f"the replaced brain came back: {names}"


async def test_a_bundle_restore_drops_the_log_of_the_brain_it_replaces(
    store: MemoryStore, tmp_path
):
    """Regression: same hazard on the JSON-bundle path, which renames the file aside.

    The bundle restore moves the live database aside and calls init_db on the path
    it just vacated, so a log left at that path is replayed over an empty schema.
    """
    await store.create_frame("guitar", "entity")
    bundle = str(tmp_path / "backup.json")
    await create_encrypted_backup(bundle, db_path=store.db_path)

    await store.create_frame("pasta", "entity")
    assert Path(str(store.db_path) + "-wal").exists(), "precondition: log is not empty"

    await restore_encrypted_backup(bundle, db_path=store.db_path)
    await store.close()

    names = {f.name for f in await MemoryStore(store.db_path).list_frames()}
    assert names == {"guitar"}, f"the replaced brain came back: {names}"


# Warms a pool and then exits without closing it, which is the shape that wedges
# shutdown. Kept as a string so the scenario is exercised for real, in its own
# interpreter, rather than simulated against private state.
_WEDGE_SCRIPT = """
import asyncio, sys
from assistant.backend.db.schema import init_db
from assistant.backend.memory.store import MemoryStore

async def main():
    await init_db(sys.argv[1])
    store = MemoryStore(sys.argv[1])
    async with store._connect() as db:
        await db.execute_fetchall("SELECT 1")
    assert store._pool, "precondition: a warm connection is pooled"

asyncio.run(main())
"""


def test_a_warm_pool_does_not_wedge_process_exit(tmp_path):
    """A pool still open at exit must not stop the interpreter getting out.

    aiosqlite gives every connection a non-daemon worker thread, and CPython joins
    those from inside threading._shutdown — which runs before atexit, so a store
    still holding a pool at exit hangs the process rather than merely leaking.
    The shutdown hook closes whatever connections are still running, keyed on the
    connection rather than on the store that borrowed it.

    Run in a subprocess because the only faithful observation is whether the
    interpreter exits; calling the hook in-process would stop connections that
    belong to other tests, which is how a shutdown guard becomes a source of
    order-dependent flakiness instead of a guard.

    Scope, honestly: this covers the store going out of scope with a pool still in
    it. It does not cover a connection that outlives a store whose finalizer has
    already run — that race is what the connection-level registry exists for, and
    it was found by watching a real run wedge, not by a test.
    """
    result = subprocess.run(
        [sys.executable, "-c", _WEDGE_SCRIPT, str(tmp_path / "wedge.db")],
        capture_output=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr.decode()[-2000:]
