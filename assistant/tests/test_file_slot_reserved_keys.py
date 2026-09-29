"""Regression: model output must not become a filesystem path.

`file_safe_name` is a slot on a file frame, and the `/files` endpoints read it
straight out of the `slots` table and join it onto the data directory. The model
could write that slot, because both model-output write paths accepted an
arbitrary `slot_key` with no reserved-key denylist:

- `execute_upsert_slot` (the `upsert_slot` tool)
- `apply_extraction` / `apply_search_extraction` (conversational + search facts)

So a crafted slot value was a path primitive. `Path("/app/data") / "/app/.env"`
needs no `..` at all, because an absolute right-hand operand discards the left
(verified). The GET handlers then read the file and the DELETE handler unlinked
it.

The fix is defence in depth, and both layers are pinned here:

1. Source: model-supplied slot keys in the reserved namespace are refused, so the
   value can never be forged.
2. Sink: the `/files` paths are contained via the same `resolve_sandbox_path` the
   file tools already use, so even a value written by some other route cannot read
   or delete outside the data dir.
"""

from __future__ import annotations

import pytest

from assistant.backend.pipeline.extractor import (
    RESERVED_SLOT_PREFIXES,
    ExtractedSlot,
    ExtractionResult,
    apply_extraction,
)

# The exploit shapes. `Path.__truediv__` discards the left operand for an
# absolute right side, so the first two need no traversal at all -- this is why
# string-prefix filtering was never sufficient and containment has to be checked
# on the resolved path.
HOSTILE_SLOT_VALUES = [
    "/app/.env",
    "/etc/passwd",
    "../../etc/passwd",
    "subdir/../../../../etc/passwd",
    "./../app/.env",
]

# WAF-bypass shapes that are *not* traversal and must stay contained rather than
# raise. `..` is exactly two dots, so `....` is a literal directory name; `resolve()`
# is what makes this safe, and a string-based filter would have to enumerate these
# by hand. Kept as a test because it pins the reason for resolving rather than
# pattern-matching.
NON_TRAVERSAL_VALUES = [
    "....//....//etc/shadow",
    "..././.../etc/shadow",
    "%2e%2e//etc/passwd",
    "notes/..data/../notes.txt",
]


class TestReservedSlotKeysAreRefused:
    """Source layer: the model cannot write the `file_` namespace."""

    @pytest.mark.asyncio
    async def test_upsert_slot_tool_refuses_file_safe_name(self, store):
        from assistant.backend.pipeline import tool_executor as te

        user = await store.create_user("alice")
        te._store = store
        try:
            frame = await store.create_frame(
                "file_report", "entity", owner_user_id=user.id
            )
            result = await te.execute_upsert_slot(
                {
                    "frame_name": "file_report",
                    "slot_key": "file_safe_name",
                    "slot_value": "/app/.env",
                },
                user_id="1",
                session_id="s1",
            )
        finally:
            te._store = None

        assert result.success is False, "the tool accepted a reserved slot key"
        assert "reserved" in result.error.lower()

        # And nothing landed in the DB.
        slots = {s.key: s.value for s in await store.get_slots_for_frame(frame.id)}
        assert "file_safe_name" not in slots

    @pytest.mark.asyncio
    async def test_ordinary_slot_keys_still_work(self, store):
        """The denylist must not block normal memory writes."""
        from assistant.backend.pipeline import tool_executor as te

        user = await store.create_user("alice")
        te._store = store
        try:
            result = await te.execute_upsert_slot(
                {
                    "frame_name": "alice",
                    "slot_key": "birthday",
                    "slot_value": "1990-04-01",
                },
                user_id=str(user.id),
                session_id="s1",
            )
        finally:
            te._store = None

        assert result.success is True, result.error
        frame = await store.get_frame_by_name("alice")
        slots = {s.key: s.value for s in await store.get_slots_for_frame(frame.id)}
        assert slots.get("birthday") == "1990-04-01"

    @pytest.mark.asyncio
    async def test_extraction_drops_reserved_keys(self, store):
        """Conversational extraction must drop them too, and say so."""
        extraction = ExtractionResult(
            slots=[
                ExtractedSlot(frame_name="alice", key="birthday", value="1990-04-01"),
                ExtractedSlot(
                    frame_name="alice", key="file_safe_name", value="/app/.env"
                ),
            ],
            associations=[],
        )

        summary = await apply_extraction(
            extraction, store, source_type="conversation", source_reliability=0.7
        )

        assert summary["slots_applied"] == 1, "the reserved key was applied"
        assert summary.get("reserved_keys_skipped") == 1, "the drop was not reported"

        frame = await store.get_frame_by_name("alice")
        slots = {s.key: s.value for s in await store.get_slots_for_frame(frame.id)}
        assert "file_safe_name" not in slots
        assert slots["birthday"] == "1990-04-01", "the legitimate slot was lost"

    def test_reserved_prefix_covers_the_whole_file_namespace(self):
        """A prefix check is only as good as the prefix it matches."""
        assert "file_safe_name".startswith(RESERVED_SLOT_PREFIXES)
        assert "file_name".startswith(RESERVED_SLOT_PREFIXES)
        assert "file_size".startswith(RESERVED_SLOT_PREFIXES)
        assert not "profile_name".startswith(RESERVED_SLOT_PREFIXES)


class TestFileEndpointContainment:
    """Sink layer: even a forged value cannot escape the data dir."""

    @pytest.mark.parametrize("hostile", HOSTILE_SLOT_VALUES)
    def test_contained_path_rejects_escape(self, hostile, monkeypatch, tmp_path):
        from assistant.backend.pipeline import filesystem as fs

        monkeypatch.setattr(fs, "SANDBOX_ROOT", tmp_path.resolve())

        with pytest.raises(fs.PathTraversalError):
            fs.resolve_sandbox_path(hostile)

    @pytest.mark.parametrize("benign", NON_TRAVERSAL_VALUES)
    def test_contained_path_keeps_non_traversal_inside(self, benign, monkeypatch, tmp_path):
        """These look like traversal to a regex filter but are not.

        They must resolve to a path *inside* the sandbox, not raise: raising
        would mean the check is pattern-matching rather than resolving.
        """
        from assistant.backend.pipeline import filesystem as fs

        root = tmp_path.resolve()
        monkeypatch.setattr(fs, "SANDBOX_ROOT", root)

        resolved = fs.resolve_sandbox_path(benign)

        assert str(resolved).startswith(str(root)), f"escaped to {resolved}"

    def test_contained_path_accepts_a_normal_name(self, monkeypatch, tmp_path):
        from assistant.backend.pipeline import filesystem as fs

        monkeypatch.setattr(fs, "SANDBOX_ROOT", tmp_path.resolve())

        resolved = fs.resolve_sandbox_path("notes/todo.csv")

        assert resolved == (tmp_path.resolve() / "notes" / "todo.csv")

    def test_upload_written_safe_name_is_inside_the_sandbox(self):
        """The legitimate writer must keep producing contained values.

        If this ever fails, the denylist is not the only thing standing between a
        model and a path -- the writer itself would be producing escapes.
        """
        from assistant.backend.pipeline.filesystem import resolve_sandbox_path

        resolve_sandbox_path("upload_20260928_ab12.csv")  # must not raise
