"""Regression: memory holds what a file *is*, never what it *contains*.

The rule, and its reason, from `plans/2026-10-01-file-support-diagnosis.md`:

    Bytes live on disk and are read verbatim. Memory records identity (name, path,
    size, owner) and meaning (entities). Content belongs in neither.

The code had already decided this — `retrieval.py` excludes content slots from the
prompt with the reasoning *"never prefill it, or the model answers from a snippet"* —
but enforced it only at **render** time. Four write sites still put content into
memory: create, edit, upload, and **read** (which refreshed the preview on every
file read, putting content into memory as a side effect of looking at it).

The cost was not the wasted rows. It was the fallback in `read_file`:

    except FileNotFoundError:
        pass  # Not on disk — fall back to memory slots.
    if not content:
        content = slots_dict.get("file_content_preview") or ""

A missing file returned a stale 200-character copy, and the model answered believing
it had read the file. Two representations of one thing, and the worse one winning when
the better was unavailable.

These tests pin the boundary at the two places it now holds: the write (refused at the
store, where every writer funnels) and the read (a missing file reports missing).
"""

from __future__ import annotations

import pytest

from assistant.backend.memory.store import (
    FILE_CONTENT_HINT_SLOTS,
    FileContentInMemoryError,
)


class TestContentIsRefusedAtWriteTime:
    """The store is where every writer funnels, so the rule cannot be sidestepped by
    a call site the way a per-writer fix could."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("key", FILE_CONTENT_HINT_SLOTS)
    async def test_a_content_key_is_refused(self, store, key):
        frame = await store.create_frame("file_notes.txt", "entity")
        with pytest.raises(FileContentInMemoryError):
            await store.upsert_slot(frame_id=frame.id, key=key, value="file contents")

    @pytest.mark.asyncio
    async def test_the_error_says_what_to_do_instead(self, store):
        frame = await store.create_frame("file_notes.txt", "entity")
        with pytest.raises(FileContentInMemoryError) as exc:
            await store.upsert_slot(
                frame_id=frame.id, key="file_content_preview", value="x"
            )
        message = str(exc.value)
        assert "read_file" in message, "the refusal does not say where content lives"
        assert "sandbox" in message

    @pytest.mark.asyncio
    async def test_identity_keys_are_still_allowed(self, store):
        """The guard must not block the slots that make a file findable."""
        frame = await store.create_frame("file_notes.txt", "entity")
        for key, value in (
            ("file_name", "notes.txt"),
            ("file_ext", "txt"),
            ("file_size", "345"),
            ("file_safe_name", "notes.txt"),
            ("row_count", "0"),
        ):
            slot, _ = await store.upsert_slot(frame_id=frame.id, key=key, value=value)
            assert slot.value == value

    @pytest.mark.asyncio
    async def test_writing_a_file_frame_is_not_blocked(self, store):
        """Creating the frame itself is fine; only the content slot is refused."""
        frame = await store.create_frame(
            "file_report.csv", "entity", source_type="file_create"
        )
        assert frame.id is not None


class TestAMissingFileReportsMissing:
    """The behaviour the fallback prevented.

    `read_file` must not substitute memory for disk. That is the difference between
    the agent knowing it failed and believing it succeeded.
    """

    @pytest.mark.asyncio
    async def test_read_file_does_not_return_a_stale_preview(self, store, monkeypatch):
        from assistant.backend.pipeline import tool_executor as te

        # A frame that claims to describe a file, while no such file is on disk.
        frame = await store.create_frame(
            "file_ghost.txt", "entity", source_type="file_create"
        )
        await store.upsert_slot(frame_id=frame.id, key="file_name", value="ghost.txt")
        await store.upsert_slot(
            frame_id=frame.id, key="file_safe_name", value="ghost.txt"
        )

        te._store = store
        try:
            result = await te.execute_read_file(
                {"frame_name": "file_ghost.txt"}, user_id="1", session_id="s"
            )
        finally:
            te._store = None

        assert result.success is False, (
            "read_file succeeded for a file that is not on disk — it must be serving "
            "something from memory"
        )
        assert "not" in result.error.lower()
        assert "memory" in result.error.lower() or "sandbox" in result.error.lower()


class TestTheBoundaryIsSingleHomed:
    """One list, one home. It was duplicated in `retrieval` and `tool_executor`, which
    is two places for one rule to drift."""

    def test_the_hint_list_has_one_definition(self):
        from assistant.backend.memory import retrieval as retrieval_mod
        from assistant.backend.memory import store as store_mod
        from assistant.backend.pipeline import tool_executor as te

        # retrieval re-exports it for historical readers; tool_executor no longer
        # needs it at all. Either way, the value must be the store's.
        assert retrieval_mod.FILE_CONTENT_HINT_SLOTS is store_mod.FILE_CONTENT_HINT_SLOTS
        assert not hasattr(te, "FILE_CONTENT_HINT_SLOTS") or (
            te.FILE_CONTENT_HINT_SLOTS is store_mod.FILE_CONTENT_HINT_SLOTS
        )
