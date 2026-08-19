"""SQLCipher patching — must be imported before sqlite3/aiosqlite.

Patches the stdlib sqlite3 module with sqlcipher3 when DB_KEY is set,
enabling AES-256 encrypted SQLite databases. Safe to call multiple times.
"""

import sys


def patch_sqlite_for_sqlcipher() -> None:
    """Patch stdlib sqlite3 to use sqlcipher3 when DB_KEY is set.

    idempotent: calling twice has no additional effect after the first.
    """
    # Already patched (or not needed)?
    if getattr(sys, "_sqlcipher_patched", False):
        return

    from assistant.backend.config import settings

    if not settings.db_key:
        sys._sqlcipher_patched = False
        return

    try:
        import sqlcipher3
    except ImportError:
        import logging

        logging.getLogger(__name__).warning(
            "DB_KEY is set but sqlcipher3 is not installed. "
            "Database will not be encrypted. "
            "Install with: pip install sqlcipher3"
        )
        sys._sqlcipher_patched = False
        return

    import sqlite3

    sqlcipher3.dbapi2.paramstyle = sqlite3.paramstyle
    sqlcipher3.dbapi2.sqlite_version = sqlite3.sqlite_version

    sqlite3.dbapi2 = sqlcipher3.dbapi2
    sys.modules["sqlite3"] = sqlcipher3
    sys.modules["sqlite3.dbapi2"] = sqlcipher3.dbapi2

    sys._sqlcipher_patched = True

    import logging

    logging.getLogger(__name__).info("SQLCipher encryption enabled (DB_KEY is set)")


def apply_db_key(conn) -> None:
    """Execute PRAGMA key on an existing connection.

    Call this immediately after aiosqlite.connect() returns when DB_KEY is set.
    Works with both sqlcipher3 patched sqlite3 and vanilla sqlite3 (no-op).
    """
    from assistant.backend.config import settings

    if not settings.db_key:
        return
    try:
        key = settings.db_key
        # aiosqlite.execute() is a coroutine; sqlite3.execute() is sync
        if hasattr(conn, "execute") and hasattr(conn.execute, "__call__"):
            import asyncio
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(conn.execute(f"PRAGMA key = '{key}'"))
            else:
                loop.run_until_complete(conn.execute(f"PRAGMA key = '{key}'"))
        else:
            conn.execute(f"PRAGMA key = '{key}'")
    except Exception:
        pass
