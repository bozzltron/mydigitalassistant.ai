"""Tests for brain export/import functionality."""

import pytest

from assistant.backend.db.schema import init_db
from assistant.backend.memory.store import MemoryStore


@pytest.fixture(autouse=True)
def _clear_db_key(monkeypatch):
    """Most tests assume a plain (unencrypted) export environment."""
    monkeypatch.setattr("assistant.backend.memory.store.settings.db_key", "")


async def test_export_brain_returns_version_and_frames(store: MemoryStore):
    """Export produces a well-formed JSON dict with version, frames, etc."""
    frame = await store.create_frame("guitar", "entity")
    await store.upsert_slot(frame.id, "strings", "6")

    brain = await store.export_brain()

    assert brain["version"] == 1
    assert "exported_at" in brain
    assert "frames" in brain
    assert len(brain["frames"]) == 1
    assert brain["frames"][0]["name"] == "guitar"
    assert len(brain["frames"][0]["slots"]) == 1
    assert brain["frames"][0]["slots"][0]["value"] == "6"


async def test_export_brain_includes_associations(store: MemoryStore):
    """Associations are exported correctly."""
    f1 = await store.create_frame("guitar", "entity")
    f2 = await store.create_frame("music", "concept")
    await store.create_association(
        from_frame_id=f1.id,
        to_frame_id=f2.id,
        relation_type="related_to",
    )

    brain = await store.export_brain()

    assert len(brain["associations"]) == 1
    assert brain["associations"][0]["from_frame_name"] == "guitar"
    assert brain["associations"][0]["to_frame_name"] == "music"


async def test_export_brain_excludes_embeddings(store: MemoryStore):
    """Embeddings are NOT exported (they are re-derived on import)."""
    frame = await store.create_frame("guitar", "entity")
    await store.upsert_slot(frame.id, "strings", "6")

    brain = await store.export_brain()

    assert all("embedding" not in f for f in brain["frames"])


async def test_import_brain_merge_preserves_existing(store: MemoryStore):
    """Merge mode upserts frames by name, existing data is preserved."""
    existing = await store.create_frame("guitar", "entity")
    await store.upsert_slot(existing.id, "strings", "6")

    brain_json = {
        "version": 1,
        "frames": [
            {
                "name": "guitar",
                "type": "entity",
                "slots": [
                    {"key": "brand", "value": "Fender"},
                ],
            },
            {
                "name": "piano",
                "type": "entity",
                "slots": [
                    {"key": "keys", "value": "88"},
                ],
            },
        ],
        "associations": [],
        "episodes": [],
        "feedbacks": [],
        "conflicts": [],
    }

    imported = await store.import_brain(brain_json, mode="merge")

    assert imported["frames"] == 1  # guitar existed (not counted), piano created (counted)
    assert imported["slots"] == 2  # Fender added to guitar + keys added to piano

    guitar = await store.get_frame_by_name("guitar")
    slots = {s.key: s.value for s in await store.get_slots_for_frame(guitar.id)}
    assert slots["strings"] == "6"  # original preserved
    assert slots["brand"] == "Fender"  # new slot added

    piano = await store.get_frame_by_name("piano")
    assert piano is not None


async def test_import_brain_overwrite_clears_and_reimports(store: MemoryStore):
    """Overwrite mode clears existing data and re-imports."""
    old = await store.create_frame("guitar", "entity")
    await store.upsert_slot(old.id, "strings", "6")

    brain_json = {
        "version": 1,
        "frames": [
            {
                "name": "piano",
                "type": "entity",
                "slots": [
                    {"key": "keys", "value": "88"},
                ],
            },
        ],
        "associations": [],
        "episodes": [],
        "feedbacks": [],
        "conflicts": [],
    }

    imported = await store.import_brain(brain_json, mode="overwrite")

    assert imported["frames"] == 1
    guitar = await store.get_frame_by_name("guitar")
    assert guitar is None  # old frame deleted
    piano = await store.get_frame_by_name("piano")
    assert piano is not None


async def test_import_brain_rejects_unknown_version(store: MemoryStore):
    """Unsupported export version raises ValueError."""
    brain_json = {
        "version": 99,
        "frames": [],
        "associations": [],
        "episodes": [],
        "feedbacks": [],
        "conflicts": [],
    }

    with pytest.raises(ValueError, match="version"):
        await store.import_brain(brain_json, mode="merge")


async def test_import_brain_imports_feedback(store: MemoryStore):
    """Feedback records are imported."""
    brain_json = {
        "version": 1,
        "frames": [],
        "associations": [],
        "episodes": [],
        "feedbacks": [
            {
                "message_id": "msg-1",
                "kind": "correction",
                "comment": "actually the guitar has 12 strings",
                "created_at": "2026-08-20T10:00:00Z",
            },
        ],
        "conflicts": [],
    }

    imported = await store.import_brain(brain_json, mode="merge")

    assert imported["feedbacks"] == 1
    async with store._connect() as db:
        rows = await db.execute_fetchall(
            "SELECT kind, comment FROM feedback WHERE message_id = ?",
            ("msg-1",),
        )
    assert len(rows) == 1
    assert rows[0][0] == "correction"


async def test_import_brain_imports_conflicts(store: MemoryStore):
    """Conflict records are imported."""
    await store.create_frame("guitar", "entity")

    brain_json = {
        "version": 1,
        "frames": [
            {"name": "guitar", "type": "entity", "slots": [{"key": "strings", "value": "12"}]},
        ],
        "associations": [],
        "episodes": [],
        "feedbacks": [],
        "conflicts": [
            {
                "frame_name": "guitar",
                "slot_key": "strings",
                "existing_value": "6",
                "new_value": "12",
                "resolved_value": "12",
                "status": "auto_resolved",
                "created_at": "2026-08-20T10:00:00Z",
                "resolved_at": "2026-08-20T10:01:00Z",
            },
        ],
    }

    imported = await store.import_brain(brain_json, mode="merge")

    assert imported["conflicts"] == 1
    conflicts = await store.get_conflicts()
    assert len(conflicts) == 1
    assert conflicts[0].status == "auto_resolved"


async def _encrypted_store(tmp_path, key: str):
    """Create a MemoryStore whose database is encrypted with ``key``."""
    import assistant.backend.memory.store as store_module

    db_path = str(tmp_path / "enc.db")
    store_module.settings.db_key = key
    await init_db(db_path)
    return MemoryStore(db_path)


async def test_encrypted_export_wraps_plain_payload(tmp_path, monkeypatch):
    """When DB_KEY is set, export returns an encrypted envelope."""
    monkeypatch.setattr("assistant.backend.memory.store.settings.db_key", "test-key-123")
    store = await _encrypted_store(tmp_path, "test-key-123")
    frame = await store.create_frame("guitar", "entity")
    await store.upsert_slot(frame.id, "strings", "6")

    brain = await store.export_brain()

    assert brain["version"] == 2
    assert brain["encrypted"] is True
    assert "key_id" in brain
    assert "iv" in brain
    assert "ciphertext" in brain


async def test_encrypted_import_decrypts_and_imports(tmp_path, monkeypatch):
    """Importing an encrypted export decrypts it with DB_KEY."""
    monkeypatch.setattr("assistant.backend.memory.store.settings.db_key", "demo-secret-key")
    store = await _encrypted_store(tmp_path, "demo-secret-key")

    brain_json = {
        "version": 1,
        "frames": [
            {
                "name": "piano",
                "type": "entity",
                "slots": [{"key": "keys", "value": "88"}],
            },
        ],
        "associations": [],
        "episodes": [],
        "feedbacks": [],
        "conflicts": [],
    }
    # Build encrypted envelope directly
    from assistant.backend.memory.store import _encrypt_brain_json
    envelope = _encrypt_brain_json(brain_json, "demo-secret-key")

    imported = await store.import_brain(envelope, mode="merge")
    assert imported["frames"] == 1
    piano = await store.get_frame_by_name("piano")
    assert piano is not None


async def test_encrypted_import_requires_same_key(tmp_path, monkeypatch):
    """Importing with a different DB_KEY than the export raises."""
    monkeypatch.setattr("assistant.backend.memory.store.settings.db_key", "wrong-key")
    store = await _encrypted_store(tmp_path, "wrong-key")

    from assistant.backend.memory.store import _encrypt_brain_json
    envelope = _encrypt_brain_json({"version": 1}, "correct-key")

    with pytest.raises(ValueError, match="different key"):
        await store.import_brain(envelope, mode="merge")


async def test_plain_export_still_works_without_db_key(monkeypatch, store):
    """When DB_KEY is empty, export stays plain version 1."""
    monkeypatch.setattr("assistant.backend.memory.store.settings.db_key", "")
    frame = await store.create_frame("guitar", "entity")
    await store.upsert_slot(frame.id, "strings", "6")

    brain = await store.export_brain()

    assert brain["version"] == 1
    assert brain.get("encrypted") is None
    assert brain["frames"][0]["name"] == "guitar"
