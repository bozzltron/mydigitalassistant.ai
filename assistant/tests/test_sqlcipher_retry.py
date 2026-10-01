"""Regression tests for transient DB I/O retries on encrypted connection open.

Live incident 2026-10-01: a fresh SQLCipher connection's lazy key derivation
(which reads page 1 for the salt) transiently failed to authenticate under a
concurrent WAL checkpoint. SQLCipher logged "hmac check failed for pgno=1" and
the failure surfaced as ``sqlite3.OperationalError: disk I/O error``, failing
scheduled tasks and a live chat turn. The brain's
``PRAGMA cipher_integrity_check`` still passed, so it was a retryable read race,
not corruption.

``aiosqlite_connect_checked`` forces the key derivation at open time and retries
a transient failure on a fresh connection. These tests pin that behaviour.
"""

import sqlite3

import pytest

from assistant.backend.db import sqlcipher


class _FakeConn:
    """Minimal stand-in for an aiosqlite connection.

    ``failures`` is consumed one entry per first-read attempt; a non-None entry
    is raised, None succeeds.
    """

    def __init__(self, failures):
        self._failures = list(failures)
        self.closed = False
        self.reads = 0

    async def execute_fetchall(self, sql, params=()):
        self.reads += 1
        exc = self._failures.pop(0) if self._failures else None
        if exc is not None:
            raise exc
        return [(0,)]

    async def close(self):
        self.closed = True


@pytest.fixture
def fake_connect(monkeypatch):
    """Patch aiosqlite_connect to hand out scripted connections in order."""
    queue: list[_FakeConn] = []

    async def _connect(database, **kwargs):
        return queue.pop(0)

    monkeypatch.setattr(sqlcipher, "aiosqlite_connect", _connect)
    return queue


class TestIsTransientIoError:
    def test_disk_io_error_is_transient(self):
        assert sqlcipher.is_transient_io_error(
            sqlite3.OperationalError("disk I/O error")
        )

    def test_locked_is_transient(self):
        assert sqlcipher.is_transient_io_error(
            sqlite3.OperationalError("database is locked")
        )

    def test_other_operational_error_is_not(self):
        assert not sqlcipher.is_transient_io_error(
            sqlite3.OperationalError("no such table: frames")
        )

    def test_non_operational_error_is_not(self):
        assert not sqlcipher.is_transient_io_error(ValueError("disk I/O error"))


async def test_transient_error_is_retried_on_a_fresh_connection(fake_connect):
    first = _FakeConn([sqlite3.OperationalError("disk I/O error")])
    second = _FakeConn([None])
    fake_connect.extend([first, second])

    db = await sqlcipher.aiosqlite_connect_checked("x.db", base_delay=0)

    assert db is second
    assert first.closed  # the failed connection is discarded, not reused
    assert second.reads == 1


async def test_transient_error_after_all_attempts_raises(fake_connect):
    fake_connect.extend(
        [_FakeConn([sqlite3.OperationalError("disk I/O error")]) for _ in range(3)]
    )

    with pytest.raises(sqlite3.OperationalError, match="disk I/O error"):
        await sqlcipher.aiosqlite_connect_checked("x.db", attempts=3, base_delay=0)


async def test_non_transient_error_raises_without_retry(fake_connect):
    only = _FakeConn([sqlite3.OperationalError("no such table: frames")])
    fake_connect.append(only)

    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        await sqlcipher.aiosqlite_connect_checked("x.db", base_delay=0)

    assert only.closed


async def test_open_checked_db_closes_on_exit(fake_connect):
    conn = _FakeConn([None])
    fake_connect.append(conn)

    async with sqlcipher.open_checked_db("x.db") as db:
        assert db is conn

    assert conn.closed
