"""Precise editing: address by line, refuse ambiguity.

Phase 6 of the file-editing contract in `docs/FILES.md`. Read hands the model a
line-numbered view (`[lines 201-250 of 628]`); editing accepts the same handle.
The old `edit_file` was anchor-only and defaulted to `replace_all=true`, so a
short `old_text` silently rewrote every occurrence — the opposite of precision.
These pin the two ways to say *where*, the compare-and-swap anchor, and the
memory refresh an edit now performs.
"""

from __future__ import annotations

import json

import pytest
import pytest_asyncio

CLEANUP = (
    "edit_lines.txt",
    "edit_anchor.txt",
    "edit_ambiguous.txt",
    "edit_csv.csv",
)


@pytest.fixture(autouse=True)
def _cleanup_sandbox_files():
    yield
    from assistant.backend.pipeline import filesystem

    for name in CLEANUP:
        try:
            (filesystem.SANDBOX_ROOT / name).unlink()
        except FileNotFoundError:
            pass


@pytest_asyncio.fixture(autouse=True)
async def _wire_tool_executor_store(store):
    """`edit_file` refreshes memory through a module-global store."""
    from assistant.backend.pipeline import tool_executor as te

    await store.create_user("test_user")
    te._store = store
    yield
    te._store = None


async def _write(path: str, content: str) -> None:
    from assistant.backend.pipeline.tool_executor import execute_write_file

    result = await execute_write_file({"path": path, "content": content}, "1", "s")
    assert result.success, result.error


async def _read(path: str) -> str:
    from assistant.backend.pipeline.tool_executor import execute_read_file

    result = await execute_read_file({"path": path}, "1", "s")
    assert result.success, result.error
    return result.data["content"]


class TestLineAddressedEdit:
    @pytest.mark.asyncio
    async def test_replaces_exactly_the_named_range(self):
        from assistant.backend.pipeline.tool_executor import execute_edit_file

        await _write("edit_lines.txt", "a\nb\nc\nd")

        result = await execute_edit_file(
            {"path": "edit_lines.txt", "start_line": 2, "end_line": 3, "new_text": "B\nC"},
            "1", "s",
        )
        assert result.success, result.error
        assert result.data["mode"] == "lines"
        assert result.data["replaced_lines"] == [2, 3]
        assert await _read("edit_lines.txt") == "a\nB\nC\nd"

    @pytest.mark.asyncio
    async def test_matching_anchor_is_verified_and_succeeds(self):
        """old_text + a line range is a compare-and-swap that agrees."""
        from assistant.backend.pipeline.tool_executor import execute_edit_file

        await _write("edit_lines.txt", "one\ntwo\nthree")

        result = await execute_edit_file(
            {
                "path": "edit_lines.txt",
                "start_line": 2,
                "end_line": 2,
                "old_text": "two",
                "new_text": "TWO",
            },
            "1", "s",
        )
        assert result.success, result.error
        assert await _read("edit_lines.txt") == "one\nTWO\nthree"

    @pytest.mark.asyncio
    async def test_stale_line_range_is_refused_not_guessed(self):
        """A line number whose content changed must fail, not edit the wrong line."""
        from assistant.backend.pipeline.tool_executor import execute_edit_file

        await _write("edit_lines.txt", "one\ntwo\nthree")

        result = await execute_edit_file(
            {
                "path": "edit_lines.txt",
                "start_line": 2,
                "end_line": 2,
                "old_text": "WRONG",
                "new_text": "X",
            },
            "1", "s",
        )
        assert not result.success
        assert "do not match old_text" in result.error
        assert "two" in result.error  # the actual lines, so the model can correct
        assert await _read("edit_lines.txt") == "one\ntwo\nthree"  # unchanged

    @pytest.mark.asyncio
    async def test_empty_new_text_deletes_the_range(self):
        from assistant.backend.pipeline.tool_executor import execute_edit_file

        await _write("edit_lines.txt", "a\nb\nc\nd")

        result = await execute_edit_file(
            {"path": "edit_lines.txt", "start_line": 2, "end_line": 3, "new_text": ""},
            "1", "s",
        )
        assert result.success, result.error
        assert await _read("edit_lines.txt") == "a\nd"

    @pytest.mark.asyncio
    async def test_out_of_range_lines_are_refused(self):
        from assistant.backend.pipeline.tool_executor import execute_edit_file

        await _write("edit_lines.txt", "a\nb")

        result = await execute_edit_file(
            {"path": "edit_lines.txt", "start_line": 2, "end_line": 99, "new_text": "x"},
            "1", "s",
        )
        assert not result.success
        assert "past the end" in result.error


class TestAnchorAmbiguity:
    @pytest.mark.asyncio
    async def test_ambiguous_anchor_is_refused_with_line_numbers(self):
        from assistant.backend.pipeline.tool_executor import execute_edit_file

        await _write("edit_ambiguous.txt", "alpha\nbar\nbeta\nbar\ngamma")

        result = await execute_edit_file(
            {"path": "edit_ambiguous.txt", "old_text": "bar", "new_text": "BAR"},
            "1", "s",
        )
        assert not result.success
        assert "matches 2 regions" in result.error
        assert "2" in result.error and "4" in result.error  # the lines of each match
        assert await _read("edit_ambiguous.txt") == "alpha\nbar\nbeta\nbar\ngamma"

    @pytest.mark.asyncio
    async def test_replace_all_true_still_changes_every_occurrence(self):
        from assistant.backend.pipeline.tool_executor import execute_edit_file

        await _write("edit_ambiguous.txt", "alpha\nbar\nbeta\nbar\ngamma")

        result = await execute_edit_file(
            {
                "path": "edit_ambiguous.txt",
                "old_text": "bar",
                "new_text": "BAR",
                "replace_all": True,
            },
            "1", "s",
        )
        assert result.success, result.error
        assert result.data["changes"] == 2
        assert await _read("edit_ambiguous.txt") == "alpha\nBAR\nbeta\nBAR\ngamma"

    @pytest.mark.asyncio
    async def test_unique_anchor_replaces_once_by_default(self):
        from assistant.backend.pipeline.tool_executor import execute_edit_file

        await _write("edit_anchor.txt", "hello world")

        result = await execute_edit_file(
            {"path": "edit_anchor.txt", "old_text": "world", "new_text": "there"},
            "1", "s",
        )
        assert result.success, result.error
        assert result.data["mode"] == "text"
        assert await _read("edit_anchor.txt") == "hello there"

    @pytest.mark.asyncio
    async def test_no_addressing_is_refused(self):
        from assistant.backend.pipeline.tool_executor import execute_edit_file

        await _write("edit_anchor.txt", "hello")

        result = await execute_edit_file({"path": "edit_anchor.txt"}, "1", "s")
        assert not result.success
        assert "nothing to edit" in result.error


class TestEditRefreshesMemory:
    @pytest.mark.asyncio
    async def test_edit_updates_the_file_profile(self, store):
        """The Phase 5 profile must not go stale after an edit."""
        from assistant.backend.pipeline.tool_executor import execute_edit_file

        await _write(
            "edit_csv.csv",
            "email,state\na@x.com,TX\nb@x.com,TX\nc@x.com,CA",
        )

        frame = await store.get_frame_by_name("file_edit_csv.csv")
        assert frame is not None

        # Add a fourth data row by replacing the last line with two lines.
        result = await execute_edit_file(
            {
                "path": "edit_csv.csv",
                "start_line": 4,
                "end_line": 4,
                "new_text": "c@x.com,CA\nd@x.com,CA",
            },
            "1", "s",
        )
        assert result.success, result.error

        slots = {s.key: s.value for s in await store.get_slots_for_frame(frame.id)}
        profile = json.loads(slots["file_profile"])
        assert profile["rows"] == 4
        # TX 2, CA 2 — recomputed from the edited bytes, not the pre-edit shape.
        assert profile["categorical"]["state"] == {"CA": 2, "TX": 2}
