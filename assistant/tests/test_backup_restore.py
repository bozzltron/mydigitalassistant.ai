"""Tests for the encrypted backup/restore module.

Run encrypted tests with:
    DB_KEY="test-backup-key-32bytes-long-here!" \
      pytest assistant/tests/test_backup_restore.py -v
"""

import json
from pathlib import Path

import pytest
import pytest_asyncio

from assistant.backend.db.schema import init_db
from assistant.backend.db.sqlcipher import connect_plain
from assistant.backend.memory.backup import (
    _derive_key,
    _export_tables_unencrypted,
    create_encrypted_backup,
    migrate_to_encrypted,
    restore_encrypted_backup,
)


@pytest_asyncio.fixture
async def encrypted_db(tmp_path):
    """Create an initialized encrypted database.

    When DB_KEY is set, the database is encrypted. Otherwise it is plain.
    """
    db_path = str(tmp_path / "brain.db")
    await init_db(db_path)
    return db_path


class TestKeyDerivation:
    def test_derive_key_is_32_bytes(self):
        key = _derive_key("test-key")
        assert len(key) == 32

    def test_same_key_same_derivation(self):
        k1 = _derive_key("secret")
        k2 = _derive_key("secret")
        assert k1 == k2

    def test_different_keys_different_derivation(self):
        k1 = _derive_key("key1")
        k2 = _derive_key("key2")
        assert k1 != k2


class TestExportUnencrypted:
    async def test_exports_all_tables(self, tmp_path):
        db_path = str(tmp_path / "plain.db")
        conn = connect_plain(db_path)
        conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute("INSERT INTO users (name) VALUES ('Alice')")
        conn.commit()
        conn.close()
        tables = await _export_tables_unencrypted(db_path)
        assert "users" in tables
        assert "sqlite_sequence" not in tables
        user_rows = [r for r in tables["users"] if r["name"] == "Alice"]
        assert len(user_rows) == 1


@pytest.mark.skipif(
    'not os.environ.get("DB_KEY")',
    reason="DB_KEY not set — run with DB_KEY env var to activate encrypted tests",
)
class TestCreateEncryptedBackup:
    async def test_creates_file(self, encrypted_db, tmp_path):
        dest = str(tmp_path / "backup.enc.json")
        result = await create_encrypted_backup(dest, db_path=encrypted_db)
        assert Path(dest).exists()
        assert result["version"] == 1
        assert result["key_id"] is not None

    async def test_bundle_has_required_fields(self, encrypted_db, tmp_path):
        dest = str(tmp_path / "backup.enc.json")
        await create_encrypted_backup(dest, db_path=encrypted_db)
        with open(dest) as f:
            bundle = json.load(f)
        assert bundle["version"] == 1
        assert "key_id" in bundle
        assert "iv" in bundle
        assert "data" in bundle
        assert len(bundle["iv"]) > 0
        assert len(bundle["data"]) > 0


@pytest.mark.skipif(
    'not os.environ.get("DB_KEY")',
    reason="DB_KEY not set — run with DB_KEY env var to activate encrypted tests",
)
class TestRestoreEncryptedBackup:
    async def test_restores_database(self, encrypted_db, tmp_path):
        from assistant.backend.memory.store import MemoryStore

        store = MemoryStore(encrypted_db)
        user = await store.create_user("Alice")
        frame = await store.create_frame("TestFrame", "entity", owner_user_id=user.id)
        await store.upsert_slot(frame.id, "color", "blue", source_type="user")

        dest = str(tmp_path / "backup.enc.json")
        await create_encrypted_backup(dest, db_path=encrypted_db)

        restored_db = str(tmp_path / "restored.db")
        await init_db(restored_db)
        await restore_encrypted_backup(dest, db_path=restored_db)

        store2 = MemoryStore(restored_db)
        users = await store2.list_users()
        assert len(users) == 1
        assert users[0].name == "Alice"


@pytest.mark.skipif(
    'not os.environ.get("DB_KEY")',
    reason="DB_KEY not set — run with DB_KEY env var to activate encrypted tests",
)
class TestMigrateToEncrypted:
    async def test_migrates_and_preserves_data(self, tmp_path):
        source_db = str(tmp_path / "unencrypted.db")
        conn = connect_plain(source_db)
        conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute("INSERT INTO users (name) VALUES ('Alice')")
        conn.commit()
        conn.close()

        target_db = str(tmp_path / "encrypted.db")
        result = await migrate_to_encrypted(source_db, db_path=target_db)
        assert result["source"] == source_db
        assert result["old_db_moved_to"] is not None

        from assistant.backend.memory.store import MemoryStore

        store = MemoryStore(result["new_encrypted_db"])
        users = await store.list_users()
        assert len(users) == 1
        assert users[0].name == "Alice"
