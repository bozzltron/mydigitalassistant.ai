"""Tests for the portable brain feature (version-3 encrypted SQLCipher dump).

Run with:
    DB_KEY="test-key-32-bytes-long-here!!" \
      pytest assistant/tests/test_brain_portable.py -v
"""

import json
import os
from pathlib import Path

import pytest
import pytest_asyncio

from assistant.backend.config import settings
from assistant.backend.db.schema import init_db
from assistant.backend.memory.backup import (
    _decrypt_sqlcipher_dump,
    _encrypt_sqlcipher_dump,
    export_portable_brain,
    get_key_id,
    restore_portable_brain,
)


@pytest.fixture
def db_key():
    return os.environ.get("DB_KEY", "test-key-32-bytes-long-here!!")


@pytest_asyncio.fixture
async def fresh_encrypted_db(tmp_path, db_key):
    """Create a fresh encrypted database with some data."""
    original_key = os.environ.get("DB_KEY")
    os.environ["DB_KEY"] = db_key

    db_path = str(tmp_path / "brain.db")
    await init_db(db_path)

    from assistant.backend.memory.store import MemoryStore

    store = MemoryStore(db_path)
    user = await store.create_user("TestUser")
    frame = await store.create_frame("TestFrame", "entity", owner_user_id=user.id)
    await store.upsert_slot(frame.id, "color", "blue", source_type="test")
    await store.create_association(
        from_frame_id=frame.id,
        to_frame_id=frame.id,
        relation_type="related_to",
    )

    if original_key is not None:
        os.environ["DB_KEY"] = original_key
    elif "DB_KEY" in os.environ:
        del os.environ["DB_KEY"]

    return db_path


class TestGetKeyId:
    def test_returns_16_char_hex(self, db_key):
        kid = get_key_id(db_key)
        assert len(kid) == 16
        assert all(c in "0123456789abcdef" for c in kid)

    def test_same_key_same_id(self, db_key):
        assert get_key_id(db_key) == get_key_id(db_key)

    def test_different_keys_different_ids(self):
        assert get_key_id("key1") != get_key_id("key2")


class TestEncryptDecryptRoundtrip:
    def test_encrypted_envelope_has_version_3(self, db_key):
        raw = b"hello world"
        env = _encrypt_sqlcipher_dump(raw, db_key)
        assert env["version"] == 3
        assert env["encrypted"] is True
        assert env["key_id"] == get_key_id(db_key)
        assert env["cipher"] == "SQLCipher"
        assert "iv" in env
        assert "payload" in env

    def test_decrypt_returns_original_bytes(self, db_key):
        raw = b"\x00\x01\x02" * 100
        env = _encrypt_sqlcipher_dump(raw, db_key)
        decrypted = _decrypt_sqlcipher_dump(env, db_key)
        assert decrypted == raw

    def test_decrypt_wrong_key_raises(self, db_key):
        raw = b"test data"
        env = _encrypt_sqlcipher_dump(raw, db_key)
        with pytest.raises(ValueError, match="different key"):
            _decrypt_sqlcipher_dump(env, "wrong-key-32-bytes-here!!!!")

    def test_tampered_ciphertext_raises(self, db_key):
        raw = b"test data"
        env = _encrypt_sqlcipher_dump(raw, db_key)
        env["payload"] = env["payload"][:-4] + "0000"
        with pytest.raises(ValueError, match="Decryption failed"):
            _decrypt_sqlcipher_dump(env, db_key)


class TestExportPortableBrain:
    @pytest.mark.skipif(
        'not os.environ.get("DB_KEY")',
        reason="DB_KEY not set",
    )
    async def test_export_creates_file(self, fresh_encrypted_db, tmp_path, db_key):
        dest = str(tmp_path / "brain.assistant-brain")
        result = await export_portable_brain(dest, db_path=fresh_encrypted_db)
        assert Path(dest).exists()
        assert result["version"] == 3
        assert result["key_id"] == get_key_id(db_key)
        assert result["frame_count"] == 1
        assert result["slot_count"] == 1

    @pytest.mark.skipif(
        'not os.environ.get("DB_KEY")',
        reason="DB_KEY not set",
    )
    async def test_export_file_is_valid_json(self, fresh_encrypted_db, tmp_path):
        dest = str(tmp_path / "brain.assistant-brain")
        await export_portable_brain(dest, db_path=fresh_encrypted_db)
        with open(dest) as f:
            env = json.load(f)
        assert env["version"] == 3
        assert env["encrypted"] is True
        assert "exported_at" in env
        assert "frame_count" in env

    @pytest.mark.skipif(
        'not os.environ.get("DB_KEY")',
        reason="DB_KEY not set",
    )
    async def test_export_counts_are_accurate(self, fresh_encrypted_db, tmp_path):
        dest = str(tmp_path / "brain.assistant-brain")
        result = await export_portable_brain(dest, db_path=fresh_encrypted_db)
        assert result["frame_count"] >= 1
        assert result["slot_count"] >= 1


class TestRestorePortableBrain:
    @pytest.mark.skipif(
        'not os.environ.get("DB_KEY")',
        reason="DB_KEY not set",
    )
    async def test_restore_overwrites_db(self, fresh_encrypted_db, tmp_path, db_key):
        from assistant.backend.memory.store import MemoryStore

        dest = str(tmp_path / "exported.assistant-brain")
        await export_portable_brain(dest, db_path=fresh_encrypted_db)

        restored_db = str(tmp_path / "restored.db")
        await init_db(restored_db)
        result = await restore_portable_brain(dest, db_path=restored_db)

        assert result["restored_from"] == str(Path(dest).resolve())
        store2 = MemoryStore(restored_db)
        frames = await store2.list_frames()
        assert len(frames) >= 1

    @pytest.mark.skipif(
        'not os.environ.get("DB_KEY")',
        reason="DB_KEY not set",
    )
    async def test_restore_creates_pre_restore_backup(self, fresh_encrypted_db, tmp_path):
        dest = str(tmp_path / "exported.assistant-brain")
        await export_portable_brain(dest, db_path=fresh_encrypted_db)

        restored_db = str(tmp_path / "restored.db")
        await init_db(restored_db)
        result = await restore_portable_brain(dest, db_path=restored_db)

        assert result["pre_restore_backup"] is not None
        assert Path(result["pre_restore_backup"]).exists()

    @pytest.mark.skipif(
        'not os.environ.get("DB_KEY")',
        reason="DB_KEY not set",
    )
    async def test_restore_preserves_all_data(self, fresh_encrypted_db, tmp_path):
        import asyncio

        dest = str(tmp_path / "exported.assistant-brain")
        await export_portable_brain(dest, db_path=fresh_encrypted_db)

        restored_db = str(tmp_path / "restored.db")
        await init_db(restored_db)
        await restore_portable_brain(dest, db_path=restored_db)

        from assistant.backend.memory.store import MemoryStore

        store = MemoryStore(fresh_encrypted_db)
        orig_frames = await store.list_frames()
        orig_slot_lists = await asyncio.gather(
            *[store.get_slots_for_frame(f.id) for f in orig_frames]
        )
        orig_slots = sum(len(sl) for sl in orig_slot_lists)

        store2 = MemoryStore(restored_db)
        restored_frames = await store2.list_frames()
        restored_slot_lists = await asyncio.gather(
            *[store2.get_slots_for_frame(f.id) for f in restored_frames]
        )
        restored_slots = sum(len(sl) for sl in restored_slot_lists)

        assert len(restored_frames) == len(orig_frames)
        assert restored_slots == orig_slots

    @pytest.mark.skipif(
        'not os.environ.get("DB_KEY")',
        reason="DB_KEY not set",
    )
    async def test_restore_wrong_key_raises_before_modifying_db(
        self, fresh_encrypted_db, tmp_path, monkeypatch
    ):

        dest = str(tmp_path / "exported.assistant-brain")
        await export_portable_brain(dest, db_path=fresh_encrypted_db)

        restored_db = str(tmp_path / "restored.db")
        await init_db(restored_db)

        monkeypatch.setattr(settings, "db_key", "completely-different-key-here!!")
        with pytest.raises(ValueError, match="different key"):
            await restore_portable_brain(dest, db_path=restored_db)

    @pytest.mark.skipif(
        'not os.environ.get("DB_KEY")',
        reason="DB_KEY not set",
    )
    async def test_restore_unsupported_version_raises(self, fresh_encrypted_db, tmp_path, db_key):
        dest = str(tmp_path / "exported.assistant-brain")
        await export_portable_brain(dest, db_path=fresh_encrypted_db)

        with open(dest) as f:
            env = json.load(f)
        env["version"] = 99
        with open(dest, "w") as f:
            json.dump(env, f)

        restored_db = str(tmp_path / "restored.db")
        await init_db(restored_db)
        with pytest.raises(ValueError, match="Unsupported portable brain version"):
            await restore_portable_brain(dest, db_path=restored_db)

    @pytest.mark.skipif(
        'not os.environ.get("DB_KEY")',
        reason="DB_KEY not set",
    )
    async def test_restore_nonexistent_file_raises(self, tmp_path):
        restored_db = str(tmp_path / "restored.db")
        await init_db(restored_db)
        with pytest.raises(FileNotFoundError):
            await restore_portable_brain("/nonexistent/path.assistant-brain", db_path=restored_db)
