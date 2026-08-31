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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])