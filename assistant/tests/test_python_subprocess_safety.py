"""Regression: the Python subprocess must not leak the backend's secrets.

`OllamaClient._execute_python_sandboxed` (now `_execute_python_subprocess`) ran
model-authored Python with `env={**os.environ}`. In the deployed container
`docker-compose.yml` places `DB_KEY` -- the SQLCipher key for the user's entire
brain -- and `BRAVE_API_KEY` in that environment, so a `compute` call could read
and print them. The function was named and documented as a sandbox ("no network,
limited imports") while enforcing neither; its `allowed_imports` block is a
prefix that adds names and removes nothing.

It is also reachable with the raw user message at `orchestrator.py:863` / `:1930`
and with model-chosen code via the `compute` tool.

These tests pin the two properties the function genuinely provides -- a scrubbed
environment and a timeout that kills the whole process group -- so the docstring's
claims stay true. They deliberately do NOT assert "no network": that is not
enforced, and claiming it in a test would be the same lie in a new place.
"""

from __future__ import annotations

import asyncio

import pytest

from assistant.backend.pipeline.llm_client import OllamaClient


@pytest.fixture
def client() -> OllamaClient:
    """A client with no math model: the subprocess method is model-independent."""
    return OllamaClient(
        chat_model="unused",
        utility_model="unused",
        embedding_model="unused",
        math_model="",
    )


class TestEnvironmentIsScrubbed:
    @pytest.mark.asyncio
    async def test_db_key_is_not_visible_to_subprocess_code(self, client, monkeypatch):
        """The brain key must not be readable by model-authored code."""
        monkeypatch.setenv("DB_KEY", "super-secret-brain-key")
        monkeypatch.setenv("BRAVE_API_KEY", "brave-secret")

        out = await client._execute_python_subprocess(
            "import os; print('DB_KEY=' + os.environ.get('DB_KEY', '<absent>'))", 10
        )

        assert "super-secret-brain-key" not in out, (
            f"the database key leaked into the subprocess: {out!r}"
        )
        assert "DB_KEY=<absent>" in out

    @pytest.mark.asyncio
    async def test_brave_key_is_not_visible(self, client, monkeypatch):
        monkeypatch.setenv("BRAVE_API_KEY", "brave-secret")

        out = await client._execute_python_subprocess(
            "import os; print(os.environ.get('BRAVE_API_KEY', '<absent>'))", 10
        )

        assert "brave-secret" not in out
        assert "<absent>" in out

    @pytest.mark.asyncio
    async def test_arbitrary_env_is_not_inherited(self, client, monkeypatch):
        """A scrubbed env is the invariant; listing individual keys is not enough."""
        monkeypatch.setenv("SOME_FUTURE_SECRET", "leak-me")

        out = await client._execute_python_subprocess(
            "import os; print(sorted(os.environ))", 10
        )

        assert "SOME_FUTURE_SECRET" not in out

    @pytest.mark.asyncio
    async def test_code_still_runs_with_a_minimal_env(self, client):
        """Scrubbing must not break the tool it exists to serve."""
        out = await client._execute_python_subprocess("print(6 * 7)", 10)
        assert "42" in out


class TestTimeoutKillsTheTree:
    @pytest.mark.asyncio
    async def test_timeout_returns_an_error_string(self, client):
        out = await client._execute_python_subprocess(
            "import time; time.sleep(30)", 1
        )
        assert "timed out" in out.lower()

    @pytest.mark.asyncio
    async def test_grandchild_does_not_survive_the_timeout(self, client, tmp_path):
        """`subprocess.run`'s timeout killed only the direct child.

        Code that forks survived and kept running. The fix starts the child in
        its own process group and kills the group, so a grandchild dies too.
        """
        marker = tmp_path / "grandchild_alive"

        # Child spawns a grandchild that would outlive a naive timeout, then
        # sleeps so the parent is killed while the grandchild is mid-life.
        code = (
            "import subprocess, sys, time\n"
            f"subprocess.Popen([sys.executable, '-c', "
            f"\"import time; time.sleep(8); open({str(marker)!r}, 'w').write('x')\"])\n"
            "time.sleep(30)\n"
        )

        out = await client._execute_python_subprocess(code, 1)
        assert "timed out" in out.lower()

        # Give the grandchild time to write its marker if it survived.
        await asyncio.sleep(9)
        assert not marker.exists(), (
            "a grandchild outlived the timeout; the process group was not killed"
        )


class TestNoFalseSandboxClaim:
    """The name and docstring must not assert containment that is not provided."""

    def test_method_is_no_longer_called_sandboxed(self):
        assert not hasattr(
            OllamaClient, "_execute_python_sandboxed"
        ), "the misleading name is back"

    def test_docstring_says_not_a_sandbox(self):
        doc = OllamaClient._execute_python_subprocess.__doc__ or ""
        assert "not a security sandbox" in doc.lower()
        # It should say what *is* enforced, too.
        assert "environment" in doc.lower()
        assert "process group" in doc.lower() or "kill" in doc.lower()

    @pytest.mark.asyncio
    async def test_network_is_reachable_which_is_why_the_claim_mattered(
        self, client
    ):
        """Pin the truth: sockets work, so 'no network' would be a false claim.

        If a real sandbox is ever added, this test should be inverted -- and that
        inversion is the signal that the docstring can be strengthened.
        """
        out = await client._execute_python_subprocess(
            "import socket; s = socket.socket(); print('socket-ok'); s.close()", 10
        )
        assert (
            "socket-ok" in out
        ), f"network became unreachable; update the docstring: {out!r}"
