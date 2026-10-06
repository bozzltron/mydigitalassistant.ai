"""`rename_file`: the disk file and its memory frame move together.

Regression: renaming a file had no tool, so the model flailed through
read+write+delete and could not complete it (a live turn asked to rename a
calendar). A rename is a first-class file operation; this pins that the disk file
moves, the ``file_<name>`` frame moves with it, and a clash or an extension change
is refused rather than silently applied.
"""

from __future__ import annotations

import pytest

from assistant.backend.pipeline import filesystem

_SANDBOX_FILES = ("rename_old.txt", "rename_new.txt", "rename_taken.txt")


def _cleanup() -> None:
    for name in _SANDBOX_FILES:
        (filesystem.SANDBOX_ROOT / name).unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_rename_moves_the_file_and_its_frame(store, stub_llm):
    from assistant.backend.pipeline.tool_executor import (
        execute_read_file,
        execute_rename_file,
        execute_write_file,
        init_store,
    )

    init_store(store.db_path, embed_fn=stub_llm.embed, embedding_model="nomic-embed-text")
    user = await store.create_user("rename_user")
    uid = str(user.id)
    try:
        await execute_write_file({"path": "rename_old.txt", "content": "hello"}, uid, "s")
        assert (filesystem.SANDBOX_ROOT / "rename_old.txt").exists()

        result = await execute_rename_file(
            {"path": "rename_old.txt", "new_name": "rename_new.txt"}, uid, "s"
        )
        assert result.success, result.error
        assert not (filesystem.SANDBOX_ROOT / "rename_old.txt").exists()
        assert (filesystem.SANDBOX_ROOT / "rename_new.txt").exists()

        assert await store.get_frame_by_name("file_rename_old.txt") is None
        frame = await store.get_frame_by_name("file_rename_new.txt")
        assert frame is not None
        slots = {s.key: s.value for s in await store.get_slots_for_frame(frame.id)}
        assert slots["file_name"] == "rename_new.txt"
        assert slots["file_safe_name"] == "rename_new.txt"

        read = await execute_read_file({"path": "rename_new.txt"}, uid, "s")
        assert read.success and read.data["content"] == "hello"
    finally:
        _cleanup()


@pytest.mark.asyncio
async def test_rename_refuses_to_overwrite(store, stub_llm):
    from assistant.backend.pipeline.tool_executor import (
        execute_rename_file,
        execute_write_file,
        init_store,
    )

    init_store(store.db_path, embed_fn=stub_llm.embed, embedding_model="nomic-embed-text")
    user = await store.create_user("rename_user")
    uid = str(user.id)
    try:
        await execute_write_file({"path": "rename_old.txt", "content": "a"}, uid, "s")
        await execute_write_file({"path": "rename_taken.txt", "content": "b"}, uid, "s")

        result = await execute_rename_file(
            {"path": "rename_old.txt", "new_name": "rename_taken.txt"}, uid, "s"
        )
        assert not result.success
        assert "already exists" in result.error
        assert (filesystem.SANDBOX_ROOT / "rename_old.txt").exists()
    finally:
        _cleanup()


@pytest.mark.asyncio
async def test_rename_refuses_an_extension_change(store, stub_llm):
    from assistant.backend.pipeline.tool_executor import (
        execute_rename_file,
        execute_write_file,
        init_store,
    )

    init_store(store.db_path, embed_fn=stub_llm.embed, embedding_model="nomic-embed-text")
    user = await store.create_user("rename_user")
    uid = str(user.id)
    try:
        await execute_write_file({"path": "rename_old.txt", "content": "a"}, uid, "s")

        result = await execute_rename_file(
            {"path": "rename_old.txt", "new_name": "rename_new.docx"}, uid, "s"
        )
        assert not result.success
        assert "extension" in result.error
        assert (filesystem.SANDBOX_ROOT / "rename_old.txt").exists()
    finally:
        _cleanup()
