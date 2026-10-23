"""CLI safety rails for brain operations.

The portable export is the artifact you restore from when a memory write goes
wrong, so it must not be writable — or deletable — by the agent's own file tools.
The first verified export was written under `/app/data`, which is exactly that
sandbox: `delete_file` would have removed the backup.
"""

from pathlib import Path

import pytest

from assistant.backend.config import settings
from assistant.backend.pipeline.filesystem import SANDBOX_ROOT
from assistant.cli.db import export_db, is_inside_sandbox


def test_a_path_inside_the_sandbox_is_rejected():
    assert is_inside_sandbox(SANDBOX_ROOT / "brain-exports" / "brain.assistant-brain")
    assert is_inside_sandbox(str(SANDBOX_ROOT / "x.assistant-brain"))


def test_a_path_outside_the_sandbox_is_allowed():
    assert not is_inside_sandbox(Path("/app/brain/exports/brain.assistant-brain"))
    assert not is_inside_sandbox("/app/brain/x.assistant-brain")


def test_a_parent_traversal_cannot_hide_inside_the_sandbox():
    """`/app/brain/../data/x` resolves into the sandbox and must be refused."""
    assert is_inside_sandbox("/app/brain/../data/x.assistant-brain")


@pytest.mark.asyncio
async def test_plaintext_dump_refuses_an_encrypted_brain(monkeypatch, tmp_path):
    """`db export` cannot read SQLCipher; say so instead of "file is not a database"."""
    monkeypatch.setattr(settings, "db_key", "test-key")
    assert await export_db(tmp_path / "out.sql.gz") is False
    assert not (tmp_path / "out.sql.gz").exists()
