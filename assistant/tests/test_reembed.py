"""`assistant db reembed` — the recovery command for an embedding-model change.

It did not work, and it reported that it had.

`reembed_db` passed `llm_client.embed` as the `embed_fn`. That callable returns an
`EmbeddingResponse`, not a `list[float]`, so `json.dumps` raised inside
`store_frame_embedding` for every single frame. `embed_frames` caught each failure
with a bare `except: continue`, so the batch "succeeded", the counter advanced by
`len(batch)`, and the command printed

    ✓ Re-embedded 2408/2408 frames. Metadata updated: embedding_model = qwen3-embedding:0.6b

having written nothing. That is the state the brain was found in: 1895 live frames,
1 with a usable vector, and a metadata key claiming the model was current.

Two things had to be true for this to be invisible, and both are now pinned here:
the callable has to be unwrapped, and a failed embed has to be counted.
"""

import pytest

from assistant.backend.db.schema import init_db
from assistant.backend.memory.metadata import (
    METADATA_KEY_EMBEDDING_MODEL,
    get_metadata,
    set_metadata,
)
from assistant.backend.memory.store import MemoryStore
from assistant.backend.pipeline.llm_client import EmbeddingResponse

MODEL = "qwen3-embedding:0.6b"
STALE = "nomic-embed-text"


async def _store(tmp_path) -> MemoryStore:
    db_path = str(tmp_path / "reembed.db")
    await init_db(db_path)
    return MemoryStore(db_path)


async def _frames(store: MemoryStore, n: int) -> list[int]:
    ids = []
    for i in range(n):
        f = await store.create_frame(f"frame_{i}", "entity")
        await store.upsert_slot(f.id, "detail", str(i))
        ids.append(f.id)
    return ids


async def test_an_embedding_response_is_not_a_usable_embed_fn(tmp_path):
    """The shape mismatch, stated as the store sees it.

    `OllamaClient.embed` is the obvious thing to hand `embed_frames` and it is
    wrong. The pydantic model is not JSON-serializable, so the write fails.
    """
    store = await _store(tmp_path)
    frame_ids = await _frames(store, 1)

    async def wrong_embed_fn(text: str) -> EmbeddingResponse:
        return EmbeddingResponse(embedding=[0.1] * 768, model=MODEL)

    # The old signature returned None and raised nothing.
    stored = await store.embed_frames(frame_ids, wrong_embed_fn, MODEL)

    assert stored == 0
    assert await store.get_frame_embedding(frame_ids[0], MODEL) is None


async def test_embed_frames_reports_how_many_it_actually_stored(tmp_path, caplog):
    """A partly-failing batch must be countable.

    Without a return value the caller cannot tell "worked" from "silently did
    nothing", which is precisely how reembed_db claimed success.
    """
    store = await _store(tmp_path)
    frame_ids = await _frames(store, 3)

    calls = {"n": 0}

    async def flaky_embed_fn(text: str) -> list[float]:
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("ollama not answering")
        return [0.1] * 768

    with caplog.at_level("WARNING", logger="assistant.backend.memory.store"):
        stored = await store.embed_frames(frame_ids, flaky_embed_fn, MODEL)

    assert stored == 2
    assert "2 of 3" in caplog.text
    assert "ollama not answering" in caplog.text


async def test_embed_frames_warns_when_nothing_at_all_succeeds(tmp_path, caplog):
    """The all-or-nothing case deserves its own loud line, not a log of 100 rows."""
    store = await _store(tmp_path)
    frame_ids = await _frames(store, 4)

    async def broken_embed_fn(text: str) -> list[float]:
        raise RuntimeError("connection refused")

    with caplog.at_level("WARNING", logger="assistant.backend.memory.store"):
        stored = await store.embed_frames(frame_ids, broken_embed_fn, MODEL)

    assert stored == 0
    assert "0 of 4" in caplog.text
    # The first few failures are shown; the rest are counted, not dumped.
    assert "(+1 more)" in caplog.text


async def test_reembed_db_reports_failure_and_leaves_metadata_alone(
    tmp_path, monkeypatch, capsys
):
    """End to end: a re-embed that stores nothing must not claim success.

    The metadata key is the trap. It is the thing the startup audit trusts, so
    advancing it past a failed re-embed is what makes the failure permanent and
    invisible.
    """
    from assistant.cli import db as db_cli

    store = await _store(tmp_path)
    await _frames(store, 3)
    db_path = str(tmp_path / "reembed.db")
    await set_metadata(db_path, METADATA_KEY_EMBEDDING_MODEL, STALE)

    monkeypatch.setattr(db_cli.settings, "database_path", db_path)
    monkeypatch.setattr(db_cli.settings, "embedding_model", MODEL)

    class BrokenClient:
        def __init__(self, **kwargs):
            pass

        async def embed(self, text: str) -> EmbeddingResponse:
            # Right type, unusable content: json.dumps still gets a real list here,
            # so force the failure the way a broken Ollama would.
            raise RuntimeError("connection refused")

    monkeypatch.setattr(
        "assistant.backend.pipeline.llm_client.OllamaClient", BrokenClient
    )

    ok = await db_cli.reembed_db()

    assert ok is False
    out = capsys.readouterr().out
    assert "0/3 frames were embedded" in out
    # Metadata still points at the model whose vectors are actually stored.
    assert await get_metadata(db_path, METADATA_KEY_EMBEDDING_MODEL) == STALE


async def test_reembed_db_advances_metadata_only_after_a_real_reembed(
    tmp_path, monkeypatch, capsys
):
    """The happy path still works, and still writes the metadata."""
    from assistant.cli import db as db_cli

    store = await _store(tmp_path)
    frame_ids = await _frames(store, 2)
    db_path = str(tmp_path / "reembed.db")
    await set_metadata(db_path, METADATA_KEY_EMBEDDING_MODEL, STALE)

    monkeypatch.setattr(db_cli.settings, "database_path", db_path)
    monkeypatch.setattr(db_cli.settings, "embedding_model", MODEL)

    class WorkingClient:
        def __init__(self, **kwargs):
            pass

        async def embed(self, text: str) -> EmbeddingResponse:
            return EmbeddingResponse(embedding=[0.1] * 1024, model=MODEL)

    monkeypatch.setattr(
        "assistant.backend.pipeline.llm_client.OllamaClient", WorkingClient
    )

    ok = await db_cli.reembed_db()

    assert ok is True
    assert "2/2" in capsys.readouterr().out
    assert await get_metadata(db_path, METADATA_KEY_EMBEDDING_MODEL) == MODEL
    for fid in frame_ids:
        assert await store.get_frame_embedding(fid, MODEL) == [0.1] * 1024


@pytest.mark.parametrize("target", ["nomic-embed-text", "mxbai-embed-large"])
async def test_reembed_target_model_argument_is_honoured(
    tmp_path, monkeypatch, target
):
    """A `--model` argument reaches the client, not just the log line.

    `reembed_db` has two model sources -- the argument and the setting -- and they
    have to agree, because the metadata key is written from the same variable the
    vectors were produced with.
    """
    from assistant.cli import db as db_cli

    await _store(tmp_path)
    db_path = str(tmp_path / "reembed.db")
    monkeypatch.setattr(db_cli.settings, "database_path", db_path)
    monkeypatch.setattr(db_cli.settings, "embedding_model", "the-configured-one")
    seen = {}

    class Client:
        def __init__(self, **kwargs):
            seen.update(kwargs)

        async def embed(self, text: str) -> EmbeddingResponse:
            return EmbeddingResponse(embedding=[0.1] * 1024, model=target)

    monkeypatch.setattr("assistant.backend.pipeline.llm_client.OllamaClient", Client)
    await db_cli.reembed_db(target_model=target)
    assert seen.get("embedding_model") == target
