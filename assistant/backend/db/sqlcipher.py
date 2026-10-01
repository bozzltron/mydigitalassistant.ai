"""SQLCipher connection helpers.

Provides explicit, per-connection encryption factories. Encryption is no longer
applied by globally patching ``sys.modules["sqlite3"]``; callers choose plain or
encrypted connections as needed.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

_logger = logging.getLogger(__name__)

# Capture the vanilla sqlite3 connect function before anything else can patch it.
_sqlite3_connect = sqlite3.connect


def _sqlcipher_module() -> Any:
    """Return the sqlcipher3 module, raising if unavailable."""
    try:
        import sqlcipher3
    except ImportError as exc:
        raise RuntimeError(
            "DB_KEY is set but sqlcipher3 is not installed. "
            "Install with: pip install sqlcipher3"
        ) from exc
    return sqlcipher3


# Methods that may raise sqlcipher3 exceptions and need translating to the
# stdlib sqlite3 exception classes so callers can catch them uniformly.
_SQLCIPHER_WRAPPED_METHODS = frozenset(
    {
        "backup",
        "close",
        "commit",
        "create_aggregate",
        "create_collation",
        "create_function",
        "create_window_function",
        "cursor",
        "enable_load_extension",
        "execute",
        "executemany",
        "executescript",
        "interrupt",
        "load_extension",
        "rollback",
        "set_authorizer",
        "set_busy_handler",
        "set_busy_timeout",
        "set_key",
        "set_progress_handler",
        "set_trace_callback",
    }
)


def _translate_sqlcipher_exception(exc: BaseException) -> BaseException:
    """Return an equivalent stdlib sqlite3 exception for a sqlcipher3 one."""
    import sqlcipher3

    # Check most-specific classes first so IntegrityError maps to
    # sqlite3.IntegrityError, not sqlite3.DatabaseError or sqlite3.Error.
    for name in (
        "IntegrityError",
        "OperationalError",
        "InternalError",
        "ProgrammingError",
        "NotSupportedError",
        "DataError",
        "DatabaseError",
        "InterfaceError",
        "Error",
        "Warning",
    ):
        if isinstance(exc, getattr(sqlcipher3.dbapi2, name, ())):
            sqlite_exc_cls = getattr(sqlite3, name, None)
            if sqlite_exc_cls is not None:
                return sqlite_exc_cls(*exc.args)
    return exc


class _SqlcipherConnectionWrapper:
    """Wrap a sqlcipher3 connection so it raises stdlib sqlite3 exceptions."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._conn, name)
        if name in _SQLCIPHER_WRAPPED_METHODS and callable(attr):
            if name == "backup":
                return self._backup

            def wrapped(*args: Any, **kwargs: Any) -> Any:
                try:
                    return attr(*args, **kwargs)
                except Exception as exc:
                    raise _translate_sqlcipher_exception(exc) from None

            return wrapped
        return attr

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "_conn":
            super().__setattr__(name, value)
        elif hasattr(self._conn, name):
            setattr(self._conn, name, value)
        else:
            super().__setattr__(name, value)

    def _backup(self, dst: Any, *args: Any, **kwargs: Any) -> Any:
        """Unwrap the destination so sqlcipher3 sees a native Connection."""
        raw_dst = dst._conn if isinstance(dst, _SqlcipherConnectionWrapper) else dst
        try:
            return self._conn.backup(raw_dst, *args, **kwargs)
        except Exception as exc:
            raise _translate_sqlcipher_exception(exc) from None

    def __enter__(self) -> _SqlcipherConnectionWrapper:
        self._conn.__enter__()
        return self

    def __exit__(self, *args: Any, **kwargs: Any) -> Any:
        return self._conn.__exit__(*args, **kwargs)


def _encryption_requested() -> bool:
    """Return True when the app has configured a database encryption key."""
    from assistant.backend.config import settings

    return bool(settings.db_key)


def connect_plain(database: str | Path, **kwargs: Any) -> Any:
    """Open a plain (unencrypted) SQLite connection."""
    return _sqlite3_connect(str(database), **kwargs)


def connect_encrypted(database: str | Path, **kwargs: Any) -> Any:
    """Open an encrypted SQLCipher connection using the configured DB_KEY."""
    from assistant.backend.config import settings

    key = settings.db_key
    if not key:
        raise ValueError("DB_KEY is not set. Cannot open encrypted database.")

    sqlcipher3 = _sqlcipher_module()
    conn = sqlcipher3.connect(str(database), **kwargs)
    conn.execute(f"PRAGMA key = '{key}'")
    return _SqlcipherConnectionWrapper(conn)


def connect(database: str | Path, *, encrypted: bool | None = None, **kwargs: Any) -> Any:
    """Open a SQLite connection.

    ``encrypted`` defaults to ``True`` when ``DB_KEY`` is set. If ``DB_KEY`` is
    set but sqlcipher3 is not installed, a warning is logged and a plain
    connection is returned so the app can still function.
    """
    if encrypted is None:
        encrypted = _encryption_requested()

    if encrypted:
        try:
            return connect_encrypted(database, **kwargs)
        except RuntimeError:
            _logger.warning(
                "Encryption requested but sqlcipher3 unavailable; falling back to plain SQLite"
            )

    return connect_plain(database, **kwargs)


def aiosqlite_connect_plain(database: str | Path, **kwargs: Any) -> Any:
    """Open an async plain SQLite connection."""
    import aiosqlite

    def connector() -> Any:
        return _sqlite3_connect(str(database), **kwargs)

    return aiosqlite.Connection(connector, kwargs.pop("iter_chunk_size", 64))


def aiosqlite_connect_encrypted(database: str | Path, **kwargs: Any) -> Any:
    """Open an async encrypted SQLCipher connection using the configured DB_KEY."""
    from assistant.backend.config import settings

    key = settings.db_key
    if not key:
        raise ValueError("DB_KEY is not set. Cannot open encrypted database.")

    import aiosqlite

    sqlcipher3 = _sqlcipher_module()

    def connector() -> Any:
        conn = sqlcipher3.connect(str(database), **kwargs)
        conn.execute(f"PRAGMA key = '{key}'")
        return _SqlcipherConnectionWrapper(conn)

    return aiosqlite.Connection(connector, kwargs.pop("iter_chunk_size", 64))


def aiosqlite_connect(
    database: str | Path,
    *,
    encrypted: bool | None = None,
    **kwargs: Any,
) -> Any:
    """Open an async SQLite connection.

    ``encrypted`` defaults to ``True`` when ``DB_KEY`` is set.
    """
    if encrypted is None:
        encrypted = _encryption_requested()

    if encrypted:
        try:
            return aiosqlite_connect_encrypted(database, **kwargs)
        except RuntimeError:
            _logger.warning(
                "Encryption requested but sqlcipher3 unavailable; falling back to plain SQLite"
            )

    return aiosqlite_connect_plain(database, **kwargs)


# SQLite/SQLCipher errors a *fresh* connection can clear on retry.
#
# The one this exists for: SQLCipher derives its key lazily on a connection's
# first read, taking the database salt from page 1. Under concurrent WAL
# checkpoints that read can transiently fail to authenticate — SQLCipher logs
# "hmac check failed for pgno=1" and the failure surfaces as "disk I/O error" —
# then succeed on the next connection. It is a retryable read race, not
# corruption (``PRAGMA cipher_integrity_check`` still passes), so a new
# connection is the right response instead of failing the user's turn.
_TRANSIENT_IO_MARKERS = (
    "disk i/o error",
    "database is locked",
    "database table is locked",
    "unable to open database",
)


def is_transient_io_error(exc: BaseException) -> bool:
    """True for SQLite errors that a fresh attempt can plausibly clear."""
    if not isinstance(exc, sqlite3.OperationalError):
        return False
    message = str(exc).lower()
    return any(marker in message for marker in _TRANSIENT_IO_MARKERS)


async def aiosqlite_connect_checked(
    database: str | Path,
    *,
    encrypted: bool | None = None,
    attempts: int = 3,
    base_delay: float = 0.05,
    **kwargs: Any,
) -> Any:
    """Open an async connection and force SQLCipher's lazy key derivation now.

    The first read on an encrypted connection derives the key from page 1, so a
    transient page-1 read failure would otherwise surface in the middle of a
    caller's query. Validating here turns it into a retry on a fresh connection,
    with a warning logged so the event is visible rather than silently swallowed.
    Non-transient errors (and a persistent I/O failure) still raise.
    """
    last_exc: BaseException | None = None
    for attempt in range(attempts):
        db = await aiosqlite_connect(database, encrypted=encrypted, **kwargs)
        try:
            # Forces the key derivation and the page-1 read.
            await db.execute_fetchall("SELECT count(*) FROM sqlite_master")
            return db
        except sqlite3.OperationalError as exc:
            try:
                await db.close()
            except Exception:
                _logger.debug("Closing a failed DB connection also failed", exc_info=True)
            last_exc = exc
            if attempt >= attempts - 1 or not is_transient_io_error(exc):
                raise
            _logger.warning(
                "Transient DB I/O error opening %s (attempt %d/%d): %s; retrying",
                database,
                attempt + 1,
                attempts,
                exc,
            )
            await asyncio.sleep(base_delay * (2**attempt))

    # Unreachable: the loop either returns or raises.
    assert last_exc is not None
    raise last_exc


@asynccontextmanager
async def open_checked_db(
    database: str | Path,
    *,
    encrypted: bool | None = None,
    **kwargs: Any,
) -> AsyncGenerator[Any, None]:
    """``aiosqlite_connect_checked`` as an async context manager that closes on exit.

    For the many call sites that used ``async with aiosqlite_connect(...)`` and
    want the transient-I/O retry without managing the connection themselves.
    """
    db = await aiosqlite_connect_checked(database, encrypted=encrypted, **kwargs)
    try:
        yield db
    finally:
        await db.close()
