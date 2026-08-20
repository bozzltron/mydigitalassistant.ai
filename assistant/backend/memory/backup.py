"""Encrypted backup and restore for the assistant brain.

Produces AES-256-GCM encrypted JSON bundles. The DB_KEY is used as a
HKDF-derived AES-256 key. Backup format:

{
  "version": 1,
  "key_id": "<first 16 chars of SHA-256 of key>",
  "iv": "<base64>",          # 12-byte random IV per backup
  "ciphertext": "<base64>",  # AES-256-GCM ciphertext of UTF-8 JSON
  "tag": "<base64>",         # GCM auth tag (16 bytes)
}

The inner JSON contains all tables exported from SQLite as rows.
"""

import hashlib
import json
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from assistant.backend.config import settings
from assistant.backend.db.sqlcipher import (
    aiosqlite_connect,
    connect,
    connect_plain,
)


def _derive_key(key: str) -> bytes:
    """Derive a 32-byte AES key from DB_KEY using HKDF-SHA256."""
    return hashlib.sha256(key.encode()).digest()


def _key_id(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:16]


@asynccontextmanager
async def _export_tables_encrypted(db_path: str):
    """Export all tables from an encrypted SQLCipher database."""
    db = await aiosqlite_connect(db_path, encrypted=True)
    await db.execute("PRAGMA foreign_keys = OFF")
    tables: dict[str, list[dict]] = {}

    cursor = await db.execute_fetchall(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name",
    )
    table_names = [r[0] for r in cursor]

    for tname in table_names:
        if tname in ("sqlite_sequence", "sqlite_stat1", "sqlite_stat4"):
            continue
        rows = await db.execute_fetchall(f"SELECT * FROM {tname}")
        cols = [desc[1] for desc in await db.execute_fetchall(f"PRAGMA table_info({tname})")]
        tables[tname] = [dict(zip(cols, row, strict=True)) for row in rows]

    await db.close()
    yield tables


def _import_tables_sync(db_path: str, tables: dict[str, list[dict]]) -> None:
    """Import table data into a fresh database (schema must already exist).

    Uses a synchronous connection so it can run outside the aiosqlite worker
    thread and avoid re-entrancy issues during restore/migration.
    """
    conn = connect(db_path)
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.commit()

    for tname, rows in tables.items():
        if not rows:
            continue
        for row in rows:
            placeholders = ", ".join(["?"] * len(row))
            cols = ", ".join(row.keys())
            conn.execute(
                f"INSERT OR IGNORE INTO {tname} ({cols}) VALUES ({placeholders})",
                list(row.values()),
            )
    conn.commit()
    conn.close()


async def create_encrypted_backup(dest_path: str | Path, *, db_path: str | None = None) -> dict:
    """Create an encrypted backup of the database.

    Args:
        dest_path: Output file path for the encrypted bundle.
        db_path: Source database path. Defaults to ``settings.database_path``.

    Returns:
        dict with metadata: version, key_id, db_size_bytes, backup_size_bytes
    """
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    db_path = db_path or settings.database_path
    if not Path(db_path).exists():
        raise FileNotFoundError(f"Database not found at {db_path}")

    if not settings.db_key:
        raise ValueError(
            "DB_KEY is not set. Cannot create encrypted backup. "
            "Set DB_KEY in .env first."
        )

    key = _derive_key(settings.db_key)
    key_id = _key_id(settings.db_key)

    async with _export_tables_encrypted(db_path) as tables:
        plaintext = json.dumps(tables, indent=2, default=str).encode()

        iv = secrets.token_bytes(12)
        aesgcm = AESGCM(key)
        ciphertext = aesgcm.encrypt(iv, plaintext, None)

        bundle = {
            "version": 1,
            "key_id": key_id,
            "iv": iv.hex(),
            "data": ciphertext.hex(),
        }

        dest = Path(dest_path)
        dest.write_bytes(json.dumps(bundle).encode())

        return {
            "version": 1,
            "key_id": key_id,
            "db_size_bytes": Path(db_path).stat().st_size,
            "backup_size_bytes": dest.stat().st_size,
            "backup_path": str(dest),
        }


async def restore_encrypted_backup(src_path: str | Path, *, db_path: str | None = None) -> dict:
    """Restore an encrypted backup, overwriting the current database.

    Args:
        src_path: Path to the encrypted backup bundle.
        db_path: Target database path. Defaults to ``settings.database_path``.

    Returns:
        dict with metadata about the restore.
    """
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if not settings.db_key:
        raise ValueError(
            "DB_KEY is not set. Cannot restore encrypted backup. "
            "Set DB_KEY in .env first."
        )

    src = Path(src_path)
    if not src.exists():
        raise FileNotFoundError(f"Backup not found at {src_path}")

    bundle = json.loads(src.read_bytes().decode())
    version = bundle.get("version")
    if version != 1:
        raise ValueError(f"Unsupported backup version: {version}")

    key_id = bundle.get("key_id")
    if key_id != _key_id(settings.db_key):
        raise ValueError(
            "Backup was created with a different DB_KEY. "
            "Key ID mismatch. Please set the correct DB_KEY."
        )

    key = _derive_key(settings.db_key)
    iv = bytes.fromhex(bundle["iv"])
    ciphertext = bytes.fromhex(bundle["data"])

    aesgcm = AESGCM(key)
    try:
        plaintext = aesgcm.decrypt(iv, ciphertext, None)
    except Exception as e:
        raise ValueError(f"Decryption failed (wrong key or corrupted file): {e}") from None

    tables = json.loads(plaintext.decode())

    db_path = db_path or settings.database_path
    backup_db_path = db_path + ".pre-restore"
    Path(db_path).rename(backup_db_path)

    try:
        from assistant.backend.db.schema import init_db

        await init_db(db_path)
        _import_tables_sync(db_path, tables)
        Path(backup_db_path).unlink()
    except Exception:
        if Path(backup_db_path).exists():
            Path(backup_db_path).rename(db_path)
        raise

    return {
        "restored_from": str(src),
        "db_path": db_path,
        "tables_restored": len(tables),
    }


async def _export_tables_unencrypted(db_path: str) -> dict:
    """Export all tables from an unencrypted SQLite database."""
    import sqlite3

    conn = connect_plain(db_path)
    conn.row_factory = sqlite3.Row

    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name",
    )
    table_names = [r["name"] for r in cursor.fetchall()]

    tables: dict[str, list[dict]] = {}
    for tname in table_names:
        if tname in ("sqlite_sequence", "sqlite_stat1", "sqlite_stat4"):
            continue
        cols = [desc[1] for desc in conn.execute(f"PRAGMA table_info({tname})")]
        rows = conn.execute(f"SELECT * FROM {tname}").fetchall()
        tables[tname] = [dict(zip(cols, row, strict=True)) for row in rows]

    conn.close()
    return tables


async def migrate_from_encrypted(encrypted_src: str | Path, *, db_path: str | None = None) -> dict:
    """Migrate an encrypted SQLite database to a new encrypted database.

    Reads all data from an encrypted source (using the current DB_KEY),
    then creates a fresh encrypted database at ``db_path`` (or
    ``settings.database_path`` if not specified) and imports all data.
    The source file is moved to <db_path>.encrypted.backup.

    Use this when migrating between encryption keys or when the source
    encrypted DB cannot be read as unencrypted.

    Args:
        encrypted_src: Path to the existing encrypted SQLite file.
        db_path: Target database path. Defaults to ``settings.database_path``.

    Returns:
        dict with migration stats.
    """
    if not settings.db_key:
        raise ValueError("DB_KEY is not set. Set it in .env first.")

    src_path = Path(encrypted_src)
    if not src_path.exists():
        raise FileNotFoundError(f"Source database not found: {src_path}")

    db_path = db_path or settings.database_path
    backup_path = db_path + ".encrypted.backup"

    if Path(db_path).exists():
        Path(db_path).rename(backup_path)

    try:
        from assistant.backend.db.schema import init_db

        await init_db(db_path)
        async with _export_tables_encrypted(str(src_path)) as tables:
            _import_tables_sync(db_path, tables)
    except Exception:
        if Path(backup_path).exists():
            if Path(db_path).exists():
                Path(db_path).unlink()
            Path(backup_path).rename(db_path)
        raise

    return {
        "source": str(src_path),
        "new_encrypted_db": db_path,
        "source_moved_to": backup_path,
        "tables_migrated": len(tables),
    }


async def migrate_to_encrypted(unencrypted_src: str | Path, *, db_path: str | None = None) -> dict:
    """Migrate an unencrypted SQLite database to a new encrypted one.

    Reads all data from the unencrypted source file, then creates a new
    encrypted database at ``db_path`` (or ``settings.database_path`` if not
    specified) and imports all data. The original unencrypted file is moved
    to <db_path>.unencrypted.

    Args:
        unencrypted_src: Path to the existing unencrypted SQLite file.
        db_path: Target database path. Defaults to ``settings.database_path``.

    Returns:
        dict with migration stats.

    Raises:
        ValueError: If DB_KEY is not set.
    """
    if not settings.db_key:
        raise ValueError(
            "DB_KEY is not set. Set DB_KEY in .env first to enable encryption."
        )

    src_path = Path(unencrypted_src)
    if not src_path.exists():
        raise FileNotFoundError(f"Source database not found: {src_path}")

    db_path = db_path or settings.database_path
    backup_path = db_path + ".unencrypted"

    if Path(db_path).exists():
        Path(db_path).rename(backup_path)

    try:
        from assistant.backend.db.schema import init_db

        await init_db(db_path)
        tables = await _export_tables_unencrypted(str(src_path))
        _import_tables_sync(db_path, tables)
    except Exception:
        if Path(backup_path).exists():
            if Path(db_path).exists():
                Path(db_path).unlink()
            Path(backup_path).rename(db_path)
        raise

    return {
        "source": str(src_path),
        "new_encrypted_db": db_path,
        "old_db_moved_to": backup_path,
        "tables_migrated": len(tables),
    }
