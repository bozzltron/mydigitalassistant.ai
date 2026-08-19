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
from pathlib import Path

import aiosqlite

from assistant.backend.config import settings
from assistant.backend.db.sqlcipher import apply_db_key, patch_sqlite_for_sqlcipher

patch_sqlite_for_sqlcipher()


def _derive_key(key: str) -> bytes:
    """Derive a 32-byte AES key from DB_KEY using HKDF-SHA256."""
    return hashlib.sha256(key.encode()).digest()


def _key_id(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:16]


async def _export_tables(db_path: str) -> dict:
    """Export all tables from SQLite to a dict of table_name -> list of rows."""
    db = await aiosqlite.connect(db_path)
    apply_db_key(db)
    await db.execute("PRAGMA foreign_keys = OFF")
    tables: dict[str, list[dict]] = {}

    async with db:
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

    return tables


async def _import_tables(db_path: str, tables: dict[str, list[dict]]) -> None:
    """Import table data into a fresh database (schema must already exist)."""
    db = await aiosqlite.connect(db_path)
    apply_db_key(db)
    await db.execute("PRAGMA foreign_keys = OFF")
    await db.commit()

    async with db:
        for tname, rows in tables.items():
            if not rows:
                continue
            for row in rows:
                placeholders = ", ".join(["?"] * len(row))
                cols = ", ".join(row.keys())
                await db.execute(
                    f"INSERT OR IGNORE INTO {tname} ({cols}) VALUES ({placeholders})",
                    list(row.values()),
                )
        await db.commit()


async def create_encrypted_backup(dest_path: str | Path) -> dict:
    """Create an encrypted backup of the database.

    Args:
        dest_path: Output file path for the encrypted bundle.

    Returns:
        dict with metadata: version, key_id, db_size_bytes, backup_size_bytes
    """
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    db_path = settings.database_path
    if not Path(db_path).exists():
        raise FileNotFoundError(f"Database not found at {db_path}")

    if not settings.db_key:
        raise ValueError(
            "DB_KEY is not set. Cannot create encrypted backup. "
            "Set DB_KEY in .env first."
        )

    key = _derive_key(settings.db_key)
    key_id = _key_id(settings.db_key)

    tables = await _export_tables(db_path)
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


async def restore_encrypted_backup(src_path: str | Path) -> dict:
    """Restore an encrypted backup, overwriting the current database.

    Args:
        src_path: Path to the encrypted backup bundle.

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

    db_path = settings.database_path
    backup_db_path = db_path + ".pre-restore"
    Path(db_path).rename(backup_db_path)

    try:
        from assistant.backend.db.schema import init_db

        await init_db(db_path)
        await _import_tables(db_path, tables)
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
    """Export all tables from an unencrypted SQLite database.

    Uses a raw aiosqlite connection without SQLCipher key — for unencrypted DBs.
    """
    import sqlite3

    unpatched_conn = sqlite3.connect(db_path)
    cursor = unpatched_conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name",
    )
    table_names = [r[0] for r in cursor.fetchall()]
    unpatched_conn.close()

    db = await aiosqlite.connect(db_path)
    await db.execute("PRAGMA foreign_keys = OFF")
    tables: dict[str, list[dict]] = {}

    async with db:
        for tname in table_names:
            if tname in ("sqlite_sequence", "sqlite_stat1", "sqlite_stat4"):
                continue
            rows = await db.execute_fetchall(f"SELECT * FROM {tname}")
            cols = [
                desc[1] for desc in await db.execute_fetchall(f"PRAGMA table_info({tname})")
            ]
            tables[tname] = [dict(zip(cols, row, strict=True)) for row in rows]

    return tables


async def migrate_to_encrypted(unencrypted_src: str | Path) -> dict:
    """Migrate an unencrypted SQLite database to a new encrypted one.

    Reads all data from the unencrypted source file, then creates a new
    encrypted database at settings.database_path and imports all data.
    The original unencrypted file is moved to <db_path>.unencrypted.

    Args:
        unencrypted_src: Path to the existing unencrypted SQLite file.

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

    db_path = settings.database_path
    backup_path = db_path + ".unencrypted"

    if Path(db_path).exists():
        Path(db_path).rename(backup_path)

    try:
        from assistant.backend.db.schema import init_db

        await init_db(db_path)
        tables = await _export_tables_unencrypted(str(src_path))
        await _import_tables(db_path, tables)
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
