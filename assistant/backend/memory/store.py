import asyncio
import json
import logging
import re
import threading
import weakref
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import aiosqlite

from assistant.backend.db.schema import _load_sqlite_vec
from assistant.backend.db.sqlcipher import aiosqlite_connect_checked
from assistant.backend.memory.belief_revision import OperationType, revise
from assistant.backend.memory.confidence import (
    ConflictResolution,
    bump_confidence,
    default_source_reliability,
    forget_priority,
    initial_confidence,
    lower_confidence,
)
from assistant.backend.memory.models import (
    Alert,
    Association,
    Conflict,
    Episode,
    Feedback,
    Frame,
    Slot,
    User,
)

logger = logging.getLogger(__name__)

# A frame with at least this many slots is several ideas rather than one, and
# gets one vector per slot instead of a single averaged vector. Sized from the
# live distribution: 1811 of 1978 frames have fewer than 6 slots, so this only
# engages on the 167 that could actually be diluted.
CHUNK_MIN_SLOTS = 6

# An alert is memory of a type, not a row in a notifications table. Because it is
# memory it participates in retrieval, so the agent can raise one when it is
# contextually relevant rather than only when the user opens a bell. See
# plans/2026-09-30-alerts-as-memory.md.
ALERT_FRAME_TYPE = "alert"

# Slot keys that hold file *content* rather than facts about the file.
#
# Memory holds what a file **is**, never what it **contains**: the bytes live in the
# sandbox and are read verbatim. These keys were previously written on every file
# frame and merely hidden at render time, which left a stale copy that the `read_file`
# disk-failure fallback could serve in place of the real file — the model answered
# from a truncated preview believing it had read the file. Refused at write time here
# so no future writer can reintroduce it; see
# plans/2026-10-01-file-support-diagnosis.md.
FILE_CONTENT_HINT_SLOTS: tuple[str, ...] = ("file_content", "file_content_preview")


class FileContentInMemoryError(ValueError):
    """Raised when a write tries to put file content into a slot.

    File content belongs on disk. This is raised rather than logged so a caller that
    reintroduces the duplication fails loudly at the point of the mistake, instead of
    quietly recreating a copy that goes stale.
    """

# Max ids per batched IN (...) lookup. SQLite's default bound-parameter ceiling
# is 999, so stay well under it; get_all_associations_for_frames binds each id
# twice (from_ and to_), which is why this is not simply 999.
_BATCH_CHUNK = 400

# How many warm connections a store keeps. A connection is not the expensive
# part — SQLCipher derives the key lazily, on the first *read* of a fresh
# connection: measured at 57ms for a frame lookup on a fresh connection against
# 0.07ms on a warm one, while open-and-close with no read costs 0.6ms. A turn
# issues a dozen-plus store calls, so paying that per call dominated retrieval
# and it did not get better as the brain grew. Keep a few instead.
#
# More than one so a long housekeeping write (consolidation, GC) does not make
# live chat reads queue behind a single connection's worker thread. This is a
# cache, not a queue: a checkout that finds the pool empty opens a private
# connection rather than waiting, so a nested or deeply-reentrant store call
# degrades to the old open-per-call cost instead of deadlocking.
_POOL_SIZE = 3

# Every connection this module has opened and not yet closed, so nothing can
# wedge process exit. aiosqlite runs each connection on a non-daemon thread, and
# CPython joins non-daemon threads from inside threading._shutdown — after the
# atexit handlers but *before* the join completes, a live connection hangs the
# process rather than merely leaking. This registry is what makes the shutdown
# hook total: it is keyed on the connection rather than on the store that
# borrowed it, so a pool whose owner has been collected and a connection still
# checked out are both covered, whereas tracking stores left both to a finalizer
# that could already have run. Weak, so a connection is still collectable — and
# aiosqlite's own __del__ stops the thread when one is dropped without being
# closed.
_live_conns: "weakref.WeakSet[aiosqlite.Connection]" = weakref.WeakSet()


def _force_close(db: aiosqlite.Connection) -> None:
    """Close a connection without awaiting it.

    `stop()` closes the sqlite handle on the worker thread and ends it, which is
    what is left when there is no loop to await on — a finished test, a CLI run,
    interpreter shutdown — and it also clears aiosqlite's "deleted before being
    closed" ResourceWarning, which fires only if the handle is still open.
    """
    try:
        db.stop()
    except Exception as exc:
        logger.debug("Abandoning a DB connection failed: %s", exc)


def _abandon(conns: list[aiosqlite.Connection]) -> None:
    """Abandon every connection in a pool list and empty it.

    Module-level and list-taking so a store can register it as its own
    finalizer: a finalizer must not hold a reference to the object it watches,
    or the object could never be collected.
    """
    while conns:
        _force_close(conns.pop())


def _close_pools_at_exit() -> None:
    while _live_conns:
        _force_close(_live_conns.pop())


if hasattr(threading, "_register_atexit"):
    threading._register_atexit(_close_pools_at_exit)


def _parse_iso_ts(value: str | None) -> datetime | None:
    """Parse an ISO timestamp, tolerating Z suffix and missing offset.

    Naive timestamps are interpreted as UTC (the storage convention). Returns
    None for unparseable values so callers can skip them safely.
    """
    if not value:
        return None
    try:
        ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts


class MemoryStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        # Idle warm connections, and the event loop they belong to. A connection
        # is only usable by the loop that opened it. The list object is never
        # rebound, so the finalizer below always watches the live one.
        self._pool: list[aiosqlite.Connection] = []
        self._pool_loop: asyncio.AbstractEventLoop | None = None
        # A store that is collected while it still holds connections would
        # otherwise strand their worker threads, and interpreter shutdown joins
        # those before finalizers that are already too late to matter. Runs
        # whichever comes first: this, or the exit hook above.
        self._finalizer = weakref.finalize(self, _abandon, self._pool)

    async def _open(self) -> aiosqlite.Connection:
        """Open a connection with the pragmas and extension every caller needs."""
        # Validated open (see aiosqlite_connect_checked): the first read on a
        # fresh encrypted connection derives the key from page 1, which can
        # transiently fail under a concurrent WAL checkpoint.
        db = await aiosqlite_connect_checked(self.db_path)
        _live_conns.add(db)
        await db.execute("PRAGMA foreign_keys = ON")
        # Housekeeping (consolidation/GC) shares this file with live chat;
        # wait for the write lock instead of failing after the 5s default.
        await db.execute("PRAGMA busy_timeout = 15000")
        await _load_sqlite_vec(db)
        return db

    async def _acquire(self) -> aiosqlite.Connection:
        """Check out a connection, warm if one is free.

        A checkout that finds the pool empty opens a private connection rather
        than waiting, so a nested or deeply-reentrant store call costs what it
        used to instead of deadlocking. `_release` decides what to keep.
        """
        loop = asyncio.get_running_loop()
        if self._pool_loop is not loop:
            # A worker thread from a finished loop would never answer another
            # await, so connections left over from one are dropped, not reused.
            self._abandon_pool()
            self._pool_loop = loop
        if self._pool:
            return self._pool.pop()
        return await self._open()

    def _release(self, db: aiosqlite.Connection) -> None:
        """Return a settled connection to the pool, or drop it if there is no room."""
        if self._pool_loop is asyncio.get_running_loop() and len(self._pool) < _POOL_SIZE:
            self._pool.append(db)
            return
        _force_close(db)

    async def _settle(self, db: aiosqlite.Connection) -> bool:
        """Roll back anything uncommitted; report whether the connection is reusable.

        A connection that has been closed has always discarded uncommitted work,
        so a pooled one has to do it explicitly — otherwise a write that raised
        would hold the write lock and leak its rows into the next borrower.
        """
        try:
            if db.in_transaction:
                await db.rollback()
            return True
        except Exception as exc:
            logger.warning("Dropping a pooled connection that would not settle: %s", exc)
            return False

    @staticmethod
    async def _close_one(db: aiosqlite.Connection) -> None:
        try:
            await db.close()
        except Exception as exc:
            # A connection with an unfinalized statement refuses to close; stop()
            # ends its worker thread anyway, which is what we need from here.
            logger.debug("Closing a DB connection failed (%s); abandoning it", exc)
            _force_close(db)
        _live_conns.discard(db)

    def _abandon_pool(self) -> None:
        """Drop every pooled connection without awaiting, and forget the loop."""
        _abandon(self._pool)
        self._pool_loop = None

    async def close(self) -> None:
        """Close every pooled connection, draining each one. Idempotent.

        Call this when the store is finished with, and *before* replacing the
        database file underneath it. Two reasons, both about what SQLite does when
        a connection closes: it checkpoints the write-ahead log into the main
        file and deletes it, so a connection left open keeps serving the pages it
        has already read, and replays the pre-replace log over whatever file
        replaced the database.
        """
        pool, self._pool_loop = self._pool, None
        self._pool.clear()
        for db in pool:
            await self._close_one(db)

    @asynccontextmanager
    async def _connect(self):
        """Yield a DB connection, reusing a warm one when the pool has one free.

        The contract for callers is unchanged: a connection with foreign keys on,
        a busy timeout, and sqlite-vec loaded.
        """
        db = await self._acquire()
        try:
            yield db
        finally:
            if await self._settle(db):
                self._release(db)
            else:
                await self._close_one(db)

    # Users
    async def create_user(self, name: str) -> User:
        async with self._connect() as db:
            cursor = await db.execute(
                "INSERT INTO users (name) VALUES (?)",
                (name,),
            )
            await db.commit()
            row = await db.execute_fetchall(
                "SELECT id, name, created_at FROM users WHERE id = ?",
                (cursor.lastrowid,),
            )
            id_, name_, created_at = row[0]
            return User(id=id_, name=name_, created_at=created_at)

    async def get_user(self, user_id: int) -> User | None:
        async with self._connect() as db:
            row = await db.execute_fetchall(
                "SELECT id, name, created_at FROM users WHERE id = ?",
                (user_id,),
            )
            if not row:
                return None
            id_, name_, created_at = row[0]
            return User(id=id_, name=name_, created_at=created_at)

    async def get_user_by_name(self, name: str) -> User | None:
        async with self._connect() as db:
            row = await db.execute_fetchall(
                "SELECT id, name, created_at FROM users WHERE name = ?",
                (name,),
            )
            if not row:
                return None
            id_, name_, created_at = row[0]
            return User(id=id_, name=name_, created_at=created_at)

    async def list_users(self) -> list[User]:
        async with self._connect() as db:
            rows = await db.execute_fetchall(
                "SELECT id, name, created_at FROM users ORDER BY id"
            )
            return [
                User(id=id_, name=name_, created_at=created_at)
                for id_, name_, created_at in rows
            ]

    # Frames
    async def create_frame(
        self,
        name: str,
        type: str,
        confidence: float = 0.5,
        essential: int = 0,
        priority: float = 0.5,
        owner_user_id: int | None = None,
        source_type: str | None = None,
        source_url: str | None = None,
        source_reliability: float | None = None,
    ) -> Frame:
        async with self._connect() as db:
            # Soft-deleted frames keep their UNIQUE name row; recreate by
            # resurrecting instead of violating the constraint (consolidation
            # and GC both tombstone via deleted_at). Owner-scoped: never
            # resurrect another user's tombstoned frame (privacy).
            existing = await db.execute_fetchall(
                "SELECT id FROM frames WHERE name = ? AND deleted_at IS NOT NULL "
                "AND (owner_user_id IS ? OR "
                "(? IS NOT NULL AND owner_user_id IS NULL))",
                (name, owner_user_id, owner_user_id),
            )
            if existing:
                frame_id = existing[0][0]
                # Restore the row to what the caller asked for, not just un-delete
                # it. The previous version set deleted_at = NULL alone, so a
                # frame came back with its *stale* type, confidence and source
                # while the caller received no error -- and `type` is the field
                # the whole retrieval layer keys off. Slots live in a separate
                # table and are deliberately left untouched.
                await db.execute(
                    "UPDATE frames SET deleted_at = NULL, type = ?, confidence = ?, "
                    "essential = ?, priority = ?, source_type = ?, source_url = ?, "
                    "source_reliability = ?, updated_at = datetime('now') "
                    "WHERE id = ?",
                    (
                        type,
                        confidence,
                        essential,
                        priority,
                        source_type,
                        source_url,
                        source_reliability,
                        frame_id,
                    ),
                )
                await db.commit()
                return await self._get_frame_row(db, frame_id)

            try:
                cursor = await db.execute(
                    "INSERT INTO frames "
                    "(name, type, confidence, essential, priority, owner_user_id, "
                    "source_type, source_url, source_reliability) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        name,
                        type,
                        confidence,
                        essential,
                        priority,
                        owner_user_id,
                        source_type,
                        source_url,
                        source_reliability,
                    ),
                )
            except aiosqlite.IntegrityError:
                # UNIQUE(name) held by another user's tombstoned frame —
                # namespace this row instead of resurrecting their memory.
                scoped = f"{name}__u{owner_user_id}"
                cursor = await db.execute(
                    "INSERT INTO frames "
                    "(name, type, confidence, essential, priority, owner_user_id, "
                    "source_type, source_url, source_reliability) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        scoped,
                        type,
                        confidence,
                        essential,
                        priority,
                        owner_user_id,
                        source_type,
                        source_url,
                        source_reliability,
                    ),
                )
            await db.commit()
            return await self._get_frame_row(db, cursor.lastrowid)

    async def get_frame(self, frame_id: int) -> Frame | None:
        async with self._connect() as db:
            row = await db.execute_fetchall(
                "SELECT id, name, type, confidence, essential, priority, "
                "owner_user_id, source_type, source_url, source_reliability, "
                "embedding_model, created_at, updated_at, deleted_at "
                "FROM frames WHERE id = ?",
                (frame_id,),
            )
            if not row:
                return None
            return Frame(**self._frame_dict(row[0]))

    async def get_frames_by_ids(self, frame_ids: list[int]) -> dict[int, Frame]:
        """Fetch many frames in one query, keyed by id.

        Every ``_connect()`` re-derives the SQLCipher key and reloads the
        sqlite-vec extension, so per-row lookups are ruinously expensive on an
        encrypted DB. Graph traversal needs hundreds of frames; fetch them in
        batches instead. Ids are chunked to stay clear of SQLite's bound-parameter
        limit. Missing ids are simply absent from the returned mapping.
        """
        result: dict[int, Frame] = {}
        ids = [i for i in dict.fromkeys(frame_ids) if i is not None]
        for start in range(0, len(ids), _BATCH_CHUNK):
            chunk = ids[start : start + _BATCH_CHUNK]
            placeholders = ",".join("?" * len(chunk))
            async with self._connect() as db:
                rows = await db.execute_fetchall(
                    "SELECT id, name, type, confidence, essential, priority, "
                    "owner_user_id, source_type, source_url, source_reliability, "
                    "embedding_model, created_at, updated_at, deleted_at "
                    f"FROM frames WHERE id IN ({placeholders})",
                    chunk,
                )
            for row in rows:
                frame = Frame(**self._frame_dict(row))
                result[frame.id] = frame
        return result

    async def get_frame_by_name(self, name: str) -> Frame | None:
        """Exact-name lookup, excluding soft-deleted (tombstoned) frames."""
        async with self._connect() as db:
            row = await db.execute_fetchall(
                "SELECT id, name, type, confidence, essential, priority, "
                "owner_user_id, source_type, source_url, source_reliability, "
                "embedding_model, created_at, updated_at FROM frames "
                "WHERE name = ? AND deleted_at IS NULL",
                (name,),
            )
            if not row:
                return None
            return Frame(**self._frame_dict(row[0]))

    async def list_live_frame_stubs(self) -> list[tuple[int, str, str]]:
        """(id, name, type) for every non-tombstoned frame.

        Lightweight feed for canonicalization caches — excludes soft-deleted
        frames so resolution never lands on a consolidation loser.
        """
        async with self._connect() as db:
            rows = await db.execute_fetchall(
                "SELECT id, name, type FROM frames WHERE deleted_at IS NULL ORDER BY id"
            )
            return [(r[0], r[1], r[2]) for r in rows]

    async def get_alias_frame_id(self, alias_norm: str) -> int | None:
        """Resolve a normalized alias to its surviving frame id (Phase 9B).

        Returns None when unknown or when the aliases table does not exist yet
        (databases initialized before consolidation shipped).
        """
        async with self._connect() as db:
            try:
                rows = await db.execute_fetchall(
                    "SELECT frame_id FROM frame_aliases WHERE alias_norm = ?",
                    (alias_norm,),
                )
            except aiosqlite.OperationalError:
                return None
            return rows[0][0] if rows else None

    async def record_frame_alias(self, alias_norm: str, frame_id: int) -> None:
        """Record/rewrite a canonical-name mapping (used by consolidation)."""
        async with self._connect() as db:
            await db.execute(
                "INSERT INTO frame_aliases (alias_norm, frame_id) VALUES (?, ?) "
                "ON CONFLICT(alias_norm) DO UPDATE SET frame_id = excluded.frame_id",
                (alias_norm, frame_id),
            )
            await db.commit()

    async def rewrite_aliases_target(self, old_frame_id: int, new_frame_id: int) -> None:
        """Point every alias referencing old_frame_id at new_frame_id."""
        async with self._connect() as db:
            try:
                await db.execute(
                    "UPDATE frame_aliases SET frame_id = ? WHERE frame_id = ?",
                    (new_frame_id, old_frame_id),
                )
                await db.commit()
            except aiosqlite.OperationalError:
                pass

    async def list_frames(
        self, type: str | None = None, owner_user_id: int | None = None
    ) -> list[Frame]:
        async with self._connect() as db:
            if type and owner_user_id is not None:
                rows = await db.execute_fetchall(
                    "SELECT id, name, type, confidence, essential, priority, "
                    "owner_user_id, source_type, source_url, source_reliability, "
                    "embedding_model, created_at, updated_at FROM frames "
                    "WHERE type = ? AND (owner_user_id = ? OR owner_user_id IS NULL) "
                    "ORDER BY id",
                    (type, owner_user_id),
                )
            elif type:
                rows = await db.execute_fetchall(
                    "SELECT id, name, type, confidence, essential, priority, "
                    "owner_user_id, source_type, source_url, source_reliability, "
                    "embedding_model, created_at, updated_at "
                    "FROM frames WHERE type = ? ORDER BY id",
                    (type,),
                )
            elif owner_user_id is not None:
                rows = await db.execute_fetchall(
                    "SELECT id, name, type, confidence, essential, priority, "
                    "owner_user_id, source_type, source_url, source_reliability, "
                    "embedding_model, created_at, updated_at FROM frames "
                    "WHERE owner_user_id = ? OR owner_user_id IS NULL ORDER BY id",
                    (owner_user_id,),
                )
            else:
                rows = await db.execute_fetchall(
                    "SELECT id, name, type, confidence, essential, priority, "
                    "owner_user_id, source_type, source_url, source_reliability, "
                    "embedding_model, created_at, updated_at FROM frames ORDER BY id"
                )
            return [Frame(**self._frame_dict(row)) for row in rows]
    async def update_frame(self, frame_id: int, **kwargs) -> Frame:
        allowed = {
            "name",
            "type",
            "confidence",
            "essential",
            "priority",
            "owner_user_id",
            "source_type",
            "source_url",
            "source_reliability",
        }
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            raise ValueError("No valid fields to update")
        async with self._connect() as db:
            set_clause = ", ".join(f"{k} = ?" for k in updates)
            values = list(updates.values()) + [frame_id]
            await db.execute(
                f"UPDATE frames SET {set_clause}, updated_at = datetime('now') WHERE id = ?",
                values,
            )
            await db.commit()
            return await self._get_frame_row(db, frame_id)

    async def merge_frames(self, primary_id: int, secondary_id: int) -> Frame:
        async with self._connect() as db:
            # Move slots from secondary to primary, logging merges to history.
            secondary_slots = await db.execute_fetchall(
                "SELECT id, key, value, confidence, source_episode_id "
                "FROM slots WHERE frame_id = ?",
                (secondary_id,),
            )
            for slot_id, key, value, conf, src in secondary_slots:
                # Log merge before moving slot.
                await db.execute(
                    """
                    INSERT INTO slot_history (
                        slot_id, frame_id, slot_key, old_value, new_value, reason, source_episode_id
                    )
                    VALUES (?, ?, ?, ?, ?, 'merge', ?)
                    """,
                    (slot_id, primary_id, key, value, value, src),
                )
                # Upsert into primary using update to avoid unique conflict.
                existing = await db.execute_fetchall(
                    "SELECT id, value FROM slots WHERE frame_id = ? AND key = ?",
                    (primary_id, key),
                )
                if existing:
                    pri_slot_id, _ = existing[0]
                    await db.execute(
                        "UPDATE slots SET value = ?, confidence = ?, "
                        "source_episode_id = ?, updated_at = datetime('now') "
                        "WHERE id = ?",
                        (value, conf, src, pri_slot_id),
                    )
                    await db.execute(
                        "DELETE FROM slots WHERE id = ?",
                        (slot_id,),
                    )
                else:
                    await db.execute(
                        "UPDATE slots SET frame_id = ? WHERE id = ?",
                        (primary_id, slot_id),
                    )

            # Move associations from secondary to primary.
            #
            # The two UPDATEs below repoint edges, and `associations` is
            # UNIQUE(from_frame_id, to_frame_id, relation_type) -- a
            # statement-level constraint, so a collision aborts the whole merge
            # and rolls back the slot moves already done. Two frames that both
            # point at the same neighbour with the same relation is the most
            # common consolidation merge candidate, so this is not an edge case.
            #
            # Therefore the edges that *would* collide are deleted first, while
            # they are still attributable to the secondary. Each direction is
            # handled separately: an edge created by the first UPDATE cannot
            # collide with one from the second (that would require both endpoints
            # to be primary, which the != primary guards exclude).
            await db.execute(
                "DELETE FROM associations "
                "WHERE from_frame_id = ? AND to_frame_id != ? AND EXISTS ("
                "  SELECT 1 FROM associations a2 "
                "  WHERE a2.from_frame_id = ? "
                "    AND a2.to_frame_id = associations.to_frame_id "
                "    AND a2.relation_type = associations.relation_type"
                ")",
                (secondary_id, primary_id, primary_id),
            )
            await db.execute(
                "DELETE FROM associations "
                "WHERE to_frame_id = ? AND from_frame_id != ? AND EXISTS ("
                "  SELECT 1 FROM associations a2 "
                "  WHERE a2.to_frame_id = ? "
                "    AND a2.from_frame_id = associations.from_frame_id "
                "    AND a2.relation_type = associations.relation_type"
                ")",
                (secondary_id, primary_id, primary_id),
            )
            await db.execute(
                "UPDATE associations SET from_frame_id = ? "
                "WHERE from_frame_id = ? AND to_frame_id != ?",
                (primary_id, secondary_id, primary_id),
            )
            await db.execute(
                "UPDATE associations SET to_frame_id = ? "
                "WHERE to_frame_id = ? AND from_frame_id != ?",
                (primary_id, secondary_id, primary_id),
            )
            # Drop edges the repointing turned into self-loops on the survivor.
            # An edge (secondary, secondary) becomes (primary, secondary) above,
            # which the to_frame_id != primary guard does not catch.
            await db.execute(
                "DELETE FROM associations "
                "WHERE from_frame_id = to_frame_id "
                "AND (from_frame_id = ? OR from_frame_id = ?)",
                (primary_id, secondary_id),
            )
            await db.execute(
                "DELETE FROM frames WHERE id = ?",
                (secondary_id,),
            )
            await db.commit()
            return await self._get_frame_row(db, primary_id)

    async def set_frame_priority(
        self, frame_id: int, priority: float
    ) -> Frame:
        """Set frame priority. Use bump_priority() or max_priority() from confidence module."""
        async with self._connect() as db:
            await db.execute(
                "UPDATE frames SET priority = ?, updated_at = datetime('now') "
                "WHERE id = ? AND essential = 0",
                (priority, frame_id),
            )
            await db.commit()
            return await self._get_frame_row(db, frame_id)

    async def forget_frame(self, frame_id: int) -> Frame:
        """Soft-delete a frame by setting priority to 0. Does not affect essential frames."""
        return await self.set_frame_priority(frame_id, forget_priority())

    async def set_slot_priority(self, slot_id: int, priority: float) -> Slot:
        """Set slot priority."""
        async with self._connect() as db:
            await db.execute(
                "UPDATE slots SET priority = ?, updated_at = datetime('now') "
                "WHERE id = ? AND essential = 0",
                (priority, slot_id),
            )
            await db.commit()
            return await self._get_slot_row(db, slot_id)

    async def forget_slot(self, slot_id: int) -> Slot:
        """Soft-delete a slot by setting priority to 0. Does not affect essential slots."""
        return await self.set_slot_priority(slot_id, forget_priority())

    async def prune_frames(self, frame_ids: list[int]) -> list[int]:
        """Hard-delete specific frames (by id) and cascade everything attached.

        This removes frames permanently — unlike ``forget_frame`` (soft-delete to
        priority 0), which leaves the row alive in ``list_frames()`` and the brain
        graph. Deleting a frame cascades to its slots, slot_history, associations,
        frame_embeddings, frame_aliases, and working_memory rows via the ON DELETE
        CASCADE foreign keys in the schema; alerts that reference a removed frame
        keep their row with ``source_frame_id`` set to NULL (schema FK policy).

        Use for targeted removal of dangling or noisy frames (e.g. a file frame
        whose physical file was deleted), or wholesale via
        ``prune_frames_by_source_type`` for a whole category.

        Returns the ids of the requested frames (they are removed regardless of
        whether the ids existed).
        """
        if not frame_ids:
            return []
        async with self._connect() as db:
            placeholders = ",".join("?" * len(frame_ids))
            await db.execute(
                f"DELETE FROM frames WHERE id IN ({placeholders})",
                list(frame_ids),
            )
            await db.commit()
            return list(frame_ids)

    async def prune_frames_by_source_type(self, source_type: str) -> list[int]:
        """Hard-delete every frame with the given source_type and cascade attached data.

        Same semantics as ``prune_frames`` but selects the frame ids to remove by
        ``source_type``. Returns the ids of the removed frames.
        """
        async with self._connect() as db:
            rows = await db.execute_fetchall(
                "SELECT id FROM frames WHERE source_type = ? ORDER BY id",
                (source_type,),
            )
            ids = [row[0] for row in rows]
        return await self.prune_frames(ids)

    async def prune_file_frame(self, frame_id: int) -> int:
        """Hard-delete a file frame together with its CSV row frames.

        Deleting a file must clean its memory cluster, not just tombstone it:
        ``forget_frame`` sets priority 0 but the frame and its ``part_of`` row
        frames stay alive in ``list_frames()`` and the brain graph, pointing at a
        file that no longer exists. This prunes the frame and all its ``part_of``
        children (the per-row CSV frames) via the same FK-cascade path as
        ``prune_frames``. Returns the number of frames pruned (the file frame
        plus any row frames; like ``prune_frames`` the ids are pruned regardless
        of whether they still existed).
        """
        async with self._connect() as db:
            rows = await db.execute_fetchall(
                "SELECT to_frame_id FROM associations "
                "WHERE from_frame_id = ? AND relation_type = 'part_of'",
                (frame_id,),
            )
            ids = [frame_id] + [row[0] for row in rows]
        if not ids:
            return 0
        await self.prune_frames(ids)
        return len(ids)

    # Embeddings
    async def store_frame_embedding(
        self,
        frame_id: int,
        embedding: list[float],
        embedding_model: str,
        chunk_index: int = 0,
    ) -> None:
        """Store one embedding for a frame as a sqlite-vec vector.

        `chunk_index` distinguishes a frame's several vectors (see
        `_frame_to_embed_chunks`). Chunk 0 is the frame's own identity; the rest
        are its individual slots. Writes are upserts, so re-embedding a frame
        overwrites its chunks rather than accumulating them.
        """
        async with self._connect() as db:
            await db.execute(
                """
                INSERT INTO frame_embeddings
                    (frame_id, embedding_model, chunk_index, embedding, updated_at)
                VALUES (?, ?, ?, vec_f32(?), datetime('now'))
                ON CONFLICT(frame_id, embedding_model, chunk_index) DO UPDATE SET
                    embedding = vec_f32(excluded.embedding),
                    updated_at = excluded.updated_at
                """,
                (frame_id, embedding_model, chunk_index, json.dumps(embedding)),
            )
            await db.commit()

    async def store_frame_embeddings(
        self,
        frame_id: int,
        embeddings: list[list[float]],
        embedding_model: str,
    ) -> None:
        """Replace every vector a frame holds for one model, atomically.

        A whole-set write rather than a per-chunk upsert, because slots change:
        if a slot is deleted or renamed, its chunk has to go, or the frame keeps
        answering questions about something it no longer says. Chunks outside the
        new set are removed in the same transaction that adds the new ones, so a
        frame is never briefly half-indexed.
        """
        async with self._connect() as db:
            await db.execute(
                "DELETE FROM frame_embeddings "
                "WHERE frame_id = ? AND embedding_model = ?",
                (frame_id, embedding_model),
            )
            await db.executemany(
                """
                INSERT INTO frame_embeddings
                    (frame_id, embedding_model, chunk_index, embedding, updated_at)
                VALUES (?, ?, ?, vec_f32(?), datetime('now'))
                """,
                [
                    (frame_id, embedding_model, i, json.dumps(vec))
                    for i, vec in enumerate(embeddings)
                ],
            )
            await db.commit()

    async def count_frame_embedding_chunks(
        self, frame_id: int, embedding_model: str
    ) -> int:
        """How many vectors a frame holds for one model."""
        async with self._connect() as db:
            rows = await db.execute_fetchall(
                "SELECT COUNT(*) FROM frame_embeddings "
                "WHERE frame_id = ? AND embedding_model = ?",
                (frame_id, embedding_model),
            )
        return rows[0][0] if rows else 0

    async def embed_frames(
        self,
        frame_ids: list[int],
        embed_fn,  # async callable: (text) -> list[float]
        embedding_model: str,
    ) -> int:
        """Generate and store embeddings for a list of frames. Returns the count stored.

        Individual frames that fail are skipped rather than aborting the batch, but
        the failures are counted and logged, and the caller gets the number back.
        They used to be swallowed by a bare `except: continue`, which made a wholly
        broken call indistinguishable from a wholly successful one -- and that is
        how `assistant db reembed` came to report "✓ Re-embedded 2408/2408 frames"
        having stored nothing at all (it passed `llm_client.embed`, which returns an
        EmbeddingResponse, where a list[float] was expected; json.dumps then raised
        for every frame).
        """
        embedded = 0
        failures: list[str] = []
        for frame_id in frame_ids:
            try:
                frame = await self.get_frame(frame_id)
                if not frame:
                    continue
                slots = await self.get_slots_for_frame(frame_id)
                chunks = self._frame_to_embed_chunks(frame, slots)
                vectors = [await embed_fn(text) for text in chunks]
                await self.store_frame_embeddings(frame_id, vectors, embedding_model)
                embedded += 1
            except Exception as exc:
                failures.append(f"{frame_id}: {exc}")

        if failures:
            preview = "; ".join(failures[:3])
            more = f" (+{len(failures) - 3} more)" if len(failures) > 3 else ""
            logger.warning(
                "embed_frames(%s): stored %d of %d, %d failed -- %s%s",
                embedding_model,
                embedded,
                len(frame_ids),
                len(failures),
                preview,
                more,
            )
        return embedded

    async def embed_frames_batch(
        self,
        frame_ids: list[int],
        embeddings: list[list[float]],
        embedding_model: str,
    ) -> None:
        """Store pre-computed embeddings for a list of frames."""
        for frame_id, embedding in zip(frame_ids, embeddings, strict=True):
            try:
                await self.store_frame_embedding(frame_id, embedding, embedding_model)
            except Exception:
                continue

    @staticmethod
    def _frame_to_embed_text(frame: Frame, slots: list[Slot]) -> str:
        """Build embeddable text from a frame and its slots."""
        parts = [f"{frame.type}: {frame.name}"]
        for slot in slots:
            parts.append(f"  {slot.key} = {slot.value}")
        return "\n".join(parts)

    @staticmethod
    def _frame_to_embed_chunks(frame: Frame, slots: list[Slot]) -> list[str]:
        """Split a frame into the texts that get embedded, one vector each.

        A frame with few slots is already a single clean idea, so it keeps
        exactly one vector and its embedding is unchanged -- 1902 of the 1978
        live frames are in this group, and none of them need re-embedding.

        A frame with many slots is not one idea, it is a pile of them. One
        averaged vector lands in the middle of the pile and matches nothing in
        particular: measured against the live index, the 24-slot `why_not` frame
        ranked 453rd of 500 for its own name, and 1541st for a question about
        one of its slots. Measured on the same vectors, a name-only chunk ranked
        1st and a per-slot chunk ranked 4th.

        So slot-rich frames get the frame's name as one vector and each slot as
        its own, and search collapses them back to the best hit per frame.
        """
        if len(slots) < CHUNK_MIN_SLOTS:
            return [MemoryStore._frame_to_embed_text(frame, slots)]

        name_chunk = f"{frame.type}: {frame.name}"
        chunks = [name_chunk]
        for slot in slots:
            chunks.append(f"{name_chunk}\n  {slot.key} = {slot.value}")
        return chunks

    # Episode embeddings (semantic recall over raw conversation turns)
    async def store_episode_embedding(
        self,
        episode_id: int,
        embedding: list[float],
        embedding_model: str,
    ) -> None:
        """Store an embedding for an episode's verbatim content."""
        async with self._connect() as db:
            await db.execute(
                """
                INSERT INTO episode_embeddings (episode_id, embedding_model, embedding, updated_at)
                VALUES (?, ?, vec_f32(?), datetime('now'))
                ON CONFLICT(episode_id, embedding_model) DO UPDATE SET
                    embedding = vec_f32(excluded.embedding),
                    updated_at = excluded.updated_at
                """,
                (episode_id, embedding_model, json.dumps(embedding)),
            )
            await db.commit()

    async def embed_missing_episodes(
        self,
        embed_fn,  # async callable: (text) -> list[float]
        embedding_model: str,
        cap: int | None = None,
    ) -> int:
        """Embed episodes that lack a vector for this model. Returns count.

        Write-time embedding is best-effort in the orchestrator; this tops up
        anything missed (crashes, older turns, imports).
        """
        async with self._connect() as db:
            rows = await db.execute_fetchall(
                """
                SELECT e.id FROM episodes e
                WHERE NOT EXISTS (
                    SELECT 1 FROM episode_embeddings ee
                    WHERE ee.episode_id = e.id AND ee.embedding_model = ?
                )
                ORDER BY e.id DESC
                """,
                (embedding_model,),
            )
        ids = [r[0] for r in rows]
        if cap is not None:
            ids = ids[:cap]
        done = 0
        for episode_id in ids:
            try:
                row = await self._get_episode_row(episode_id)
                if row is None:
                    continue
                episode = Episode(**self._episode_dict(row))
                text = f"{episode.role}: {episode.content[:4000]}"
                embedding = await embed_fn(text)
                await self.store_episode_embedding(episode_id, embedding, embedding_model)
                done += 1
            except Exception:
                continue
        return done

    async def embed_stale_frames(
        self,
        embed_fn,  # async callable: (text) -> list[float]
        embedding_model: str,
        cap: int | None = 100,
    ) -> int:
        """Re-embed frames whose stored vectors disagree with their slots.

        Write-time embedding covers new frames and any frame an extraction
        touched, but the backend has 28 `upsert_slot` call sites and not all of
        them re-embed. The correction pipeline is one that doesn't: correcting a
        slot leaves the old value still answering for itself until something
        notices. Rather than audit call sites -- and rely on every future one
        remembering -- this checks the invariant directly: a frame's stored
        vector count must equal what its slots imply (1 below the threshold,
        1 + slot count at or above it).

        The check is two grouped counts, so it stays cheap on a full pass.
        Returns how many frames were re-embedded.
        """
        async with self._connect() as db:
            rows = await db.execute_fetchall(
                """
                SELECT f.id
                FROM frames f
                LEFT JOIN (
                    SELECT frame_id, COUNT(*) AS n
                    FROM slots GROUP BY frame_id
                ) s ON s.frame_id = f.id
                LEFT JOIN (
                    SELECT frame_id, COUNT(*) AS n
                    FROM frame_embeddings
                    WHERE embedding_model = ?
                    GROUP BY frame_id
                ) e ON e.frame_id = f.id
                WHERE f.deleted_at IS NULL
                  AND f.priority > 0
                  AND COALESCE(e.n, 0) != CASE
                        WHEN COALESCE(s.n, 0) < ? THEN 1
                        ELSE COALESCE(s.n, 0) + 1
                      END
                ORDER BY f.updated_at DESC
                LIMIT ?
                """,
                (embedding_model, CHUNK_MIN_SLOTS, cap if cap is not None else -1),
            )
        ids = [r[0] for r in rows]
        if not ids:
            return 0
        return await self.embed_frames(ids, embed_fn, embedding_model)

    async def search_similar_episodes(
        self,
        embedding: list[float],
        user_id: int | None,
        embedding_model: str,
        limit: int = 5,
        min_distance: float = 0.7,
        exclude_session_ids: list[str] | None = None,
    ) -> list[tuple[Episode, float]]:
        """Semantic search over conversation turns.

        Returns (episode, similarity) tuples, best first. Episodes are strictly
        personal: user_id=None skips the filter only for observatory/admin use;
        the retriever always passes a concrete user. exclude_session_ids drops
        turns already present verbatim as chat history.
        """
        owner_filter = (
            "" if user_id is None else "AND (e.user_id = ?)"
        )
        session_filter = ""
        # Params follow the statement's textual placeholder order:
        # model, select-distance, where-distance, min_distance,
        # [owner], [excluded sessions...], limit.
        params: list = [
            embedding_model,
            json.dumps(embedding),
            json.dumps(embedding),
            min_distance,
        ]
        if user_id is not None:
            params.append(user_id)
        if exclude_session_ids:
            session_filter = "AND e.session_id NOT IN ({})".format(
                ",".join("?" for _ in exclude_session_ids)
            )
            params.extend(exclude_session_ids)
        params.append(limit)
        async with self._connect() as db:
            rows = await db.execute_fetchall(
                f"""
                WITH candidate AS MATERIALIZED (
                    SELECT episode_id, embedding
                    FROM episode_embeddings
                    WHERE embedding_model = ?
                )
                SELECT e.id, e.user_id, e.session_id, e.role, e.content,
                       e.frame_ids, e.timestamp,
                       vec_distance_cosine(candidate.embedding, ?) as distance
                FROM candidate
                JOIN episodes e ON candidate.episode_id = e.id
                WHERE vec_distance_cosine(candidate.embedding, ?) <= ?
                  {owner_filter}
                  {session_filter}
                ORDER BY distance ASC
                LIMIT ?
                """,
                params,
            )
            return [
                (Episode(**self._episode_dict(row)), 1.0 - row[7]) for row in rows
            ]

    async def _get_episode_row(self, episode_id: int) -> tuple | None:
        async with self._connect() as db:
            rows = await db.execute_fetchall(
                "SELECT id, user_id, session_id, role, content, frame_ids, timestamp "
                "FROM episodes WHERE id = ?",
                (episode_id,),
            )
            return rows[0] if rows else None

    async def get_frame_embedding(
        self, frame_id: int, embedding_model: str
    ) -> list[float] | None:
        """Retrieve a frame's primary embedding (chunk 0) for a model."""
        async with self._connect() as db:
            row = await db.execute_fetchall(
                "SELECT vec_to_json(embedding) FROM frame_embeddings "
                "WHERE frame_id = ? AND embedding_model = ? "
                "ORDER BY chunk_index LIMIT 1",
                (frame_id, embedding_model),
            )
            if not row:
                return None
            return json.loads(row[0][0])

    async def get_all_frame_embeddings(
        self, embedding_model: str, primary_only: bool = False
    ) -> list[tuple[int, list[float]]]:
        """Get every (frame_id, embedding) pair for a model, one per chunk.

        A slot-rich frame appears more than once unless `primary_only`, which
        returns just chunk 0 -- the frame's own identity. Callers comparing
        frames to each other (duplicate detection) want that one; callers doing
        recall want all of them.
        """
        sql = (
            "SELECT frame_id, vec_to_json(embedding) FROM frame_embeddings "
            "WHERE embedding_model = ?"
        )
        if primary_only:
            sql += " AND chunk_index = 0"
        sql += " ORDER BY frame_id, chunk_index"
        async with self._connect() as db:
            rows = await db.execute_fetchall(sql, (embedding_model,))
            return [(frame_id, json.loads(embedding)) for frame_id, embedding in rows]

    async def search_similar_frames(
        self,
        embedding: list[float],
        user_id: int | None,
        embedding_model: str,
        limit: int = 10,
        min_distance: float = 0.7,
    ) -> list[tuple[Frame, list[Slot], float]]:
        """Search frames by vector similarity using sqlite-vec vec_distance_cosine.

        Returns list of (frame, slots, similarity) tuples, most similar first.
        Similarity = 1 - cosine_distance (0..1; higher is more similar).
        Filters to frames owned by user_id or with no owner (shared household
        frame); user_id=None skips ownership filtering entirely (observatory /
        admin views).
        Uses the specified embedding_model for the search.

        A frame may hold several vectors (see `_frame_to_embed_chunks`). The
        reduction to one row per frame happens in SQL, not in Python: MIN()
        distance grouped by frame, with the threshold in HAVING. Doing it after
        the LIMIT instead would let a 24-slot frame occupy 24 of the 10 result
        slots and push every other memory out of the answer.
        """
        # None-safe ownership filter keeps the MATERIALIZED query shape intact.
        owner_filter = (
            "" if user_id is None else "AND (f.owner_user_id = ? OR f.owner_user_id IS NULL)"
        )
        async with self._connect() as db:
            rows = await db.execute_fetchall(
                f"""
                -- MATERIALIZED barrier: filter by embedding_model BEFORE any
                -- vec_distance_cosine() call. Mixed-dimension rows from other
                -- models would otherwise crash distance computation depending
                -- on row visitation order.
                WITH candidate AS MATERIALIZED (
                    SELECT frame_id, embedding
                    FROM frame_embeddings
                    WHERE embedding_model = ?
                )
                SELECT f.id, f.name, f.type, f.confidence, f.essential, f.priority,
                       f.owner_user_id, f.source_type, f.source_url, f.source_reliability,
                       f.created_at, f.updated_at, f.embedding_model,
                       MIN(vec_distance_cosine(candidate.embedding, ?)) AS distance
                FROM candidate
                JOIN frames f ON candidate.frame_id = f.id
                WHERE f.deleted_at IS NULL
                  AND f.priority > 0
                  {owner_filter}
                GROUP BY f.id
                HAVING distance <= ?
                ORDER BY distance ASC
                LIMIT ?
                """,
                # Params follow the statement's textual placeholder order:
                # model, select-distance, [owner], threshold, limit. The owner
                # filter sits in WHERE, ahead of the HAVING threshold -- binding
                # these the other way round silently searches with the distance
                # as a user id and the user id as a distance.
                (
                    embedding_model,
                    json.dumps(embedding),
                    *([user_id] if user_id is not None else []),
                    min_distance,
                    limit,
                ),
            )

            results: list[tuple[Frame, list[Slot], float]] = []
            for row in rows:
                frame = Frame(
                    id=row[0],
                    name=row[1],
                    type=row[2],
                    confidence=row[3],
                    essential=row[4],
                    priority=row[5],
                    owner_user_id=row[6],
                    source_type=row[7],
                    source_url=row[8],
                    source_reliability=row[9],
                    created_at=row[10],
                    updated_at=row[11],
                    embedding_model=row[12],
                )
                slots = await self.get_slots_for_frame(frame.id)
                results.append((frame, slots, 1.0 - row[13]))

            return results

    async def clear_frame_embedding(
        self, frame_id: int, embedding_model: str
    ) -> None:
        """Remove every embedding a frame holds for a model, chunks included."""
        async with self._connect() as db:
            await db.execute(
                "DELETE FROM frame_embeddings WHERE frame_id = ? AND embedding_model = ?",
                (frame_id, embedding_model),
            )
            await db.commit()

    # Slots
    async def set_derived_slot(
        self,
        frame_id: int,
        key: str,
        value: str,
        source_type: str | None = None,
    ) -> None:
        """Write a slot whose value is *derived*, not asserted by a source.

        A belief enters memory because something claims it: the user said it, a
        page said it. A derived value is computed from other memory -- a summary's
        prose, its entity list, a turn counter. Recomputing it and comparing to the
        last computation is not a disagreement, so routing these through
        ``upsert_slot`` recorded thousands of conflicts on zero disputes: the
        summarizer produced 1,172 rows from counters and timestamps alone, and one
        more per content slot per run until summaries were moved here too.

        Writes the value and nothing else -- no conflict, no slot_history, no
        confidence change. ``updated_at`` still moves, so the fact that a derived
        value changed remains visible.
        """
        async with self._connect() as db:
            await db.execute(
                "INSERT INTO slots "
                "(frame_id, key, value, confidence, essential, priority, "
                "source_type, last_strengthened_at) "
                "VALUES (?, ?, ?, ?, 0, 0.5, ?, datetime('now')) "
                "ON CONFLICT(frame_id, key) DO UPDATE SET "
                "value = excluded.value, "
                "updated_at = datetime('now')",
                (frame_id, key, value, initial_confidence(), source_type),
            )
            await db.commit()

    async def upsert_slot(
        self,
        frame_id: int,
        key: str,
        value: str,
        source_episode_id: int | None = None,
        essential: int = 0,
        priority: float = 0.5,
        source_type: str | None = None,
        source_url: str | None = None,
        source_reliability: float | None = None,
    ) -> tuple[Slot, Conflict | None]:
        if key in FILE_CONTENT_HINT_SLOTS:
            # File content belongs on disk, read verbatim. Refused here because the
            # store is where every writer funnels, so this cannot be sidestepped by
            # a new call site the way a per-writer fix could.
            raise FileContentInMemoryError(
                f"slot key {key!r} holds file content, which belongs in the sandbox, "
                "not in memory. Store what the file IS (name, path, size); read what "
                "it CONTAINS with read_file. See "
                "plans/2026-10-01-file-support-diagnosis.md"
            )
        if not key or not str(key).strip():
            # A slot with no key is an object with no identity: nothing can look it
            # up, and it inflates every count that iterates slots.
            raise ValueError("upsert_slot requires a non-blank key")
        if value is None or not str(value).strip():
            # A blank value is not a fact. This is the store-level counterpart to the
            # extraction-path guard, and it exists because the extraction guard was
            # not enough: CSV row ingestion writes one slot per column, including
            # empty cells, which produced 65 blank-value slots in a single day
            # (measured 2026-10-01). Refusing here covers every writer, not just the
            # extractor — the same reasoning as the content guard above.
            #
            # An empty CSV cell is an absent fact; not writing a slot for it is the
            # accurate representation, not a loss.
            raise ValueError(
                f"upsert_slot requires a non-blank value (frame_id={frame_id}, "
                f"key={key!r})"
            )
        async with self._connect() as db:
            existing = await db.execute_fetchall(
                "SELECT id, value, confidence, essential, source_reliability, priority FROM slots "
                "WHERE frame_id = ? AND key = ?",
                (frame_id, key),
            )
            if not existing:
                eff_rel = (
                    source_reliability
                    if source_reliability is not None
                    else default_source_reliability(source_type)
                )
                cursor = await db.execute(
                    "INSERT INTO slots "
                    "(frame_id, key, value, confidence, essential, priority, "
                    "source_type, source_url, source_reliability, source_episode_id, "
                    "last_strengthened_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))",
                    (
                        frame_id,
                        key,
                        value,
                        initial_confidence(),
                        essential,
                        priority,
                        source_type,
                        source_url,
                        eff_rel,
                        source_episode_id,
                    ),
                )
                slot_id = cursor.lastrowid
                # No commit between the slot and its history row: they are one
                # belief change. Committing the slot alone left an unaudited
                # value behind if the history insert failed, and slot_history is
                # the belief-revision audit trail. It was also two fsyncs on the
                # hottest write path where one suffices.
                await db.execute(
                    """
                    INSERT INTO slot_history (
                        slot_id, frame_id, slot_key, old_value, new_value, reason, source_episode_id
                    )
                    VALUES (?, ?, ?, NULL, ?, ?, ?)
                    """,
                    (slot_id, frame_id, key, value, OperationType.INITIAL.value, source_episode_id),
                )
                await db.commit()
                slot = await self._get_slot_row(db, slot_id)
                return slot, None

            (
                slot_id,
                existing_value,
                existing_confidence,
                existing_essential,
                existing_rel,
                existing_pri,
            ) = existing[0]

            if existing_value == value:
                new_conf = bump_confidence(existing_confidence)
                await db.execute(
                    "UPDATE slots "
                    "SET confidence = ?, essential = ?, updated_at = datetime('now'), "
                    "last_strengthened_at = datetime('now') "
                    "WHERE id = ?",
                    (new_conf, existing_essential, slot_id),
                )
                await db.execute(
                    """
                    INSERT INTO slot_history (
                        slot_id, frame_id, slot_key, old_value, new_value, reason, source_episode_id
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        slot_id, frame_id, key, existing_value, value,
                        OperationType.EXPAND.value, source_episode_id,
                    ),
                )
                await db.commit()
                slot = await self._get_slot_row(db, slot_id)
                return slot, None

            existing_slot = {
                "value": existing_value,
                "confidence": existing_confidence,
                "source_reliability": existing_rel,
                "priority": existing_pri,
            }
            revision_result = revise(
                existing_slot=existing_slot,
                new_value=value,
                source_type=source_type,
                new_source_reliability=source_reliability,
                new_priority=priority,
            )

            conflict: Conflict | None = None

            if revision_result.operation == OperationType.REVISE:
                # The provenance each side carried at decision time. Recorded for
                # every resolve, because without it a past decision cannot be
                # audited: slot_history keeps the values but not the reliability,
                # which made the conflict_ladder_value experiment 100%
                # unreconstructable. `source_reliability` is recorded as received
                # (possibly None), not as defaulted, so the row shows what the
                # decision actually ran on.
                provenance = (
                    existing_rel,
                    source_reliability,
                    existing_confidence,
                    initial_confidence(),
                    existing_pri,
                    priority,
                )
                if revision_result.resolution == ConflictResolution.NEW_WINS:
                    await db.execute(
                        "UPDATE slots "
                        "SET value = ?, confidence = ?, essential = ?, source_episode_id = ?, "
                        "source_type = ?, source_url = ?, source_reliability = ?, "
                        "updated_at = datetime('now'), last_strengthened_at = datetime('now') "
                        "WHERE id = ?",
                        (value, initial_confidence(), existing_essential, source_episode_id,
                         source_type, source_url, source_reliability, slot_id),
                    )
                    await db.execute(
                        """
                        INSERT INTO slot_history (
                            slot_id, frame_id, slot_key, old_value, new_value,
                            reason, source_episode_id
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            slot_id, frame_id, key, existing_value, value,
                            OperationType.REVISE.value, source_episode_id,
                        ),
                    )
                    cursor = await db.execute(
                        """
                        INSERT INTO conflicts (
                            frame_id, slot_key, existing_value, new_value,
                            resolved_value, status, resolved_at,
                            existing_source_reliability, new_source_reliability,
                            existing_confidence, new_confidence,
                            existing_priority, new_priority
                        )
                        VALUES (?, ?, ?, ?, ?, 'auto_resolved', datetime('now'),
                                ?, ?, ?, ?, ?, ?)
                        """,
                        (frame_id, key, existing_value, value, value, *provenance),
                    )
                    await db.commit()
                    conflict = await self._get_conflict_row(db, cursor.lastrowid)
                else:
                    # EXISTING_WINS: the ladder *decided*, and the existing value
                    # stands. Recording that as 'pending' made a decision
                    # indistinguishable from a deferral -- measured on the live brain,
                    # 256 of 279 'pending' rows were exactly this: the slot still
                    # held the existing value, so the decision had been applied and
                    # the label was wrong. 92% of the apparent backlog was applied
                    # decisions mislabelled as open questions.
                    #
                    # `resolved_value` carries the winner (the existing value) so the
                    # row says what was decided, matching the shape
                    # `manual_override_conflict` already uses on the human path. The
                    # slot is deliberately NOT updated: existing standing is the
                    # correct outcome, not an omission.
                    cursor = await db.execute(
                        """
                        INSERT INTO conflicts (
                            frame_id, slot_key, existing_value, new_value,
                            resolved_value, status, resolved_at,
                            existing_source_reliability, new_source_reliability,
                            existing_confidence, new_confidence,
                            existing_priority, new_priority
                        )
                        VALUES (?, ?, ?, ?, ?, 'auto_resolved', datetime('now'),
                                ?, ?, ?, ?, ?, ?)
                        """,
                        (frame_id, key, existing_value, value, existing_value, *provenance),
                    )
                    conflict_id = cursor.lastrowid
                    await db.execute(
                        """
                        INSERT INTO slot_history (
                            slot_id, frame_id, slot_key, old_value, new_value,
                            reason, source_episode_id
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            slot_id, frame_id, key, existing_value, value,
                            OperationType.REVISE.value, source_episode_id,
                        ),
                    )
                    # One belief change, one transaction: the conflict row and
                    # its history entry commit together, so an interrupted write
                    # cannot leave a conflict with no audit trail.
                    await db.commit()
                    conflict = await self._get_conflict_row(db, conflict_id)

            slot = await self._get_slot_row(db, slot_id)
            return slot, conflict

    async def get_slot(self, frame_id: int, key: str) -> Slot | None:
        async with self._connect() as db:
            row = await db.execute_fetchall(
                "SELECT id, frame_id, key, value, confidence, essential, priority, "
                "source_type, source_url, source_reliability, source_episode_id, "
                "updated_at, last_strengthened_at FROM slots WHERE frame_id = ? AND key = ?",
                (frame_id, key),
            )
            if not row:
                return None
            return Slot(**self._slot_dict(row[0]))

    async def get_slot_by_id(self, slot_id: int) -> Slot | None:
        async with self._connect() as db:
            row = await db.execute_fetchall(
                "SELECT id, frame_id, key, value, confidence, essential, priority, "
                "source_type, source_url, source_reliability, source_episode_id, "
                "updated_at, last_strengthened_at FROM slots WHERE id = ?",
                (slot_id,),
            )
            if not row:
                return None
            return Slot(**self._slot_dict(row[0]))

    async def get_slots_for_frame(self, frame_id: int) -> list[Slot]:
        async with self._connect() as db:
            rows = await db.execute_fetchall(
                "SELECT id, frame_id, key, value, confidence, essential, priority, "
                "source_type, source_url, source_reliability, source_episode_id, "
                "updated_at, last_strengthened_at FROM slots WHERE frame_id = ? ORDER BY id",
                (frame_id,),
            )
            return [Slot(**self._slot_dict(row)) for row in rows]

    async def get_slots_for_frames(self, frame_ids: list[int]) -> dict[int, list[Slot]]:
        """Fetch slots for many frames in one query, keyed by frame id.

        See get_frames_by_ids for why per-frame lookups are not viable here.
        Frames with no slots are absent from the mapping.
        """
        out: dict[int, list[Slot]] = {}
        ids = list(dict.fromkeys(frame_ids))
        for start in range(0, len(ids), _BATCH_CHUNK):
            chunk = ids[start : start + _BATCH_CHUNK]
            placeholders = ",".join("?" * len(chunk))
            async with self._connect() as db:
                rows = await db.execute_fetchall(
                    "SELECT id, frame_id, key, value, confidence, essential, priority, "
                    "source_type, source_url, source_reliability, source_episode_id, "
                    f"updated_at, last_strengthened_at FROM slots "
                    f"WHERE frame_id IN ({placeholders}) ORDER BY id",
                    chunk,
                )
            for row in rows:
                slot = Slot(**self._slot_dict(row))
                out.setdefault(slot.frame_id, []).append(slot)
        return out

    async def get_slot_history(self, slot_id: int) -> list[dict]:
        async with self._connect() as db:
            rows = await db.execute_fetchall(
                "SELECT id, slot_id, frame_id, slot_key, old_value, new_value, "
                "reason, source_episode_id, timestamp "
                "FROM slot_history WHERE slot_id = ? ORDER BY id",
                (slot_id,),
            )
            return [
                {
                    "id": r[0],
                    "slot_id": r[1],
                    "frame_id": r[2],
                    "slot_key": r[3],
                    "old_value": r[4],
                    "new_value": r[5],
                    "reason": r[6],
                    "source_episode_id": r[7],
                    "timestamp": r[8],
                }
                for r in rows
            ]

    # Associations
    async def create_association(
        self,
        from_frame_id: int,
        to_frame_id: int,
        relation_type: str,
        confidence: float = 0.5,
        essential: int = 0,
        priority: float = 0.5,
        source_type: str | None = None,
        source_url: str | None = None,
        source_reliability: float | None = None,
    ) -> Association:
        async with self._connect() as db:
            existing = await db.execute_fetchall(
                "SELECT id, confidence FROM associations "
                "WHERE from_frame_id = ? AND to_frame_id = ? AND relation_type = ?",
                (from_frame_id, to_frame_id, relation_type),
            )
            if existing:
                assoc_id, current_confidence = existing[0]
                new_confidence = bump_confidence(current_confidence)
                await db.execute(
                    "UPDATE associations SET confidence = ? WHERE id = ?",
                    (new_confidence, assoc_id),
                )
                await db.commit()
                return await self._get_association_row(db, assoc_id)

            cursor = await db.execute(
                "INSERT INTO associations "
                "(from_frame_id, to_frame_id, relation_type, confidence, essential, "
                "priority, source_type, source_url, source_reliability) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    from_frame_id,
                    to_frame_id,
                    relation_type,
                    confidence,
                    essential,
                    priority,
                    source_type,
                    source_url,
                    source_reliability,
                ),
            )
            await db.commit()
            return await self._get_association_row(db, cursor.lastrowid)

    async def get_associations_to(self, frame_id: int) -> list[Association]:
        async with self._connect() as db:
            rows = await db.execute_fetchall(
                "SELECT id, from_frame_id, to_frame_id, relation_type, confidence, "
                "essential, priority, source_type, source_url, source_reliability, "
                "embedding_model, created_at FROM associations "
                "WHERE to_frame_id = ? ORDER BY id",
                (frame_id,),
            )
            return [Association(**self._association_dict(row)) for row in rows]

    async def get_associations_from(self, frame_id: int) -> list[Association]:
        async with self._connect() as db:
            rows = await db.execute_fetchall(
                "SELECT id, from_frame_id, to_frame_id, relation_type, confidence, "
                "essential, priority, source_type, source_url, source_reliability, "
                "embedding_model, created_at FROM associations WHERE from_frame_id = ? ORDER BY id",
                (frame_id,),
            )
            return [Association(**self._association_dict(row)) for row in rows]

    async def get_all_associations_for_frame(self, frame_id: int) -> list[Association]:
        async with self._connect() as db:
            rows = await db.execute_fetchall(
                """
                SELECT id, from_frame_id, to_frame_id, relation_type, confidence,
                       essential, priority, source_type, source_url, source_reliability,
                       embedding_model, created_at
                FROM associations
                WHERE from_frame_id = ? OR to_frame_id = ?
                ORDER BY id
                """,
                (frame_id, frame_id),
            )
            return [Association(**self._association_dict(row)) for row in rows]

    async def get_all_associations_for_frames(
        self, frame_ids: list[int]
    ) -> dict[int, list[Association]]:
        """All edges touching any of ``frame_ids``, keyed by the queried id.

        The graph walk expands a whole frontier per hop, so it needs every edge
        leaving the frontier in one round trip rather than one per frame. An
        edge between two queried frames appears under both keys.
        """
        out: dict[int, list[Association]] = {i: [] for i in frame_ids}
        ids = list(dict.fromkeys(frame_ids))
        for start in range(0, len(ids), _BATCH_CHUNK):
            chunk = ids[start : start + _BATCH_CHUNK]
            placeholders = ",".join("?" * len(chunk))
            async with self._connect() as db:
                rows = await db.execute_fetchall(
                    f"""
                    SELECT id, from_frame_id, to_frame_id, relation_type, confidence,
                           essential, priority, source_type, source_url,
                           source_reliability, embedding_model, created_at
                    FROM associations
                    WHERE from_frame_id IN ({placeholders})
                       OR to_frame_id IN ({placeholders})
                    ORDER BY id
                    """,
                    chunk + chunk,
                )
            for row in rows:
                assoc = Association(**self._association_dict(row))
                if assoc.from_frame_id in out:
                    out[assoc.from_frame_id].append(assoc)
                if assoc.to_frame_id in out:
                    out[assoc.to_frame_id].append(assoc)
        return out

    async def get_all_associations(self) -> list[Association]:
        async with self._connect() as db:
            rows = await db.execute_fetchall(
                """SELECT id, from_frame_id, to_frame_id, relation_type, confidence,
                          essential, priority, source_type, source_url, source_reliability,
                          embedding_model, created_at
                   FROM associations ORDER BY id"""
            )
            return [Association(**self._association_dict(row)) for row in rows]

    # Episodes
    async def create_episode(
        self,
        user_id: int,
        session_id: str,
        role: str,
        content: str,
        frame_ids: list[int] | None = None,
        reasoning_trace: str | None = None,
        search_info: str | None = None,
    ) -> Episode:
        frame_ids = frame_ids or []
        async with self._connect() as db:
            cursor = await db.execute(
                "INSERT INTO episodes (user_id, session_id, role, content, "
                "frame_ids, reasoning_trace, search_info) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    user_id,
                    session_id,
                    role,
                    content,
                    json.dumps(frame_ids),
                    reasoning_trace,
                    search_info,
                ),
            )
            await db.commit()
            # Fetch the inserted row directly using the lastrowid
            row = await db.execute_fetchall(
                "SELECT id, user_id, session_id, role, content, frame_ids, "
                "timestamp, reasoning_trace, search_info FROM episodes WHERE id = ?",
                (cursor.lastrowid,),
            )
            if not row:
                raise ValueError("Failed to retrieve created episode")
            return Episode(**self._episode_dict(row[0]))

    async def get_episodes_for_user(self, user_id: int, limit: int = 50) -> list[Episode]:
        async with self._connect() as db:
            rows = await db.execute_fetchall(
                "SELECT id, user_id, session_id, role, content, frame_ids, timestamp "
                "FROM episodes WHERE user_id = ? ORDER BY id DESC LIMIT ?",
                (user_id, limit),
            )
            return [Episode(**self._episode_dict(row)) for row in rows]

    async def get_sessions_for_user(self, user_id: int) -> list[dict]:
        """Get all sessions for a user with episode counts and last message.

        Excludes soft-deleted sessions.
        """
        async with self._connect() as db:
            rows = await db.execute_fetchall(
                """
                SELECT s.id, s.user_id, s.title, s.created_at, s.updated_at,
                       COUNT(e.id) as episode_count,
                       MAX(e.timestamp) as last_activity,
                       -- The opening user message, not the alphabetically-last
                       -- one. MAX() over TEXT is the lexicographic maximum, so a
                       -- session whose turns are "aaa first" then "zzz last" was
                       -- labelled "zzz last" (verified). MIN(id) picks the first
                       -- turn; the correlated subquery keeps it to user turns.
                       (SELECT e2.content FROM episodes e2
                        WHERE e2.session_id = s.id AND e2.user_id = s.user_id
                          AND e2.role = 'user' AND e2.content != ''
                        ORDER BY e2.id ASC LIMIT 1) as first_user_message
                FROM sessions s
                LEFT JOIN episodes e ON e.session_id = s.id AND e.user_id = s.user_id
                WHERE s.user_id = ? AND s.deleted_at IS NULL
                GROUP BY s.id, s.user_id, s.title, s.created_at, s.updated_at
                ORDER BY last_activity DESC
                """,
                (user_id,),
            )
            sessions = []
            for row in rows:
                (
                    sid,
                    uid,
                    title,
                    created_at,
                    updated_at,
                    episode_count,
                    last_activity,
                    first_user_msg,
                ) = row
                # Use title if available, otherwise first user message, otherwise generic
                if title:
                    label = title
                elif first_user_msg:
                    label = first_user_msg[:80]
                else:
                    label = f"Conversation {episode_count}"
                sessions.append({
                    "id": sid,
                    "episode_count": episode_count or 0,
                    "last_activity": last_activity,
                    "created_at": created_at,
                    "last_message": label,
                })
            return sessions

    async def create_session(self, session_id: str, user_id: int, title: str = None) -> None:
        """Create a new session record."""
        async with self._connect() as db:
            sql = (
                "INSERT INTO sessions (id, user_id, title, created_at, updated_at) "
                "VALUES (?, ?, ?, datetime('now'), datetime('now'))"
            )
            await db.execute(sql, (session_id, user_id, title))
            await db.commit()

    async def update_session_title(self, session_id: str, user_id: int, title: str) -> None:
        """Update session title."""
        async with self._connect() as db:
            sql = (
                "UPDATE sessions SET title = ?, updated_at = datetime('now') "
                "WHERE id = ? AND user_id = ?"
            )
            await db.execute(sql, (title, session_id, user_id))
            await db.commit()

    async def delete_session(self, session_id: str, user_id: int) -> bool:
        """Soft-delete a session by setting deleted_at timestamp."""
        async with self._connect() as db:
            cursor = await db.execute(
                """
                UPDATE sessions
                SET deleted_at = datetime('now'), updated_at = datetime('now')
                WHERE id = ? AND user_id = ? AND deleted_at IS NULL
                """,
                (session_id, user_id)
            )
            await db.commit()
            return cursor.rowcount > 0

    async def restore_session(self, session_id: str, user_id: int) -> bool:
        """Restore a soft-deleted session by clearing deleted_at timestamp."""
        async with self._connect() as db:
            cursor = await db.execute(
                """
                UPDATE sessions
                SET deleted_at = NULL, updated_at = datetime('now')
                WHERE id = ? AND user_id = ? AND deleted_at IS NOT NULL
                """,
                (session_id, user_id)
            )
            await db.commit()
            return cursor.rowcount > 0

    async def get_deleted_sessions_for_user(self, user_id: int) -> list[dict]:
        """Get all soft-deleted sessions for a user (trash can view)."""
        async with self._connect() as db:
            rows = await db.execute_fetchall(
                """
                SELECT s.id, s.user_id, s.title, s.created_at, s.updated_at, s.deleted_at,
                       COUNT(e.id) as episode_count,
                       MAX(e.timestamp) as last_activity,
                       MAX(CASE WHEN e.role = 'user' THEN e.content END) as first_user_message
                FROM sessions s
                LEFT JOIN episodes e ON e.session_id = s.id AND e.user_id = s.user_id
                WHERE s.user_id = ? AND s.deleted_at IS NOT NULL
                GROUP BY s.id, s.user_id, s.title, s.created_at, s.updated_at, s.deleted_at
                ORDER BY s.deleted_at DESC
                """,
                (user_id,),
            )
            sessions = []
            for row in rows:
                (
                    sid,
                    uid,
                    title,
                    created_at,
                    updated_at,
                    deleted_at,
                    episode_count,
                    last_activity,
                    first_user_message,
                ) = row
                sessions.append({
                    "id": sid,
                    "user_id": uid,
                    "title": title,
                    "created_at": created_at,
                    "updated_at": updated_at,
                    "deleted_at": deleted_at,
                    "episode_count": episode_count,
                    "last_activity": last_activity,
                    "first_user_message": first_user_message[:200] if first_user_message else None,
                })
            return sessions

    async def get_episodes_for_session(
        self, session_id: str, user_id: int | None = None, limit: int | None = None
    ) -> list[Episode]:
        """The most recent `limit` turns in a session, oldest first.

        Owner-scoped when `user_id` is given and bounded when `limit` is. Both
        matter: this read had neither, so it returned every turn of any session
        whose id the caller knew, and it grew without bound as a conversation got
        longer. Every other read behind retrieval is owner-scoped; this one was
        the exception, and the hot callers filtered and truncated in Python after
        paying for the full read.

        `limit` selects the *most recent* N (then re-sorts oldest-first), because
        that is what every request-path caller wants: the chat UI restores the
        tail of a conversation, and retrieval wants the last few turns. Pass no
        limit to read a whole session (summarisation, export). A non-positive
        limit reads the whole session, since `episodes[-0:]` is the entire list
        and callers relied on that by accident.
        """
        if limit is not None and limit <= 0:
            limit = None

        sql = (
            "SELECT id, user_id, session_id, role, content, frame_ids, timestamp, "
            "reasoning_trace, search_info FROM episodes WHERE session_id = ?"
        )
        params: list = [session_id]
        if user_id is not None:
            sql += " AND user_id = ?"
            params.append(user_id)

        # DESC takes the newest N when a limit is set; the result is reversed
        # below so callers always receive chronological order.
        sql += " ORDER BY id DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)

        async with self._connect() as db:
            rows = await db.execute_fetchall(sql, tuple(params))
            return [Episode(**self._episode_dict(row)) for row in reversed(rows)]

    async def update_episode_frame_ids(self, episode_id: int, frame_ids: list[int]) -> None:
        """Update the frame_ids for an episode after extraction completes."""
        async with self._connect() as db:
            await db.execute(
                "UPDATE episodes SET frame_ids = ? WHERE id = ?",
                (json.dumps(frame_ids), episode_id),
            )
            await db.commit()

    async def get_episodes_for_frames(
        self, frame_ids: list[int], limit: int = 20
    ) -> list[Episode]:
        """Episodes that touched any of the given frames, newest first.

        frame_ids is a JSON array column; json_each expands it so an episode
        linking several frames matches each one.
        """
        if not frame_ids:
            return []
        placeholders = ",".join("?" * len(frame_ids))
        async with self._connect() as db:
            rows = await db.execute_fetchall(
                "SELECT id, user_id, session_id, role, content, frame_ids, timestamp "
                "FROM episodes WHERE EXISTS ("
                f"SELECT 1 FROM json_each(episodes.frame_ids) je "
                f"WHERE je.value IN ({placeholders})) "
                "ORDER BY id DESC LIMIT ?",
                (*frame_ids, limit),
            )
            return [Episode(**self._episode_dict(row)) for row in rows]

    async def search_frames_keyword(self, term: str, limit: int = 10) -> list[Frame]:
        """Case-insensitive substring match on frame names and slot keys/values.

        Keyword complement to vector search: catches exact names and acronyms
        that embeddings blur, and serves as the fallback when the embedding
        model is unreachable. Excludes soft-deleted (priority 0 / GC'd) frames.
        """
        return [
            frame
            for frame, _strength in await self.search_frames_lexical(term, limit)
        ]

    async def search_frames_lexical(
        self, term: str, limit: int = 10
    ) -> list[tuple[Frame, float]]:
        """Tokenized lexical match with per-frame match strength.

        Unlike naive whole-query LIKE ('%mountain wolf%' never matches
        'The Mountain & The Wolf'), this splits the query into tokens and
        scores each frame by how many tokens it matches somewhere in its
        name or slots. Returns (frame, coverage) with coverage in 0..1 =
        matched_tokens / query_tokens, best-first. Excludes soft-deleted
        (priority 0 / GC'd) frames.
        """
        tokens = [t for t in re.split(r"\W+", term.lower()) if len(t) >= 2]
        if not tokens:
            return []
        like_clauses = " OR ".join(
            "(f.name LIKE ? COLLATE NOCASE OR s.key LIKE ? COLLATE NOCASE "
            "OR s.value LIKE ? COLLATE NOCASE)"
            for _ in tokens
        )
        params: list = []
        for t in tokens:
            like = f"%{t}%"
            params.extend([like, like, like])
        async with self._connect() as db:
            rows = await db.execute_fetchall(
                "SELECT DISTINCT f.id, f.name, f.type, f.confidence, f.essential, "
                "f.priority, f.owner_user_id, f.source_type, f.source_url, "
                "f.source_reliability, f.embedding_model, f.created_at, f.updated_at, "
                "s.key, s.value "
                "FROM frames f LEFT JOIN slots s ON s.frame_id = f.id "
                f"WHERE f.priority > 0 AND f.deleted_at IS NULL AND ({like_clauses})",
                params,
            )
        # Score coverage per frame across its row expansion (one row per slot).
        by_frame: dict[int, dict] = {}
        for row in rows:
            fid = row[0]
            entry = by_frame.setdefault(
                fid, {"row": row, "matched": set()}
            )
            haystack = " ".join(
                str(x).lower() for x in row[13:15] if x is not None
            )
            hay_name = (row[1] or "").lower()
            for i, t in enumerate(tokens):
                if t in hay_name or t in haystack:
                    entry["matched"].add(i)
        scored = [
            (Frame(**self._frame_dict(e["row"][:13])), len(e["matched"]) / len(tokens))
            for e in by_frame.values()
        ]
        scored.sort(key=lambda pair: (-pair[1], -pair[0].confidence))
        return scored[:limit]

    # Conflicts
    async def get_conflicts(self, status: str | None = None) -> list[Conflict]:
        async with self._connect() as db:
            if status:
                rows = await db.execute_fetchall(
                    "SELECT id, frame_id, slot_key, existing_value, new_value, "
                    "resolved_value, status, created_at, resolved_at, "
                    "existing_source_reliability, new_source_reliability, "
                    "existing_confidence, new_confidence, "
                    "existing_priority, new_priority "
                    "FROM conflicts WHERE status = ? ORDER BY id",
                    (status,),
                )
            else:
                rows = await db.execute_fetchall(
                    "SELECT id, frame_id, slot_key, existing_value, new_value, "
                    "resolved_value, status, created_at, resolved_at, "
                    "existing_source_reliability, new_source_reliability, "
                    "existing_confidence, new_confidence, "
                    "existing_priority, new_priority "
                    "FROM conflicts ORDER BY id"
                )
            return [Conflict(**self._conflict_dict(row)) for row in rows]

    async def get_conflicts_for_frame(self, frame_id: int) -> list[Conflict]:
        async with self._connect() as db:
            rows = await db.execute_fetchall(
                "SELECT id, frame_id, slot_key, existing_value, new_value, "
                "resolved_value, status, created_at, resolved_at, "
                "existing_source_reliability, new_source_reliability, "
                "existing_confidence, new_confidence, "
                "existing_priority, new_priority "
                "FROM conflicts WHERE frame_id = ? ORDER BY id",
                (frame_id,),
            )
            return [Conflict(**self._conflict_dict(row)) for row in rows]

    async def manual_override_conflict(self, conflict_id: int, value: str) -> Slot:
        async with self._connect() as db:
            row = await db.execute_fetchall(
                "SELECT frame_id, slot_key, existing_value, new_value "
                "FROM conflicts WHERE id = ?",
                (conflict_id,),
            )
            if not row:
                raise ValueError(f"Conflict {conflict_id} not found")
            frame_id, slot_key, existing_value, new_value = row[0]

            existing_slot = await db.execute_fetchall(
                "SELECT id, value FROM slots WHERE frame_id = ? AND key = ?",
                (frame_id, slot_key),
            )
            if not existing_slot:
                raise ValueError(f"Slot not found for conflict {conflict_id}")
            slot_id, old_value = existing_slot[0]

            await db.execute(
                "UPDATE slots SET value = ?, confidence = ?, "
                "updated_at = datetime('now'), last_strengthened_at = datetime('now') WHERE id = ?",
                (value, initial_confidence(), slot_id),
            )
            await db.execute(
                """
                INSERT INTO slot_history (
                    slot_id, frame_id, slot_key, old_value, new_value, reason, source_episode_id
                )
                VALUES (?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    slot_id, frame_id, slot_key, old_value, value,
                    OperationType.MANUAL_OVERRIDE.value,
                ),
            )
            await db.execute(
                """
                UPDATE conflicts
                SET resolved_value = ?, status = 'manual_override', resolved_at = datetime('now')
                WHERE id = ?
                """,
                (value, conflict_id),
            )
            await db.commit()
            return await self._get_slot_row(db, slot_id)

    # Feedback
    async def create_feedback(
        self,
        episode_id: str | None,
        message_id: str,
        kind: str,
        comment: str | None = None,
    ) -> Feedback:
        async with self._connect() as db:
            cursor = await db.execute(
                "INSERT INTO feedback (episode_id, message_id, kind, comment) VALUES (?, ?, ?, ?)",
                (episode_id, message_id, kind, comment),
            )
            await db.commit()
            row = await db.execute_fetchall(
                "SELECT id, episode_id, message_id, kind, comment, created_at "
                "FROM feedback WHERE id = ?",
                (cursor.lastrowid,),
            )
            if not row:
                raise ValueError("Failed to retrieve created feedback")
            id_, ep_id, msg_id, k, c, created_at = row[0]
            return Feedback(
                id=id_,
                episode_id=ep_id,
                message_id=msg_id,
                kind=k,
                comment=c,
                created_at=created_at,
            )

    async def apply_positive_feedback(self, episode_id: str | None) -> int:
        """Boost confidence of frames/slots touched in an episode.

        Returns number of slots updated.
        """
        if not episode_id:
            return 0
        # NOTE: callers pass a *session id* here (legacy param name). The
        # latest episode of a session is the assistant turn, which carries no
        # frame_ids — target the most recent turn that actually touched memory.
        async with self._connect() as db:
            episode_rows = await db.execute_fetchall(
                """
                SELECT frame_ids FROM episodes
                WHERE session_id = ?
                  AND json_array_length(COALESCE(frame_ids, '[]')) > 0
                ORDER BY id DESC LIMIT 1
                """,
                (episode_id,),
            )
            if not episode_rows:
                return 0
            frame_ids = json.loads(episode_rows[0][0] or "[]")
            if not frame_ids:
                return 0

            updated = 0
            for frame_id in frame_ids:
                slots = await db.execute_fetchall(
                    "SELECT id, confidence FROM slots WHERE frame_id = ?",
                    (frame_id,),
                )
                for slot_id, conf in slots:
                    new_conf = bump_confidence(conf)
                    await db.execute(
                        "UPDATE slots SET confidence = ?, updated_at = datetime('now'), "
                        "last_strengthened_at = datetime('now') WHERE id = ?",
                        (new_conf, slot_id),
                    )
                    updated += 1

                frame_rows = await db.execute_fetchall(
                    "SELECT id, confidence FROM frames WHERE id = ?",
                    (frame_id,),
                )
                for frame_id_row, conf in frame_rows:
                    new_conf = bump_confidence(conf)
                    await db.execute(
                        "UPDATE frames SET confidence = ?, "
                        "updated_at = datetime('now') WHERE id = ?",
                        (new_conf, frame_id_row),
                    )
            await db.commit()
            return updated

    async def apply_negative_feedback(self, episode_id: str | None) -> int:
        """Lower confidence of frames/slots touched in an episode.

        Returns number of slots updated.
        """
        if not episode_id:
            return 0
        # NOTE: callers pass a *session id* here (legacy param name). The
        # latest episode of a session is the assistant turn, which carries no
        # frame_ids — target the most recent turn that actually touched memory.
        async with self._connect() as db:
            episode_rows = await db.execute_fetchall(
                """
                SELECT frame_ids FROM episodes
                WHERE session_id = ?
                  AND json_array_length(COALESCE(frame_ids, '[]')) > 0
                ORDER BY id DESC LIMIT 1
                """,
                (episode_id,),
            )
            if not episode_rows:
                return 0
            frame_ids = json.loads(episode_rows[0][0] or "[]")
            if not frame_ids:
                return 0

            updated = 0
            for frame_id in frame_ids:
                slots = await db.execute_fetchall(
                    "SELECT id, confidence FROM slots WHERE frame_id = ?",
                    (frame_id,),
                )
                for slot_id, conf in slots:
                    new_conf = lower_confidence(conf)
                    await db.execute(
                        "UPDATE slots SET confidence = ?, "
                        "updated_at = datetime('now') WHERE id = ?",
                        (new_conf, slot_id),
                    )
                    updated += 1

                frame_rows = await db.execute_fetchall(
                    "SELECT id, confidence FROM frames WHERE id = ?",
                    (frame_id,),
                )
                for frame_id_row, conf in frame_rows:
                    new_conf = lower_confidence(conf)
                    await db.execute(
                        "UPDATE frames SET confidence = ?, "
                        "updated_at = datetime('now') WHERE id = ?",
                        (new_conf, frame_id_row),
                    )
            await db.commit()
            return updated

    # Helpers
    async def _get_frame_row(self, db: aiosqlite.Connection, frame_id: int) -> Frame:
        row = await db.execute_fetchall(
            "SELECT id, name, type, confidence, essential, priority, "
            "owner_user_id, source_type, source_url, source_reliability, "
            "embedding_model, created_at, updated_at FROM frames WHERE id = ?",
            (frame_id,),
        )
        if not row:
            raise ValueError(f"Frame {frame_id} not found")
        return Frame(**self._frame_dict(row[0]))

    async def _get_slot_row(self, db: aiosqlite.Connection, slot_id: int) -> Slot:
        row = await db.execute_fetchall(
            "SELECT id, frame_id, key, value, confidence, essential, priority, "
            "source_type, source_url, source_reliability, source_episode_id, "
            "updated_at, last_strengthened_at FROM slots WHERE id = ?",
            (slot_id,),
        )
        if not row:
            raise ValueError(f"Slot {slot_id} not found")
        return Slot(**self._slot_dict(row[0]))

    async def _get_association_row(
        self, db: aiosqlite.Connection, association_id: int
    ) -> Association:
        row = await db.execute_fetchall(
            "SELECT id, from_frame_id, to_frame_id, relation_type, confidence, "
            "essential, priority, source_type, source_url, source_reliability, "
            "embedding_model, created_at FROM associations WHERE id = ?",
            (association_id,),
        )
        if not row:
            raise ValueError(f"Association {association_id} not found")
        return Association(**self._association_dict(row[0]))

    @staticmethod
    def _frame_dict(row: tuple) -> dict:
        return {
            "id": row[0],
            "name": row[1],
            "type": row[2],
            "confidence": row[3],
            "essential": row[4],
            "priority": row[5],
            "owner_user_id": row[6],
            "source_type": row[7],
            "source_url": row[8],
            "source_reliability": row[9],
            "embedding_model": row[10] if len(row) > 10 else None,
            "created_at": row[11] if len(row) > 11 else None,
            "updated_at": row[12] if len(row) > 12 else None,
            "deleted_at": row[13] if len(row) > 13 else None,
        }

    @staticmethod
    def _slot_dict(row: tuple) -> dict:
        return {
            "id": row[0],
            "frame_id": row[1],
            "key": row[2],
            "value": row[3],
            "confidence": row[4],
            "essential": row[5],
            "priority": row[6],
            "source_type": row[7],
            "source_url": row[8],
            "source_reliability": row[9],
            "source_episode_id": row[10],
            "updated_at": row[11],
            "last_strengthened_at": row[12] if len(row) > 12 else None,
        }

    @staticmethod
    def _association_dict(row: tuple) -> dict:
        return {
            "id": row[0],
            "from_frame_id": row[1],
            "to_frame_id": row[2],
            "relation_type": row[3],
            "confidence": row[4],
            "essential": row[5],
            "priority": row[6],
            "source_type": row[7],
            "source_url": row[8],
            "source_reliability": row[9],
            "embedding_model": row[10] if len(row) > 10 else None,
            "created_at": row[11] if len(row) > 11 else None,
        }

    @staticmethod
    def _episode_dict(row: tuple) -> dict:
        return {
            "id": row[0],
            "user_id": row[1],
            "session_id": row[2],
            "role": row[3],
            "content": row[4],
            "frame_ids": json.loads(row[5]),
            "timestamp": row[6],
            "reasoning_trace": row[7] if len(row) > 7 else None,
            "search_info": row[8] if len(row) > 8 else None,
        }

    async def _get_conflict_row(self, db: aiosqlite.Connection, conflict_id: int) -> Conflict:
        row = await db.execute_fetchall(
            "SELECT id, frame_id, slot_key, existing_value, new_value, "
            "resolved_value, status, created_at, resolved_at, "
            "existing_source_reliability, new_source_reliability, "
            "existing_confidence, new_confidence, "
            "existing_priority, new_priority "
            "FROM conflicts WHERE id = ?",
            (conflict_id,),
        )
        if not row:
            raise ValueError(f"Conflict {conflict_id} not found")
        return Conflict(**self._conflict_dict(row[0]))

    @staticmethod
    def _conflict_dict(row: tuple) -> dict:
        return {
            "id": row[0],
            "frame_id": row[1],
            "slot_key": row[2],
            "existing_value": row[3],
            "new_value": row[4],
            "resolved_value": row[5],
            "status": row[6],
            "created_at": row[7],
            "resolved_at": row[8],
            # Decision inputs, so a past belief change stays auditable.
            "existing_source_reliability": row[9],
            "new_source_reliability": row[10],
            "existing_confidence": row[11],
            "new_confidence": row[12],
            "existing_priority": row[13],
            "new_priority": row[14],
        }

    async def upsert_scheduled_task(
        self,
        name: str,
        description: str,
        schedule_cron: str,
        prompt: str,
        enabled: bool = True,
        owner_user_id: int | None = None,
        next_run: str | None = None,
    ) -> int:
        """Create or update a scheduled_task frame.

        Writes to both the legacy frame columns (for backward compat during migration)
        and to slots (the authoritative store going forward).

        `schedule_cron` holds the frequency tag "daily" or "once". User tasks all
        fire at the shared daily tick; `next_run` defaults to that.

        Returns the frame id.
        """
        from ..scheduler.schedule import next_daily_run

        if schedule_cron not in ("daily", "once"):
            schedule_cron = "daily"
        if next_run is None:
            if enabled:
                next_run = next_daily_run().astimezone(UTC).isoformat()

        async with self._connect() as db:
            existing = await db.execute_fetchall(
                "SELECT id FROM frames WHERE name = ? AND type = 'scheduled_task' "
                "AND deleted_at IS NULL",
                (name,),
            )
            if existing:
                frame_id = existing[0][0]
                await db.execute(
                    "UPDATE frames SET "
                    "description=?, schedule_cron=?, prompt=?, enabled=?, "
                    "next_run=?, updated_at=datetime('now') "
                    "WHERE id = ?",
                    (description, schedule_cron, prompt, 1 if enabled else 0,
                     next_run, frame_id),
                )
                # Write slots (authoritative store)
                await db.execute(
                    "INSERT OR REPLACE INTO slots "
                    "(frame_id, key, value, updated_at) "
                    "VALUES (?, 'description', ?, datetime('now'))",
                    (frame_id, description),
                )
                await db.execute(
                    "INSERT OR REPLACE INTO slots "
                    "(frame_id, key, value, updated_at) "
                    "VALUES (?, 'frequency', ?, datetime('now'))",
                    (frame_id, schedule_cron),
                )
                await db.execute(
                    "INSERT OR REPLACE INTO slots "
                    "(frame_id, key, value, updated_at) "
                    "VALUES (?, 'prompt', ?, datetime('now'))",
                    (frame_id, prompt),
                )
                await db.execute(
                    "INSERT OR REPLACE INTO slots "
                    "(frame_id, key, value, updated_at) "
                    "VALUES (?, 'enabled', ?, datetime('now'))",
                    (frame_id, "true" if enabled else "false"),
                )
                if next_run:
                    await db.execute(
                        "INSERT OR REPLACE INTO slots "
                        "(frame_id, key, value, updated_at) "
                        "VALUES (?, 'next_run', ?, datetime('now'))",
                        (frame_id, next_run),
                    )
                await db.commit()
                return frame_id
            else:
                cursor = await db.execute(
                    "INSERT INTO frames "
                    "(name, type, confidence, essential, priority, owner_user_id, "
                    "source_type, description, schedule_cron, prompt, enabled, next_run) "
                    "VALUES (?, 'scheduled_task', 1.0, 1, 0.8, ?, 'user', ?, ?, ?, ?, ?)",
                    (name, owner_user_id, description, schedule_cron, prompt,
                     1 if enabled else 0, next_run),
                )
                frame_id = cursor.lastrowid
                # Write slots (authoritative store)
                await db.execute(
                    "INSERT OR IGNORE INTO slots "
                    "(frame_id, key, value, updated_at) "
                    "VALUES (?, 'description', ?, datetime('now'))",
                    (frame_id, description),
                )
                await db.execute(
                    "INSERT OR IGNORE INTO slots "
                    "(frame_id, key, value, updated_at) "
                    "VALUES (?, 'frequency', ?, datetime('now'))",
                    (frame_id, schedule_cron),
                )
                await db.execute(
                    "INSERT OR IGNORE INTO slots "
                    "(frame_id, key, value, updated_at) "
                    "VALUES (?, 'prompt', ?, datetime('now'))",
                    (frame_id, prompt),
                )
                await db.execute(
                    "INSERT OR IGNORE INTO slots "
                    "(frame_id, key, value, updated_at) "
                    "VALUES (?, 'enabled', ?, datetime('now'))",
                    (frame_id, "true" if enabled else "false"),
                )
                if next_run:
                    await db.execute(
                        "INSERT OR IGNORE INTO slots "
                        "(frame_id, key, value, updated_at) "
                        "VALUES (?, 'next_run', ?, datetime('now'))",
                        (frame_id, next_run),
                    )
                await db.commit()
                return frame_id

    async def get_scheduled_tasks(
        self,
        owner_user_id: int | None = None,
        include_system: bool = False,
    ) -> list[dict]:
        """List all scheduled tasks (optionally filtered by user).

        Reads from slots as the authoritative store; falls back to legacy frame
        columns for pre-migration rows. Slots override columns.
        """
        async with self._connect() as db:
            if owner_user_id is not None:
                rows = await db.execute_fetchall(
                    "SELECT id, name, description, schedule_cron, prompt, "
                    "enabled, last_run, next_run, last_result_summary, "
                    "owner_user_id, source_type, created_at, updated_at "
                    "FROM frames "
                    "WHERE type = 'scheduled_task' AND deleted_at IS NULL "
                    "AND owner_user_id = ? "
                    "ORDER BY created_at DESC",
                    (owner_user_id,),
                )
            elif include_system:
                rows = await db.execute_fetchall(
                    "SELECT id, name, description, schedule_cron, prompt, "
                    "enabled, last_run, next_run, last_result_summary, "
                    "owner_user_id, source_type, created_at, updated_at "
                    "FROM frames "
                    "WHERE type = 'scheduled_task' AND deleted_at IS NULL "
                    "ORDER BY created_at DESC",
                )
            else:
                rows = await db.execute_fetchall(
                    "SELECT id, name, description, schedule_cron, prompt, "
                    "enabled, last_run, next_run, last_result_summary, "
                    "owner_user_id, source_type, created_at, updated_at "
                    "FROM frames "
                    "WHERE type = 'scheduled_task' AND deleted_at IS NULL "
                    "AND (source_type != 'system' OR source_type IS NULL) "
                    "ORDER BY created_at DESC",
                )
            col_names = [
                "id", "name", "description", "schedule_cron", "prompt",
                "enabled", "last_run", "next_run", "last_result_summary",
                "owner_user_id", "source_type", "created_at", "updated_at",
            ]
            results = []
            for r in rows:
                frame_id = r[0]
                task = dict(zip(col_names, r, strict=True))

                # Fetch slots and override column values
                slot_rows = await db.execute_fetchall(
                    "SELECT key, value FROM slots WHERE frame_id = ?",
                    (frame_id,),
                )
                for key, value in slot_rows:
                    if key in (
                        "description", "frequency", "prompt", "enabled",
                        "next_run", "last_run", "last_result_summary",
                    ):
                        task[key] = value
                    if key == "frequency":
                        task["schedule_cron"] = value  # slot key != column name
                    if key == "enabled":
                        task["enabled"] = 1 if value == "true" else 0

                results.append(task)
            return results

    async def get_due_scheduled_tasks(self) -> list[dict]:
        """Get tasks that are enabled and whose next_run <= now.

        Reads from slots (authoritative); falls back to legacy frame columns for
        pre-migration rows. Due-ness is decided on parsed datetimes to handle
        mixed UTC offsets safely.
        """
        from datetime import datetime

        async with self._connect() as db:
            # Fetch all potential tasks (enabled in column = 1 or enabled slot = "true")
            rows = await db.execute_fetchall(
                "SELECT id, name, description, schedule_cron, prompt, "
                "enabled, last_run, next_run, last_result_summary, "
                "owner_user_id, source_type, created_at, updated_at "
                "FROM frames "
                "WHERE type = 'scheduled_task' AND deleted_at IS NULL "
                "AND enabled = 1 AND next_run IS NOT NULL",
            )
        col_names = [
            "id", "name", "description", "schedule_cron", "prompt",
            "enabled", "last_run", "next_run", "last_result_summary",
            "owner_user_id", "source_type", "created_at", "updated_at",
        ]
        now = datetime.now(UTC)
        due: list[dict] = []

        async with self._connect() as db:
            for r in rows:
                frame_id = r[0]
                task = dict(zip(col_names, r, strict=True))

                slot_rows = await db.execute_fetchall(
                    "SELECT key, value FROM slots WHERE frame_id = ?",
                    (frame_id,),
                )
                slot_map = dict(slot_rows)

                # Override with slot values
                for key in ("description", "frequency", "prompt",
                             "next_run", "last_run", "last_result_summary"):
                    if key in slot_map:
                        task[key] = slot_map[key]
                frequency = slot_map.get("frequency", task.get("schedule_cron", "daily"))
                if "enabled" in slot_map:
                    task["enabled"] = 1 if slot_map["enabled"] == "true" else 0
                    if slot_map["enabled"] != "true":
                        continue  # not actually enabled
                if frequency == "once" and task["enabled"] == 1:
                    # One-shot tasks are due (they fire at next tick then disable themselves).
                    # They are NOT skipped here — update_scheduled_task_run disables after firing.
                    pass
                if "next_run" in slot_map:
                    nr = _parse_iso_ts(slot_map["next_run"])
                else:
                    nr = _parse_iso_ts(task.get("next_run"))
                if nr is not None and nr <= now:
                    due.append(task)
        return due

    async def get_nearest_scheduled_task_run(self) -> datetime | None:
        """Get the nearest next_run datetime among all enabled tasks (UTC)."""
        async with self._connect() as db:
            rows = await db.execute_fetchall(
                "SELECT next_run FROM frames "
                "WHERE type = 'scheduled_task' AND deleted_at IS NULL "
                "AND enabled = 1 AND next_run IS NOT NULL",
            )
        parsed = [
            ts for row in rows if (ts := _parse_iso_ts(row[0])) is not None
        ]
        return min(parsed) if parsed else None

    async def update_scheduled_task_run(
        self,
        frame_id: int,
        last_run: str,
        last_result_summary: str,
    ) -> None:
        """Record a run: reschedule daily tasks, disable one-shot tasks.

        Writes to both legacy frame columns (backward compat) and slots
        (authoritative store).
        """
        from ..scheduler.schedule import next_daily_run

        async with self._connect() as db:
            # Read frequency from slot (authoritative) or column (legacy fallback)
            slot_rows = await db.execute_fetchall(
                "SELECT value FROM slots WHERE frame_id = ? AND key = 'frequency'",
                (frame_id,),
            )
            if slot_rows:
                frequency = slot_rows[0][0]
            else:
                col_rows = await db.execute_fetchall(
                    "SELECT schedule_cron FROM frames WHERE id = ?",
                    (frame_id,),
                )
                if not col_rows:
                    return
                frequency = col_rows[0][0]

            summary = last_result_summary[:2000]
            if frequency == "once":
                await db.execute(
                    "UPDATE frames SET "
                    "last_run=?, last_result_summary=?, enabled=0, next_run=NULL, "
                    "updated_at=datetime('now') "
                    "WHERE id = ?",
                    (last_run, summary, frame_id),
                )
                await db.execute(
                    "INSERT OR REPLACE INTO slots "
                    "(frame_id, key, value, updated_at) "
                    "VALUES (?, 'last_run', ?, datetime('now'))",
                    (frame_id, last_run),
                )
                await db.execute(
                    "INSERT OR REPLACE INTO slots "
                    "(frame_id, key, value, updated_at) "
                    "VALUES (?, 'last_result_summary', ?, datetime('now'))",
                    (frame_id, summary),
                )
                await db.execute(
                    "INSERT OR REPLACE INTO slots "
                    "(frame_id, key, value, updated_at) "
                    "VALUES (?, 'enabled', 'false', datetime('now'))",
                    (frame_id,),
                )
                await db.execute(
                    "DELETE FROM slots WHERE frame_id = ? AND key = 'next_run'",
                    (frame_id,),
                )
            else:
                next_run = next_daily_run().astimezone(UTC).isoformat()
                await db.execute(
                    "UPDATE frames SET "
                    "last_run=?, last_result_summary=?, next_run=?, "
                    "updated_at=datetime('now') "
                    "WHERE id = ?",
                    (last_run, summary, next_run, frame_id),
                )
                await db.execute(
                    "INSERT OR REPLACE INTO slots "
                    "(frame_id, key, value, updated_at) "
                    "VALUES (?, 'last_run', ?, datetime('now'))",
                    (frame_id, last_run),
                )
                await db.execute(
                    "INSERT OR REPLACE INTO slots "
                    "(frame_id, key, value, updated_at) "
                    "VALUES (?, 'last_result_summary', ?, datetime('now'))",
                    (frame_id, summary),
                )
                await db.execute(
                    "INSERT OR REPLACE INTO slots "
                    "(frame_id, key, value, updated_at) "
                    "VALUES (?, 'next_run', ?, datetime('now'))",
                    (frame_id, next_run),
                )
            await db.commit()

    async def delete_scheduled_task(self, frame_id: int) -> None:
        """Soft-delete a scheduled task frame."""
        async with self._connect() as db:
            await db.execute(
                "UPDATE frames SET deleted_at=datetime('now') WHERE id = ?",
                (frame_id,),
            )
            await db.commit()

    async def upsert_scheduler_heartbeat(self, timestamp: str) -> None:
        """Update the scheduler heartbeat slot on the system frame.

        Upserts instead of ``INSERT OR REPLACE``: REPLACE deletes and reinserts
        the row, churning the frame/slot ids and cascading away any associations
        and slot history pointing at them. A heartbeat must not do that.
        """
        async with self._connect() as db:
            now = datetime.now(UTC).isoformat()
            rows = await db.execute_fetchall(
                "SELECT id FROM frames WHERE name = 'scheduler_heartbeat'"
            )
            if rows:
                frame_id = rows[0][0]
                await db.execute(
                    "UPDATE frames SET updated_at = ? WHERE id = ?", (now, frame_id)
                )
            else:
                cursor = await db.execute(
                    "INSERT INTO frames "
                    "(name, type, confidence, essential, priority, source_type, updated_at) "
                    "VALUES ('scheduler_heartbeat', 'system', 1.0, 0, 0.0, 'system', ?)",
                    (now,),
                )
                frame_id = cursor.lastrowid
            await db.execute(
                "INSERT INTO slots (frame_id, key, value, updated_at) "
                "VALUES (?, 'last_heartbeat', ?, ?) "
                "ON CONFLICT(frame_id, key) DO UPDATE SET "
                "value = excluded.value, updated_at = excluded.updated_at",
                (frame_id, timestamp, now),
            )
            await db.commit()

    async def migrate_scheduled_tasks_to_slots(self) -> int:
        """One-time migration: copy legacy scheduled-task column values into slots.

        Detects rows with legacy data (non-NULL columns) but no corresponding slot,
        then creates the slot entries. Idempotent — subsequent calls are no-ops.
        Returns the number of tasks migrated.
        """
        migrated = 0
        async with self._connect() as db:
            rows = await db.execute_fetchall(
                "SELECT id, name, description, schedule_cron, prompt, enabled, "
                "next_run, last_run, last_result_summary "
                "FROM frames WHERE type = 'scheduled_task' AND deleted_at IS NULL"
            )
            for row in rows:
                frame_id = row[0]
                description, frequency, prompt = row[2], row[3], row[4]
                enabled_val, next_run, last_run, last_summary = row[5], row[6], row[7], row[8]

                slot_rows = await db.execute_fetchall(
                    "SELECT key FROM slots WHERE frame_id = ?", (frame_id,)
                )
                existing_keys = {r[0] for r in slot_rows}

                if "description" not in existing_keys and description:
                    await db.execute(
                        "INSERT OR IGNORE INTO slots (frame_id, key, value, updated_at) "
                        "VALUES (?, 'description', ?, datetime('now'))",
                        (frame_id, description),
                    )
                if "frequency" not in existing_keys and frequency:
                    await db.execute(
                        "INSERT OR IGNORE INTO slots (frame_id, key, value, updated_at) "
                        "VALUES (?, 'frequency', ?, datetime('now'))",
                        (frame_id, frequency),
                    )
                if "prompt" not in existing_keys and prompt:
                    await db.execute(
                        "INSERT OR IGNORE INTO slots (frame_id, key, value, updated_at) "
                        "VALUES (?, 'prompt', ?, datetime('now'))",
                        (frame_id, prompt),
                    )
                if "enabled" not in existing_keys:
                    await db.execute(
                        "INSERT OR IGNORE INTO slots (frame_id, key, value, updated_at) "
                        "VALUES (?, 'enabled', ?, datetime('now'))",
                        (frame_id, "true" if enabled_val else "false"),
                    )
                if "next_run" not in existing_keys and next_run:
                    await db.execute(
                        "INSERT OR IGNORE INTO slots (frame_id, key, value, updated_at) "
                        "VALUES (?, 'next_run', ?, datetime('now'))",
                        (frame_id, next_run),
                    )
                if "last_run" not in existing_keys and last_run:
                    await db.execute(
                        "INSERT OR IGNORE INTO slots (frame_id, key, value, updated_at) "
                        "VALUES (?, 'last_run', ?, datetime('now'))",
                        (frame_id, last_run),
                    )
                if "last_result_summary" not in existing_keys and last_summary:
                    await db.execute(
                        "INSERT OR IGNORE INTO slots (frame_id, key, value, updated_at) "
                        "VALUES (?, 'last_result_summary', ?, datetime('now'))",
                        (frame_id, last_summary),
                    )
                migrated += 1
            await db.commit()
        return migrated

    async def upsert_scheduled_task_slot(
        self, frame_id: int, key: str, value: str
    ) -> None:
        """Write or replace a single slot on a scheduled_task frame."""
        async with self._connect() as db:
            await db.execute(
                "INSERT OR REPLACE INTO slots "
                "(frame_id, key, value, updated_at) "
                "VALUES (?, ?, ?, datetime('now'))",
                (frame_id, key, value),
            )
            await db.commit()

    async def get_last_assistant_episode(
        self, user_id: int, session_id: str
    ) -> Episode | None:
        """Return the most recent assistant episode in the given session."""
        async with self._connect() as db:
            rows = await db.execute_fetchall(
                """SELECT id, user_id, session_id, role, content, frame_ids, timestamp
                   FROM episodes
                   WHERE user_id = ? AND session_id = ? AND role = 'assistant'
                   ORDER BY timestamp DESC
                   LIMIT 1""",
                (user_id, session_id),
            )
            if not rows:
                return None
            row = rows[0]
            frame_ids = json.loads(row[5]) if row[5] else []
            return Episode(
                id=row[0],
                user_id=row[1],
                session_id=row[2],
                role=row[3],
                content=row[4],
                frame_ids=frame_ids,
                timestamp=row[6],
            )

    async def get_or_create_daily_run_frame(
        self, date_str: str, owner_user_id: int = 1
    ) -> int:
        """Get or create a daily_run_YYYY_MM_DD event frame. Returns the frame id."""
        frame_name = f"daily_run_{date_str}"
        async with self._connect() as db:
            existing = await db.execute_fetchall(
                "SELECT id FROM frames WHERE name = ? AND type = 'event' "
                "AND deleted_at IS NULL",
                (frame_name,),
            )
            if existing:
                return existing[0][0]

            cursor = await db.execute(
                "INSERT INTO frames "
                "(name, type, confidence, essential, priority, owner_user_id, source_type) "
                "VALUES (?, 'event', 1.0, 0, 0.8, ?, 'scheduler')",
                (frame_name, owner_user_id),
            )
            frame_id = cursor.lastrowid
            await db.execute(
                "INSERT OR REPLACE INTO slots "
                "(frame_id, key, value, updated_at) "
                "VALUES (?, 'date', ?, datetime('now'))",
                (frame_id, date_str),
            )
            await db.commit()
            return frame_id

    async def update_daily_run_frame(
        self,
        frame_id: int,
        tasks_run: list[str],
        status: str = "completed",
    ) -> None:
        """Append task names to tasks_run and update status on a daily-run frame.

        A day's run frame is touched once per task (the scheduler fires tasks
        sequentially), so ``tasks_run`` must ACCUMULATE — overwriting it with
        the last task's name made "what did my run find?" unanswerable from
        the frame itself.
        """
        async with self._connect() as db:
            existing = await db.execute_fetchall(
                "SELECT value FROM slots WHERE frame_id = ? AND key = 'tasks_run'",
                (frame_id,),
            )
            names: list[str] = []
            if existing and existing[0][0]:
                names = [n for n in str(existing[0][0]).split(",") if n]
            for name in tasks_run:
                if name not in names:
                    names.append(name)
            await db.execute(
                "INSERT OR REPLACE INTO slots "
                "(frame_id, key, value, updated_at) "
                "VALUES (?, 'tasks_run', ?, datetime('now'))",
                (frame_id, ",".join(names)),
            )
            await db.execute(
                "INSERT OR REPLACE INTO slots "
                "(frame_id, key, value, updated_at) "
                "VALUES (?, 'status', ?, datetime('now'))",
                (frame_id, status),
            )
            await db.commit()

    async def associate_frames(
        self,
        from_frame_id: int,
        to_frame_id: int,
        relation_type: str,
        confidence: float = 0.5,
    ) -> None:
        """Create or update an association between two frames."""
        async with self._connect() as db:
            await db.execute(
                """INSERT OR REPLACE INTO associations
                   (from_frame_id, to_frame_id, relation_type, confidence, created_at)
                    VALUES (?, ?, ?, ?, datetime('now'))""",
                (from_frame_id, to_frame_id, relation_type, confidence),
            )
            await db.commit()

    # Alerts (Learning Monitor)
    async def create_alert(
        self,
        user_id: int,
        type: str,
        title: str,
        message: str,
        source_frame_id: int | None = None,
        source_episode_id: int | None = None,
        severity: str = "info",
        about: str | None = None,
    ) -> Alert:
        """Create an alert.

        **An alert is memory of a type.** It is stored as a frame of type `alert`
        rather than a row in a notifications table, for the same reason a scheduled
        task is a frame: the agent's own memory is the source of truth, and the bell
        is a view over it.

        That choice is not cosmetic. Because an alert is memory it participates in
        retrieval, so the agent can raise one *when it is contextually relevant* --
        the user mid-conversation about their submission list can be told about the
        alert already held on a related conflict. A notification table can only be
        looked at.

        The signature is unchanged from the table-backed version, so every existing
        call site keeps working and the migration is not a rewrite of its callers.
        """
        # Type and severity ride in the frame name so a human reading the brain
        # graph can tell an alert from a fact at a glance.
        frame_name = f"alert_{type}_{int(datetime.now(UTC).timestamp() * 1000)}"
        frame = await self.create_frame(
            name=frame_name,
            type=ALERT_FRAME_TYPE,
            owner_user_id=user_id,
            source_type="alert",
            source_reliability=0.9,
        )

        # `message` and `title` are asserted by the agent, so they take the normal
        # belief path; `status` is derived state and does not.
        await self.upsert_slot(
            frame_id=frame.id,
            key="title",
            value=title,
            source_type="alert",
            source_reliability=0.9,
            priority=0.8,
        )
        await self.upsert_slot(
            frame_id=frame.id,
            key="message",
            value=message,
            source_episode_id=source_episode_id,
            source_type="alert",
            source_reliability=0.9,
            priority=0.8,
        )
        await self.set_derived_slot(frame.id, "status", "new", source_type="alert")
        await self.set_derived_slot(frame.id, "severity", severity, source_type="alert")
        await self.set_derived_slot(frame.id, "kind", type, source_type="alert")

        # What the alert concerns, so resolution has a target. Falls back to the
        # frame the caller named, which is what the deterministic backstop watches.
        target = about
        if target is None and source_frame_id is not None:
            target = str(source_frame_id)
        if target is not None:
            await self.set_derived_slot(frame.id, "about", target, source_type="alert")
        if source_episode_id is not None:
            await self.set_derived_slot(
                frame.id, "origin_episode", str(source_episode_id), source_type="alert"
            )

        return Alert(
            id=frame.id,
            user_id=user_id,
            type=type,
            title=title,
            message=message,
            source_frame_id=source_frame_id,
            source_episode_id=source_episode_id,
            severity=severity,
            is_read=False,
            created_at=frame.created_at,
        )

    async def get_alerts(
        self,
        user_id: int,
        unread_only: bool = False,
        limit: int = 50,
        include_resolved: bool = False,
    ) -> list[Alert]:
        """Alerts for a user, newest first.

        `unread_only` is kept for signature compatibility but now means
        "status = new" — there is no read flag in the memory model, because reading
        an alert without resolving it accomplishes nothing. The bell counts open
        questions, not unread notifications.
        """
        statuses = ("new",) if (unread_only or not include_resolved) else ("new", "resolved")
        alerts: list[Alert] = []
        frames = await self.list_frames(ALERT_FRAME_TYPE, owner_user_id=user_id)
        for frame in frames:
            slots = {s.key: s.value for s in await self.get_slots_for_frame(frame.id)}
            if slots.get("status") not in statuses:
                continue
            alerts.append(
                Alert(
                    id=frame.id,
                    user_id=user_id,
                    type=slots.get("kind") or "alert",
                    title=slots.get("title") or "",
                    message=slots.get("message") or "",
                    source_episode_id=(
                        int(slots["origin_episode"])
                        if slots.get("origin_episode", "").isdigit()
                        else None
                    ),
                    severity=slots.get("severity") or "info",
                    is_read=slots.get("status") == "resolved",
                    created_at=frame.created_at,
                )
            )
        alerts.sort(key=lambda a: a.id or 0, reverse=True)
        return alerts[:limit]

    async def get_unread_alert_count(self, user_id: int) -> int:
        """Count of open alerts. Named for the API it serves, not the model: the
        memory model has no read flag, so this is the count of unresolved."""
        return len(await self.get_alerts(user_id, unread_only=True, limit=1000))

    async def mark_alert_read(self, alert_id: int, user_id: int) -> bool:
        """Resolve an alert, scoped to its owner.

        Kept under its old name because the API route uses it, but it now performs
        the real transition: `status = resolved`. There is no "seen but open" state,
        because an alert the user looked at and did not answer is still a thing the
        agent is waiting on.

        The owner check is not optional: the table version scoped this in SQL, and
        dropping that would let one household member resolve another's alert by
        guessing an id. Sessions are owner-scoped everywhere else for the same
        reason -- a session id is not a capability.
        """
        return await self.resolve_alert(alert_id, user_id=user_id)

    async def resolve_alert(self, alert_id: int, user_id: int | None = None) -> bool:
        """Mark an alert resolved. Writes `slot_history` like any belief change.

        `user_id` scopes the operation to the alert's owner when given. The
        deterministic backstop (`resolve_alerts_for_session`) omits it because it
        has already selected alerts by session, and a session is itself owner-scoped
        -- but any user-facing path must pass it.
        """
        frame = await self.get_frame(alert_id)
        if frame is None or frame.type != ALERT_FRAME_TYPE:
            return False
        if user_id is not None and frame.owner_user_id != user_id:
            logger.warning(
                "Refused to resolve alert %d for user %s (owned by %s)",
                alert_id,
                user_id,
                frame.owner_user_id,
            )
            return False
        await self.set_derived_slot(alert_id, "status", "resolved", source_type="alert")
        logger.info("Alert resolved: frame=%d", alert_id)
        return True

    async def attach_alert_to_conversation(
        self,
        alert_id: int,
        user_id: int,
        session_id: str,
    ) -> tuple[str, int | None]:
        """Attach an alert to a conversation, posting it as a message.

        The selector's server side. The default is **an existing conversation**:
        giving every alert its own thread solves one problem (nothing to choose) and
        creates another, because the conversation list then fills with one-off alert
        threads -- the inbox problem in different clothes.

        The alert is always posted as a concise assistant message the user can
        answer, whether or not the conversation already has history. It used to be
        written only into an *empty* session, so choosing any conversation the user
        had used before showed nothing at all -- indistinguishable from the feature
        being broken. The message is the alert's short `title` (the question), not
        `message`, which for a task alert is the entire report. It is posted once
        per conversation: re-opening the same alert adds no second opening. The
        alert is linked to the session either way, so the backstop closes it when
        the user replies.

        Returns `(session_id, episode_id)`.
        """
        frame = await self.get_frame(alert_id)
        if frame is None or frame.type != ALERT_FRAME_TYPE:
            raise ValueError(f"frame {alert_id} is not an alert")
        if frame.owner_user_id != user_id:
            raise ValueError(f"alert {alert_id} is not owned by user {user_id}")

        slots = {s.key: s.value for s in await self.get_slots_for_frame(alert_id)}
        # Post once per conversation, keyed on whether the conversation actually
        # holds a message for this alert -- not on the `session_id` slot. An alert
        # attached before this behaviour existed has the slot set but no message,
        # and keying on the slot refused to post it, which looks exactly like the
        # feature being broken.
        already_posted = await self._session_has_alert_episode(session_id, alert_id)
        episode_id: int | None = None
        if not already_posted:
            about = slots.get("about")
            target_note = (
                f"\n\nWhat this concerns: {about}. "
                "When we settle it, update that and mark this alert resolved."
                if about
                else "\n\nWhen we settle this, mark the alert resolved."
            )
            headline = (slots.get("title") or "").strip() or (
                slots.get("message") or ""
            )[:200].strip()
            opening = f"{headline}{target_note}".strip()
            episode = await self.create_episode(
                user_id=user_id,
                session_id=session_id,
                role="assistant",
                content=opening,
                frame_ids=[alert_id],
            )
            episode_id = episode.id

        await self.set_derived_slot(
            alert_id, "session_id", session_id, source_type="alert"
        )
        logger.info(
            "Alert %d attached to conversation %s (posted=%s)",
            alert_id,
            session_id,
            episode_id is not None,
        )
        return session_id, episode_id

    async def open_alert_conversation(
        self,
        alert_id: int,
        user_id: int,
        session_id: str | None = None,
    ) -> tuple[str, int | None]:
        """Attach an alert to a conversation, creating one only if necessary.

        A thin wrapper over `attach_alert_to_conversation` for callers that have not
        chosen a venue: it reuses the session the alert already owns, or falls back
        to the alert's own `conv_alert_<id>` so the operation is idempotent.
        """
        frame = await self.get_frame(alert_id)
        if frame is None or frame.type != ALERT_FRAME_TYPE:
            raise ValueError(f"frame {alert_id} is not an alert")

        slots = {s.key: s.value for s in await self.get_slots_for_frame(alert_id)}
        target = session_id or slots.get("session_id") or f"conv_alert_{alert_id}"
        await self.create_session_if_missing(target, user_id)
        return await self.attach_alert_to_conversation(alert_id, user_id, target)

    async def _session_has_alert_episode(self, session_id: str, alert_id: int) -> bool:
        """Whether this session already holds a posted message for the alert.

        Keyed on the episode's `frame_ids`, which is what a posted alert message
        carries -- not on the alert's `session_id` slot, which an older attach set
        without writing anything.
        """
        async with self._connect() as db:
            rows = await db.execute_fetchall(
                "SELECT 1 FROM episodes WHERE session_id = ? "
                "AND EXISTS (SELECT 1 FROM json_each(episodes.frame_ids) je "
                "WHERE je.value = ?) LIMIT 1",
                (session_id, alert_id),
            )
        return bool(rows)

    async def create_session_if_missing(self, session_id: str, user_id: int) -> None:
        """Create a session row unless one exists. The attach path may target a
        conversation the user already has, which is not an error."""
        async with self._connect() as db:
            rows = await db.execute_fetchall(
                "SELECT 1 FROM sessions WHERE id = ?", (session_id,)
            )
            if rows:
                return
        await self.create_session(session_id, user_id)

    async def resolve_alerts_for_session(self, session_id: str) -> int:
        """Resolve alerts whose conversation this is.

        The deterministic backstop. Relying only on the model remembering to close
        its own alert is the failure we have already been bitten by, so when a turn
        lands in a session an alert opened, the alert closes — the model decides
        *what* to settle, and the plumbing guarantees the close.

        Returns how many alerts were resolved.
        """
        alerts = await self.get_alerts_by_session(session_id)
        resolved = 0
        for alert in alerts:
            if await self.resolve_alert(alert.id):
                resolved += 1
        if resolved:
            logger.info(
                "Resolved %d alert(s) via conversation %s", resolved, session_id
            )
        return resolved

    async def get_alerts_by_session(self, session_id: str) -> list[Alert]:
        """Open alerts whose conversation is `session_id`."""
        alerts: list[Alert] = []
        frames = await self.list_frames(ALERT_FRAME_TYPE)
        for frame in frames:
            slots = {s.key: s.value for s in await self.get_slots_for_frame(frame.id)}
            if slots.get("status") != "new":
                continue
            if slots.get("session_id") != session_id:
                continue
            alerts.append(
                Alert(
                    id=frame.id,
                    user_id=frame.owner_user_id or 0,
                    type=slots.get("kind") or "alert",
                    title=slots.get("title") or "",
                    message=slots.get("message") or "",
                    severity=slots.get("severity") or "info",
                    is_read=False,
                    created_at=frame.created_at,
                )
            )
        return alerts

    async def mark_all_alerts_read(self, user_id: int) -> int:
        """Resolve every open alert for a user. Returns how many moved."""
        alerts = await self.get_alerts(user_id, unread_only=True, limit=1000)
        for alert in alerts:
            if alert.id is not None:
                await self.resolve_alert(alert.id)
        return len(alerts)


def lexical_blend_similarity(coverage: float) -> float:
    """Rank score for a lexical frame match with token coverage 0..1.

    Blends keyword matches INTO the semantic ranking: full coverage scores
    0.95, which outranks the flat ~0.5-0.56 fuzzy band that vector search
    returns for unrelated frames, while partial coverage degrades gracefully.
    """
    return round(0.55 + 0.4 * coverage, 6)


def merge_match_scores(
    semantic: dict[int, float],
    lexical: list[tuple[int, float]],
) -> dict[int, float]:
    """Combine semantic similarities with lexical (frame_id, coverage) hits.

    A frame found both ways keeps whichever signal is stronger, so a weak
    vector match on an exact topic name is upgraded instead of shielding the
    frame from its own keyword evidence.
    """
    merged = dict(semantic)
    for frame_id, strength in lexical:
        score = lexical_blend_similarity(strength)
        if frame_id not in merged or merged[frame_id] < score:
            merged[frame_id] = score
    return merged
