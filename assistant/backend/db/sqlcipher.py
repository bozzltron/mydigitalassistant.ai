"""SQLCipher connection helpers.

Provides explicit, per-connection encryption factories. Encryption is no longer
applied by globally patching ``sys.modules["sqlite3"]``; callers choose plain or
encrypted connections as needed.
"""

from __future__ import annotations

import logging
import sqlite3
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
