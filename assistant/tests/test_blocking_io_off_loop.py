"""Regression: blocking work must not run on the event loop.

Two endpoints do genuinely blocking C work behind an `async def`:

- `POST /db/backup` and `POST /db/restore` call SQLite's backup API, which copies
  the whole (encrypted) database. On a large brain that is seconds to minutes.
- `transcribe_audio` runs faster-whisper, which is minutes of CPU-bound decoding.

Awaiting either directly froze every other in-flight request for the duration.
The tests below run each and assert the blocking call happened on a worker
thread, which is the observable contract of `asyncio.to_thread`.
"""

from __future__ import annotations

import threading

import pytest

import assistant.backend.main as main
import assistant.backend.pipeline.whisper as whisper
from assistant.backend.memory.store import MemoryStore


@pytest.mark.asyncio
async def test_db_backup_copies_off_the_event_loop(store: MemoryStore, monkeypatch):
    seen: dict[str, threading.Thread] = {}
    real_connect = main.connect

    def recording_connect(path: str):
        seen["thread"] = threading.current_thread()
        return real_connect(path)

    monkeypatch.setattr(main, "connect", recording_connect)

    await main.db_backup(store=store)

    assert seen["thread"] is not threading.main_thread()


@pytest.mark.asyncio
async def test_transcription_decodes_off_the_event_loop(monkeypatch, tmp_path):
    seen: dict[str, threading.Thread] = {}

    class FakeModel:
        def transcribe(self, path: str, **kwargs):  # noqa: ANN003, ANN201
            seen["thread"] = threading.current_thread()
            return iter([]), None

    monkeypatch.setattr(whisper, "_load_model", lambda: FakeModel())

    await whisper.transcribe_audio(tmp_path / "clip.webm")

    assert seen["thread"] is not threading.main_thread()
