"""`append_file` and `search_file`: build and query a file without reading it.

Phase 4 of plans/2026-10-07-large-file-context.md. The rejected `add_rows` was
CSV-centric; these two are format-agnostic. `append_file` adds a line without
pulling the file into the window, and `search_file` answers "is this already
here?" by matching lines. Dedup is composition — search, then append only what is
missing — with no format assumption.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

CLEANUP = (
    "phase4_contacts.csv",
    "phase4_events.jsonl",
    "phase4_notes.md",
    "phase4_report.docx",
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
    """`append_file`/`search_file` reach memory through a module-global store.

    A user row is created because `apply_file_to_memory` writes the file frame
    with `owner_user_id`, and that is a foreign key.
    """
    from assistant.backend.pipeline import tool_executor as te

    await store.create_user("test_user")
    te._store = store
    yield
    te._store = None


def _path(name: str):
    from assistant.backend.pipeline import filesystem

    filesystem.SANDBOX_ROOT.mkdir(parents=True, exist_ok=True)
    return filesystem.SANDBOX_ROOT / name


class TestAppendFile:
    @pytest.mark.asyncio
    async def test_append_creates_then_grows_without_reading(self):
        from assistant.backend.pipeline.tool_executor import execute_append_file

        first = await execute_append_file(
            {"path": "phase4_contacts.csv", "content": "ada@x.com,Ada"},
            user_id="1",
            session_id="s",
        )
        assert first.success, first.error
        assert first.data["created"] is True

        second = await execute_append_file(
            {"path": "phase4_contacts.csv", "content": "grace@x.com,Grace"},
            user_id="1",
            session_id="s",
        )
        assert second.success, second.error
        assert second.data["created"] is False

        assert _path("phase4_contacts.csv").read_text() == (
            "ada@x.com,Ada\ngrace@x.com,Grace"
        )

    @pytest.mark.asyncio
    async def test_a_missing_trailing_newline_gets_a_separator(self):
        from assistant.backend.pipeline.tool_executor import execute_append_file

        _path("phase4_notes.md").write_text("# Notes")  # no trailing newline
        result = await execute_append_file(
            {"path": "phase4_notes.md", "content": "- a line"},
            user_id="1",
            session_id="s",
        )
        assert result.success, result.error
        assert _path("phase4_notes.md").read_text() == "# Notes\n- a line"

    @pytest.mark.asyncio
    async def test_a_binary_document_is_refused(self):
        from assistant.backend.pipeline.tool_executor import execute_append_file

        result = await execute_append_file(
            {"path": "phase4_report.docx", "content": "text"},
            user_id="1",
            session_id="s",
        )
        assert result.success is False
        assert "binary document" in result.error


class TestSearchFile:
    @pytest.mark.asyncio
    async def test_membership_finds_a_line_by_number(self):
        from assistant.backend.pipeline.tool_executor import (
            execute_append_file,
            execute_search_file,
        )

        for row in ("ada@x.com,Ada", "grace@x.com,Grace", "alan@x.com,Alan"):
            await execute_append_file(
                {"path": "phase4_contacts.csv", "content": row},
                user_id="1",
                session_id="s",
            )

        found = await execute_search_file(
            {"path": "phase4_contacts.csv", "query": "grace@x.com"},
            user_id="1",
            session_id="s",
        )
        assert found.success, found.error
        assert found.data["total_matches"] == 1
        assert found.data["matches"][0]["line"] == 2
        assert "grace@x.com" in found.data["matches"][0]["text"]

    @pytest.mark.asyncio
    async def test_absence_is_a_clean_zero_not_an_error(self):
        from assistant.backend.pipeline.tool_executor import (
            execute_append_file,
            execute_search_file,
        )

        await execute_append_file(
            {"path": "phase4_contacts.csv", "content": "ada@x.com,Ada"},
            user_id="1",
            session_id="s",
        )
        result = await execute_search_file(
            {"path": "phase4_contacts.csv", "query": "nobody@x.com"},
            user_id="1",
            session_id="s",
        )
        assert result.success, result.error
        assert result.data["total_matches"] == 0
        assert result.data["matches"] == []

    @pytest.mark.asyncio
    async def test_case_insensitive_by_default_and_sensitive_on_request(self):
        from assistant.backend.pipeline.tool_executor import (
            execute_append_file,
            execute_search_file,
        )

        await execute_append_file(
            {"path": "phase4_events.jsonl", "content": '{"name": "Ada"}'},
            user_id="1",
            session_id="s",
        )
        loose = await execute_search_file(
            {"path": "phase4_events.jsonl", "query": "ada"},
            user_id="1",
            session_id="s",
        )
        assert loose.data["total_matches"] == 1

        strict = await execute_search_file(
            {"path": "phase4_events.jsonl", "query": "ada", "case_sensitive": True},
            user_id="1",
            session_id="s",
        )
        assert strict.data["total_matches"] == 0

    @pytest.mark.asyncio
    async def test_regex_matches(self):
        from assistant.backend.pipeline.tool_executor import (
            execute_append_file,
            execute_search_file,
        )

        await execute_append_file(
            {"path": "phase4_events.jsonl", "content": "user_42 joined"},
            user_id="1",
            session_id="s",
        )
        result = await execute_search_file(
            {"path": "phase4_events.jsonl", "query": r"user_\d+", "regex": True},
            user_id="1",
            session_id="s",
        )
        assert result.success, result.error
        assert result.data["total_matches"] == 1

    @pytest.mark.asyncio
    async def test_a_capped_search_says_so(self):
        from assistant.backend.pipeline.tool_executor import (
            execute_append_file,
            execute_search_file,
        )

        await execute_append_file(
            {
                "path": "phase4_contacts.csv",
                "content": "\n".join(f"x{i}@x.com,X" for i in range(5)),
            },
            user_id="1",
            session_id="s",
        )
        result = await execute_search_file(
            {"path": "phase4_contacts.csv", "query": "@x.com", "max_matches": 2},
            user_id="1",
            session_id="s",
        )
        assert result.data["match_count"] == 2
        assert result.data["total_matches"] == 5
        assert result.data["truncated"] is True
        assert "note" in result.data

    @pytest.mark.asyncio
    async def test_invalid_regex_is_reported(self):
        from assistant.backend.pipeline.tool_executor import (
            execute_append_file,
            execute_search_file,
        )

        await execute_append_file(
            {"path": "phase4_contacts.csv", "content": "ada@x.com"},
            user_id="1",
            session_id="s",
        )
        result = await execute_search_file(
            {"path": "phase4_contacts.csv", "query": "(", "regex": True},
            user_id="1",
            session_id="s",
        )
        assert result.success is False
        assert "Invalid regex" in result.error


class TestDedupIsComposition:
    @pytest.mark.asyncio
    async def test_search_then_append_adds_only_what_is_missing(self):
        from assistant.backend.pipeline.tool_executor import (
            execute_append_file,
            execute_search_file,
        )

        await execute_append_file(
            {"path": "phase4_contacts.csv", "content": "ada@x.com,Ada"},
            user_id="1",
            session_id="s",
        )

        for candidate in ("ada@x.com,Ada", "grace@x.com,Grace"):
            found = await execute_search_file(
                {"path": "phase4_contacts.csv", "query": candidate.split(",")[0]},
                user_id="1",
                session_id="s",
            )
            if found.data["total_matches"] == 0:
                await execute_append_file(
                    {"path": "phase4_contacts.csv", "content": candidate},
                    user_id="1",
                    session_id="s",
                )

        assert _path("phase4_contacts.csv").read_text() == (
            "ada@x.com,Ada\ngrace@x.com,Grace"
        )
