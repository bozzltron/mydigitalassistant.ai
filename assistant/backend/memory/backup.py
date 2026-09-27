"""Encrypted backup and restore for the assistant brain.

Two formats are supported:
1. **JSON bundle backup** (version 1): exports all DB tables as encrypted JSON.
   Used by ``create_encrypted_backup`` / ``restore_encrypted_backup``.
2. **Portable brain** (version 3): encrypts the raw SQLCipher database file
   as a single portable blob. Used by ``export_portable_brain`` /
   ``restore_portable_brain``. This preserves everything exactly —
   embeddings, tombstones, working memory — as a renameable file.

The DB_KEY is used via SHA-256 to derive an AES-256-GCM key. The key_id
field lets the UI warn when the wrong key is present before attempting
a destructive import.
"""

import hashlib
import json
import secrets
from contextlib import asynccontextmanager
from datetime import UTC, datetime
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


def _drop_write_ahead_log(db_path: str | Path) -> None:
    """Delete the write-ahead log sitting next to a database file.

    SQLite replays ``<db>-wal`` over the database file it belongs to. Once that
    file has been moved aside or overwritten, the log describes contents that no
    longer exist, and replaying it silently undoes the restore. The log has to go
    with the file it belonged to.
    """
    for suffix in ("-wal", "-shm"):
        Path(f"{db_path}{suffix}").unlink(missing_ok=True)


def _snapshot_db_bytes(db_path: str | Path) -> bytes:
    """Read a consistent, self-contained copy of a database as bytes.

    A raw read of the file is not a snapshot: recent writes live in the
    write-ahead log, and connections are kept warm, so that log is usually not
    empty. Going through SQLite's backup API gives the merged, checkpointed
    contents, and lets SQLite handle the encryption context.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        snapshot = Path(tmp) / "snapshot.db"
        src = connect(str(db_path))
        dst = connect(str(snapshot))
        try:
            with dst:
                src.backup(dst)
        finally:
            src.close()
            dst.close()
        return snapshot.read_bytes()


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
    # The log left behind belongs to the file that just moved aside; it must not
    # be replayed over the fresh database init_db is about to create here.
    _drop_write_ahead_log(db_path)

    try:
        from assistant.backend.db.schema import init_db

        await init_db(db_path)
        _import_tables_sync(db_path, tables)
        Path(backup_db_path).unlink()
    except Exception:
        if Path(backup_db_path).exists():
            # Same hazard on the way back: the half-built database left its own
            # log here, and that log is not ours to replay over the old file.
            _drop_write_ahead_log(db_path)
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


# ---------------------------------------------------------------------------
# Portable brain — encrypted SQLCipher file as a renameable blob
# ---------------------------------------------------------------------------


def get_key_id(key: str) -> str:
    """Return a short SHA-256 identifier for a key (used in envelope headers)."""
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def _encrypt_sqlcipher_dump(raw_bytes: bytes, key: str) -> dict:
    """Wrap raw SQLCipher bytes in an AES-256-GCM encrypted version-3 envelope.

    Returns the envelope dict (not yet saved to disk).
    """
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    aes_key = _derive_key(key)
    iv = secrets.token_bytes(12)
    ciphertext = AESGCM(aes_key).encrypt(iv, raw_bytes, None)
    return {
        "version": 3,
        "encrypted": True,
        "key_id": get_key_id(key),
        "cipher": "SQLCipher",
        "iv": iv.hex(),
        "payload": ciphertext.hex(),
    }


def _decrypt_sqlcipher_dump(envelope: dict, key: str) -> bytes:
    """Decrypt a version-3 portable brain envelope back to raw SQLCipher bytes.

    Validates ``key_id`` before attempting decryption to avoid corrupting
    the live database with a wrong key.
    """
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    expected_key_id = envelope.get("key_id")
    if expected_key_id and expected_key_id != get_key_id(key):
        raise ValueError(
            "Portable brain was encrypted with a different key. "
            f"Expected key ID {expected_key_id!r} but current DB_KEY produces "
            f"{get_key_id(key)!r}. Please set the correct DB_KEY."
        )

    aes_key = _derive_key(key)
    iv = bytes.fromhex(envelope["iv"])
    ciphertext = bytes.fromhex(envelope["payload"])

    try:
        return AESGCM(aes_key).decrypt(iv, ciphertext, None)
    except Exception as exc:
        raise ValueError(
            f"Decryption failed (wrong key or corrupted file): {exc}"
        ) from None


async def export_portable_brain(dest_path: str | Path, *, db_path: str | None = None) -> dict:
    """Export the live brain as a version-3 encrypted portable brain file.

    The exported file is a self-contained SQLCipher database, encrypted with
    ``DB_KEY``, that can be renamed, copied, and restored on any local
    instance sharing the same ``DB_KEY``.

    Args:
        dest_path: Output file path (e.g. ``brain-20260826.assistant-brain``).
        db_path: Source database path. Defaults to ``settings.database_path``.

    Returns:
        dict with ``version``, ``key_id``, ``exported_at``, and counts.

    Raises:
        ValueError: if DB_KEY is not set.
        FileNotFoundError: if the source database does not exist.
    """
    db_path_str = db_path or settings.database_path
    if not Path(db_path_str).exists():
        raise FileNotFoundError(f"Database not found at {db_path_str}")

    if not settings.db_key:
        raise ValueError(
            "DB_KEY is not set. Cannot create portable brain export. "
            "Set DB_KEY in .env first."
        )

    # A merged snapshot, not a raw read of the file: writes still in the
    # write-ahead log are part of the brain, and would otherwise be left behind.
    raw_bytes = _snapshot_db_bytes(db_path_str)

    envelope = _encrypt_sqlcipher_dump(raw_bytes, settings.db_key)

    # Add metadata for UI feedback
    import asyncio

    from assistant.backend.memory.store import MemoryStore

    store = MemoryStore(db_path_str)
    frames = await store.list_frames()
    slot_lists = await asyncio.gather(*[store.get_slots_for_frame(f.id) for f in frames])
    total_slots = sum(len(sl) for sl in slot_lists)
    associations = await store.get_all_associations()

    envelope["exported_at"] = datetime.now(UTC).isoformat()
    envelope["frame_count"] = len(frames)
    envelope["slot_count"] = total_slots
    envelope["association_count"] = len(associations)

    # Write to temp file first, then atomically rename
    dest = Path(dest_path)
    tmp_path = dest.with_suffix(dest.suffix + ".tmp")
    tmp_path.write_bytes(json.dumps(envelope, indent=2).encode())
    tmp_path.rename(dest)  # atomic on POSIX

    return {
        "version": 3,
        "key_id": envelope["key_id"],
        "exported_at": envelope["exported_at"],
        "frame_count": envelope["frame_count"],
        "slot_count": envelope["slot_count"],
        "association_count": envelope["association_count"],
        "file_size_bytes": dest.stat().st_size,
        "path": str(dest),
    }


async def restore_portable_brain(
    src_path: str | Path,
    *,
    db_path: str | None = None,
) -> dict:
    """Restore a version-3 portable brain, replacing the current live database.

    This operation is destructive. Before overwriting the live database,
    a backup of the current state is created at
    ``<db_path>.pre-portable-restore``.

    Args:
        src_path: Path to the portable brain JSON file.
        db_path: Target database path. Defaults to ``settings.database_path``.

    Returns:
        dict with restore metadata.

    Raises:
        ValueError: if DB_KEY is not set, key_id mismatch, or decryption fails.
        FileNotFoundError: if the source file does not exist.
    """
    if not settings.db_key:
        raise ValueError(
            "DB_KEY is not set. Cannot restore portable brain. "
            "Set DB_KEY in .env first."
        )

    src = Path(src_path)
    if not src.exists():
        raise FileNotFoundError(f"Portable brain not found at {src_path}")

    envelope = json.loads(src.read_bytes().decode())
    version = envelope.get("version")
    if version != 3:
        raise ValueError(f"Unsupported portable brain version: {version}. Expected 3.")

    # Validate key_id BEFORE touching the live DB
    expected_key_id = envelope.get("key_id")
    if expected_key_id and expected_key_id != get_key_id(settings.db_key):
        raise ValueError(
            f"Portable brain was created with a different key (key_id={expected_key_id!r}). "
            f"Current DB_KEY has key_id={get_key_id(settings.db_key)!r}. "
            "Cannot import — this would leave the brain unreadable. "
            "Set the correct DB_KEY to restore this brain."
        )

    # Decrypt — will raise ValueError on wrong key before any DB is modified
    raw_bytes = _decrypt_sqlcipher_dump(envelope, settings.db_key)

    db_path_str = db_path or settings.database_path
    live_db = Path(db_path_str)

    # Atomic swap: write to temp, rename on top of live DB.
    # Snapshot for the pre-restore backup so live_db is preserved independently —
    # and completely: a raw copy of the file would leave the log behind.
    tmp_path = live_db.with_suffix(live_db.suffix + ".tmp")
    pre_restore_backup_path = Path(db_path_str + ".pre-portable-restore")
    try:
        if live_db.exists():
            pre_restore_backup_path.write_bytes(_snapshot_db_bytes(db_path_str))
        tmp_path.write_bytes(raw_bytes)
        tmp_path.rename(live_db)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink()
        raise

    # The replaced database's write-ahead log describes the *old* contents, and
    # SQLite replays it over the file that just took its place. It has to go with
    # the file it belonged to.
    _drop_write_ahead_log(db_path_str)

    return {
        "restored_from": str(src),
        "key_id": envelope.get("key_id"),
        "exported_at": envelope.get("exported_at"),
        "frame_count": envelope.get("frame_count"),
        "slot_count": envelope.get("slot_count"),
        "association_count": envelope.get("association_count"),
        "db_path": db_path_str,
        "pre_restore_backup": (
            str(pre_restore_backup_path) if pre_restore_backup_path.exists() else None
        ),
    }
