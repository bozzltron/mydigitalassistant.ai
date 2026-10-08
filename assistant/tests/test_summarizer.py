"""Tests for background conversation summarization."""

import pytest

from assistant.backend.memory.store import MemoryStore
from assistant.backend.pipeline.llm_client import OllamaClient
from assistant.backend.scheduler.summarizer import Summarizer


class StubLLMClient(OllamaClient):
    """Deterministic stub for utility model summarization."""

    def __init__(self):
        super().__init__()
        self._next_summary = None
        self.last_user_prompt = ""

    def set_summary(self, summary: dict):
        self._next_summary = summary

    async def chat(
        self,
        messages,
        model=None,
        temperature=0.7,
        format=None,
        stream=False,
        think=None,
        num_predict=None,
        num_ctx=None,
        tools=None,
    ):
        from assistant.backend.pipeline.llm_client import ChatResponse
        for m in messages:
            if getattr(m, "role", None) == "user":
                self.last_user_prompt = m.content
        if self._next_summary:
            import json
            return ChatResponse(
                content=json.dumps(self._next_summary),
                model="test",
                done=True,
            )
        return ChatResponse(
            content='{"summary": "test", "key_entities": [], "open_questions": []}',
            model="test",
            done=True,
        )


class EmbeddingStubLLMClient(StubLLMClient):
    """Stub whose `embed_one` returns a bare vector, matching real usage.

    `embed` returns an `EmbeddingResponse`, exactly as `OllamaClient` does. The
    summarizer used to hand `embed` to `embed_frames`, which expected `list[float]`
    and raised inside `json.dumps`; `embed_frames` swallowed it per frame, so the
    summary frame was created with no embedding and only a warning was logged.
    """

    async def embed_one(self, text: str) -> list[float]:
        return [0.1] * 768

    async def embed(self, text, model=None):
        from assistant.backend.pipeline.llm_client import EmbeddingResponse

        return EmbeddingResponse(embedding=[0.1] * 768, model=model or "stub")


@pytest.fixture
async def store(tmp_path):
    from assistant.backend.db.schema import init_db
    from assistant.backend.memory.store import MemoryStore
    db_path = str(tmp_path / "test.db")
    await init_db(db_path)
    return MemoryStore(db_path)


@pytest.mark.asyncio
async def test_summarizer_creates_frame(store, tmp_path):
    """Summarizer creates a new summary frame for a session with enough turns."""
    from assistant.backend.config import settings

    # Override settings for test
    settings.summarization_min_turns = 3
    settings.summarization_max_chars = 4000
    settings.summarization_timeout_seconds = 60

    user = await store.create_user("testuser")
    session_id = "test_session_123"

    # Add enough episodes
    await store.create_episode(
        user.id, session_id, "user", "Hello, I want to research festivals", frame_ids=[]
    )
    await store.create_episode(
        user.id, session_id, "assistant", "Sure, let me help", frame_ids=[]
    )
    await store.create_episode(
        user.id, session_id, "user", "Focus on Texas indie festivals", frame_ids=[]
    )
    await store.create_episode(
        user.id, session_id, "assistant", "Found several options", frame_ids=[]
    )
    await store.create_episode(
        user.id, session_id, "user", "Let's write a bio", frame_ids=[]
    )

    # Setup stub LLM
    llm = StubLLMClient()
    llm.set_summary({
        "summary": "User researched Texas indie festivals and started bio draft",
        "key_entities": ["Texas", "festivals", "indie"],
        "open_questions": ["Which festivals have open applications?"]
    })

    summarizer = Summarizer(
        store=MemoryStore(str(tmp_path / "test.db")), llm_client=StubLLMClient()
    )
    # Need to use the same store
    summarizer.store = store
    summarizer.llm_client = StubLLMClient()
    summarizer.llm_client.set_summary({
        "summary": "User researched Texas indie festivals and started bio draft",
        "key_entities": ["Texas", "festivals", "indie"],
        "open_questions": ["Which festivals have open applications?"]
    })

    result = await summarizer.summarize_session(session_id, user.id)

    assert result is not None
    assert result.created is True
    assert result.turn_count == 5
    assert "Texas" in result.summary
    assert "festivals" in result.key_entities


@pytest.mark.asyncio
async def test_summarizer_updates_existing_frame(store, tmp_path):
    """Summarizer updates existing summary frame instead of creating duplicate."""
    from assistant.backend.config import settings

    settings.summarization_min_turns = 3

    user = await store.create_user("testuser2")
    session_id = "update_test"

    # Add episodes
    for i in range(5):
        await store.create_episode(user.id, session_id, "user", f"Message {i}", frame_ids=[])

    # First summarization
    summarizer = Summarizer(store=store, llm_client=StubLLMClient())
    summarizer.llm_client.set_summary({
        "summary": "First summary",
        "key_entities": ["test"],
        "open_questions": []
    })

    result1 = await summarizer.summarize_session(session_id, user.id)
    assert result1 is not None
    assert result1.created is True

    # Add more episodes
    for i in range(3):
        await store.create_episode(user.id, session_id, "user", f"More {i}", frame_ids=[])

    # Second summarization (should update)
    summarizer.llm_client.set_summary({
        "summary": "Updated summary with more content",
        "key_entities": ["test", "more"],
        "open_questions": []
    })

    result2 = await summarizer.summarize_session(session_id, user.id)
    assert result2 is not None
    assert result2.created is False
    assert "Updated summary" in result2.summary


@pytest.mark.asyncio
async def test_summarizer_skips_insufficient_turns(store, tmp_path):
    """Summarizer skips sessions with insufficient turns."""
    from assistant.backend.config import settings

    settings.summarization_min_turns = 10

    user = await store.create_user("testuser3")
    session_id = "short_session"

    # Only 3 turns - below minimum
    await store.create_episode(user.id, session_id, "user", "Hi", frame_ids=[])
    await store.create_episode(user.id, session_id, "assistant", "Hello", frame_ids=[])
    await store.create_episode(user.id, session_id, "user", "Bye", frame_ids=[])

    summarizer = Summarizer(store=store, llm_client=StubLLMClient())

    result = await summarizer.summarize_session(session_id, user.id)

    assert result is None


@pytest.mark.asyncio
async def test_summarizer_stores_embedding_for_summary_frame(store):
    """Regression: a summary frame must actually get its embedding stored.

    Before the fix the summarizer passed `llm_client.embed` (which returns an
    `EmbeddingResponse`) where `embed_frames` wanted `list[float]`. `json.dumps`
    rejected it for every frame, the failure was swallowed, and the frame was
    left unindexed — the "stored 0 of 1" warning in the live logs.
    """
    from assistant.backend.config import settings

    settings.summarization_min_turns = 3

    user = await store.create_user("embeduser")
    session_id = "embed_session"
    for i in range(4):
        await store.create_episode(user.id, session_id, "user", f"Message {i}", frame_ids=[])

    summarizer = Summarizer(store=store, llm_client=EmbeddingStubLLMClient())
    summarizer.llm_client.set_summary(
        {"summary": "A summary", "key_entities": [], "open_questions": []}
    )

    result = await summarizer.summarize_session(session_id, user.id)
    assert result is not None

    frame = await store.get_frame_by_name(f"conversation_summary_{session_id}")
    assert frame is not None
    chunks = await store.count_frame_embedding_chunks(frame.id, settings.embedding_model)
    assert chunks >= 1, "summary frame was created without an embedding"


def test_recent_episodes_text_keeps_the_tail_not_the_head():
    """A long session is summarized from its recent turns, not its opening.

    The old `_format_episodes(...)[:max_chars]` on an oldest-first list dropped
    the tail -- so a long conversation's summary described its first turns and
    never advanced.
    """
    from types import SimpleNamespace

    from assistant.backend.config import settings
    from assistant.backend.scheduler.summarizer import Summarizer

    original = settings.summarization_max_chars
    settings.summarization_max_chars = 60
    try:
        episodes = [
            SimpleNamespace(role="user", content=f"turn {i} " + "x" * 20)
            for i in range(20)
        ]
        text = Summarizer(store=None, llm_client=None)._recent_episodes_text(episodes)
    finally:
        settings.summarization_max_chars = original

    assert "turn 19" in text  # the most recent turn is kept
    assert "turn 0 " not in text  # the opening is dropped to fit


@pytest.mark.asyncio
async def test_second_summary_folds_in_the_prior_summary(store):
    """Summarization is incremental: the prior summary is carried forward."""
    from assistant.backend.config import settings

    settings.summarization_min_turns = 3

    user = await store.create_user("incuser")
    session_id = "incremental_session"
    for i in range(4):
        await store.create_episode(user.id, session_id, "user", f"Message {i}", frame_ids=[])

    llm = StubLLMClient()
    llm.set_summary({"summary": "FIRST SUMMARY", "key_entities": [], "open_questions": []})
    summarizer = Summarizer(store=store, llm_client=llm)
    await summarizer.summarize_session(session_id, user.id)

    llm.set_summary({"summary": "SECOND SUMMARY", "key_entities": [], "open_questions": []})
    await summarizer.summarize_session(session_id, user.id)

    assert "FIRST SUMMARY" in llm.last_user_prompt


@pytest.mark.asyncio
async def test_existing_summary_reads_the_stored_prose(store):
    from assistant.backend.config import settings

    settings.summarization_min_turns = 3

    user = await store.create_user("readuser")
    session_id = "read_session"
    for i in range(4):
        await store.create_episode(user.id, session_id, "user", f"Message {i}", frame_ids=[])

    summarizer = Summarizer(store=store, llm_client=StubLLMClient())
    summarizer.llm_client.set_summary(
        {"summary": "STORED PROSE", "key_entities": [], "open_questions": []}
    )
    await summarizer.summarize_session(session_id, user.id)

    assert await summarizer._existing_summary(session_id) == "STORED PROSE"
    assert await summarizer._existing_summary("no_such_session") is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])