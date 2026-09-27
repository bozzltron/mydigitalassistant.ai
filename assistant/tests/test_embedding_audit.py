"""The startup embedding audit.

`_check_embedding_model_mismatch` used to compare exactly one thing: the
`metadata.embedding_model` key against the configured model. That key is a claim
someone wrote, not a measurement, so it agreed with the config while the data
disagreed completely -- 1894 of 1895 live frames had no vector the retriever could
reach, and the backend started clean every time.

The audit now measures the table. These tests pin that, and they are built so the
metadata key is deliberately *correct* throughout: that is the whole scenario.
"""

import logging

from assistant.backend.config import settings
from assistant.backend.db.schema import init_db
from assistant.backend.main import _check_embedding_model_mismatch
from assistant.backend.memory.metadata import (
    METADATA_KEY_EMBEDDING_MODEL,
    set_metadata,
)
from assistant.backend.memory.store import MemoryStore

CONFIGURED = "qwen3-embedding:0.6b"
STALE = "nomic-embed-text"


async def _fresh_store(tmp_path):
    db_path = str(tmp_path / "audit.db")
    await init_db(db_path)
    return db_path, MemoryStore(db_path)


async def test_audit_warns_when_frames_lack_the_configured_model(
    tmp_path, monkeypatch, caplog
):
    """The failure this exists for: metadata says qwen3, the rows say nomic.

    Both labels coexist in frame_embeddings, so every search filtered on
    `embedding_model = 'qwen3-embedding:0.6b'` found nothing to search.
    """
    monkeypatch.setattr(settings, "embedding_model", CONFIGURED)
    db_path, store = await _fresh_store(tmp_path)

    frame = await store.create_frame("why_not", "entity")
    await store.upsert_slot(frame.id, "label", "Friend Music Records")
    await store.store_frame_embedding(frame.id, [1.0] + [0.0] * 1023, STALE)

    # The metadata key agrees with the config. It always did. That is the trap.
    await set_metadata(db_path, METADATA_KEY_EMBEDDING_MODEL, CONFIGURED)

    with caplog.at_level(logging.WARNING, logger="assistant.backend.main"):
        await _check_embedding_model_mismatch(db_path)

    warnings = " ".join(r.getMessage() for r in caplog.records)
    assert "1 live frames have no" in warnings
    assert CONFIGURED in warnings
    assert "reembed" in warnings


async def test_audit_warns_when_more_than_one_model_is_stored(
    tmp_path, monkeypatch, caplog
):
    """Two labels means one of them can never be found by a search."""
    monkeypatch.setattr(settings, "embedding_model", CONFIGURED)
    db_path, store = await _fresh_store(tmp_path)

    old = await store.create_frame("mountain_in_the_wolf", "entity")
    new = await store.create_frame("why_not", "entity")
    await store.store_frame_embedding(old.id, [1.0] + [0.0] * 767, STALE)
    await store.store_frame_embedding(new.id, [1.0] + [0.0] * 1023, CONFIGURED)

    await set_metadata(db_path, METADATA_KEY_EMBEDDING_MODEL, CONFIGURED)

    with caplog.at_level(logging.WARNING, logger="assistant.backend.main"):
        await _check_embedding_model_mismatch(db_path)

    warnings = " ".join(r.getMessage() for r in caplog.records)
    assert "more than one model" in warnings
    assert STALE in warnings and CONFIGURED in warnings


async def test_completed_migration_is_not_a_warning(tmp_path, monkeypatch, caplog):
    """Two labels after a finished re-embed is normal, and must not cry wolf.

    `reembed_db` keeps the previous model's vectors on purpose ("old embeddings
    are retained until this completes successfully"), so every completed
    migration leaves two labels in the table. An audit that warns on that fires
    on every boot forever, and a warning you always see is a warning you stop
    reading -- which is how a 1894-of-1895 failure stayed invisible in the first
    place. Stale-but-unreachable is info; stale-and-stranding is a warning.
    """
    monkeypatch.setattr(settings, "embedding_model", CONFIGURED)
    db_path, store = await _fresh_store(tmp_path)

    for i in range(3):
        frame = await store.create_frame(f"frame_{i}", "entity")
        # The old model left a vector behind; the new model covers the frame.
        await store.store_frame_embedding(frame.id, [1.0] + [0.0] * 767, STALE)
        await store.store_frame_embedding(frame.id, [1.0] + [0.0] * 1023, CONFIGURED)

    await set_metadata(db_path, METADATA_KEY_EMBEDDING_MODEL, CONFIGURED)

    with caplog.at_level(logging.WARNING, logger="assistant.backend.main"):
        await _check_embedding_model_mismatch(db_path)

    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []


async def test_audit_stays_quiet_when_every_frame_is_embedded(
    tmp_path, monkeypatch, caplog
):
    """The check has to be able to say nothing, or nobody will read it."""
    monkeypatch.setattr(settings, "embedding_model", CONFIGURED)
    db_path, store = await _fresh_store(tmp_path)

    frame = await store.create_frame("why_not", "entity")
    await store.store_frame_embedding(frame.id, [1.0] + [0.0] * 1023, CONFIGURED)
    await set_metadata(db_path, METADATA_KEY_EMBEDDING_MODEL, CONFIGURED)

    with caplog.at_level(logging.WARNING, logger="assistant.backend.main"):
        await _check_embedding_model_mismatch(db_path)

    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []


async def test_audit_ignores_soft_deleted_frames(tmp_path, monkeypatch, caplog):
    """A frame on its way out should not be reported as unretrievable.

    `deleted_at IS NOT NULL` is how GC retires a frame; its embedding is expected
    to be gone. Counting those would make the warning fire on every healthy
    database and train the reader to ignore it.
    """
    monkeypatch.setattr(settings, "embedding_model", CONFIGURED)
    db_path, store = await _fresh_store(tmp_path)

    frame = await store.create_frame("obsolete", "entity")
    # GC retires frames by stamping deleted_at; there is no store method for it
    # outside the gc/merge paths, so do it the way they do.
    async with store._connect() as db:
        await db.execute(
            "UPDATE frames SET deleted_at = datetime('now') WHERE id = ?", (frame.id,)
        )
        await db.commit()

    await set_metadata(db_path, METADATA_KEY_EMBEDDING_MODEL, CONFIGURED)

    with caplog.at_level(logging.WARNING, logger="assistant.backend.main"):
        await _check_embedding_model_mismatch(db_path)

    assert "live frames have no" not in " ".join(
        r.getMessage() for r in caplog.records
    )


async def test_audit_reports_a_failure_rather_than_raising(
    tmp_path, monkeypatch, caplog
):
    """A query that cannot run must be visible, not swallowed into a debug line.

    This is not hypothetical: the first version of the audit called
    `execute_fetchone`, which the SQLCipher connection wrapper does not expose.
    The `except` caught it, logged at DEBUG, and the audit reported nothing at all
    -- a silent-failure mode, in a function whose entire job is to not be silent.
    """
    monkeypatch.setattr(settings, "embedding_model", CONFIGURED)
    db_path, store = await _fresh_store(tmp_path)
    frame = await store.create_frame("why_not", "entity")
    await store.store_frame_embedding(frame.id, [1.0] + [0.0] * 1023, STALE)
    await set_metadata(db_path, METADATA_KEY_EMBEDDING_MODEL, CONFIGURED)

    # Drop the table the audit counts against. init_db would recreate it, but the
    # audit has to cope with a database it does not recognise.
    async with store._connect() as db:
        await db.execute("DROP TABLE frame_embeddings")
        await db.commit()

    with caplog.at_level(logging.WARNING, logger="assistant.backend.main"):
        await _check_embedding_model_mismatch(db_path)  # must not raise

    assert "could not run" in " ".join(r.getMessage() for r in caplog.records)
