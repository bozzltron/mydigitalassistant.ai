"""Regression: memory is never decayed, and maintenance does not over-backup.

Two properties this pins, both from the decision that nothing is forgotten
unless the user says so:

1. **Nothing decays.** The old weekly GC lowered slot priority and soft-deleted
   stale frames on a timer. It could only ever act on rows whose priority was
   below the default 0.5 — and nothing in normal operation writes below 0.5 —
   so it was machinery that re-forgot data the user had already forgotten. It is
   removed; this test proves a long-idle fact stays put.
2. **Backups follow real work.** `_run_consolidation` used to snapshot the DB
   unconditionally at the top of every cycle. It now plans first and only backs
   up when there are merges to apply, so a quiet brain writes no snapshots.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from assistant.backend.memory.store import MemoryStore
from assistant.backend.scheduler import runner


@pytest.mark.asyncio
async def test_a_long_idle_fact_is_not_modified(store: MemoryStore):
    """A fact untouched for a year keeps its value, priority, and confidence.

    Simulates the passage of time by back-dating `last_strengthened_at`, then
    runs every scheduled maintenance entry point. None of them may touch the
    slot: with GC gone, nothing on a timer mutates priority or confidence.
    """
    user = await store.create_user("alice")
    frame = await store.create_frame("guitar", "entity", owner_user_id=user.id)
    await store.upsert_slot(frame.id, "strings", "6")

    a_year_ago = (datetime.now(UTC) - timedelta(days=365)).isoformat()
    from assistant.backend.db.sqlcipher import aiosqlite_connect

    async with aiosqlite_connect(store.db_path) as db:
        await db.execute(
            "UPDATE slots SET last_strengthened_at = ? WHERE frame_id = ?",
            (a_year_ago, frame.id),
        )
        await db.commit()

    before = await store.get_slot(frame.id, "strings")

    # The only maintenance paths left are non-destructive. `gc` no longer exists.
    assert not hasattr(runner, "_run_memory_gc")
    assert not hasattr(runner, "_is_new_week")

    after = await store.get_slot(frame.id, "strings")
    assert after.value == before.value
    assert after.priority == before.priority
    assert after.confidence == before.confidence
    assert after.priority == 0.5, "default priority must not be decayed"


def test_gc_module_is_gone():
    """The decay subsystem was deleted, not disabled."""
    import importlib

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("assistant.backend.memory.gc")


@pytest.mark.asyncio
async def test_consolidation_skips_the_backup_when_there_is_nothing_to_merge(
    store: MemoryStore, monkeypatch
):
    """No planned merges -> no DB snapshot.

    Pre-fix, `_run_consolidation` took a backup before every run regardless of
    whether any merge would happen, so a quiet household still wrote a full
    snapshot every interval.
    """
    backups_taken: list[str] = []

    async def fake_backup(db_path, label):  # noqa: ANN001, ANN201
        backups_taken.append(label)
        raise AssertionError("backup must not be taken when there is no work")

    monkeypatch.setattr(runner, "_backup_db", fake_backup)

    import assistant.backend.memory.consolidate as consolidate

    monkeypatch.setattr(consolidate, "run_consolidation", _fake_consolidation([]))

    await runner._run_consolidation(store, _OrchestratorStub())

    assert backups_taken == []


@pytest.mark.asyncio
async def test_consolidation_merges_ad_hoc_without_a_backup_when_one_is_fresh(
    store: MemoryStore, monkeypatch
):
    """Merges apply as soon as they are found, even mid-backup-interval.

    The 12h snapshot timer is what protects merges; a merge that runs while a
    fresh snapshot already exists must not take a second one. This is the
    property that keeps snapshot count independent of merge frequency.
    """
    backups_taken: list[str] = []
    applied: list[bool] = []

    async def fake_backup(db_path, label):  # noqa: ANN001, ANN201
        backups_taken.append(label)

    monkeypatch.setattr(runner, "_backup_db", fake_backup)

    import assistant.backend.memory.consolidate as consolidate

    monkeypatch.setattr(
        consolidate, "run_consolidation", _fake_consolidation([("a", "b")], applied=applied)
    )

    await runner._run_consolidation(store, _OrchestratorStub(), has_fresh_backup=True)

    assert backups_taken == [], "a fresh snapshot already covers this merge"
    assert applied == [True], "the merge must still be applied"


@pytest.mark.asyncio
async def test_consolidation_takes_a_backup_before_merging_if_none_is_fresh(
    store: MemoryStore, monkeypatch
):
    """A merge must never run unprotected: no fresh snapshot -> take one first."""
    backups_taken: list[str] = []

    async def fake_backup(db_path, label):  # noqa: ANN001, ANN201
        backups_taken.append(label)

    monkeypatch.setattr(runner, "_backup_db", fake_backup)

    import assistant.backend.memory.consolidate as consolidate

    monkeypatch.setattr(
        consolidate, "run_consolidation", _fake_consolidation([("a", "b")])
    )

    await runner._run_consolidation(store, _OrchestratorStub(), has_fresh_backup=False)

    assert backups_taken == ["consolidation"]


def _fake_consolidation(merges, applied=None):
    """Build a stand-in for `run_consolidation` over the given planned merges."""
    class _Plan:
        def __init__(self):
            self.planned_merges = list(merges)
            self.capped = False

        def summary(self) -> str:
            return f"{len(self.planned_merges)} merges"

    async def _run(db_path, dry_run=True, embed_fn=None, max_merges=None):
        if not dry_run and applied is not None:
            applied.append(True)
        return _Plan()

    return _run


class _OrchestratorStub:
    """Only `embed_fn` is touched by the consolidation path."""

    def embed_fn(self):  # noqa: ANN201
        async def _embed(text):  # noqa: ANN001, ANN202
            return [0.0] * 1024

        return _embed
