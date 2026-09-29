"""Regression: skip_route turns must not crash on the search path.

`Orchestrator.chat(skip_route=True)` sets `classification = None` because the
router is deliberately skipped, but the search branch dereferenced
`classification.search_query` unconditionally. The router's `wants_search` veto
cannot save it: that check explicitly requires `classification is not None`, so on
a skip_route turn the veto never fires and the dereference is reached.

Four `_handle_scheduled_task` fallbacks rewrite `request.message` to an
agent-authored string and re-enter `chat(skip_route=True)`. Those strings match no
`_NON_INFO_PATTERNS`, so `classify_intent` always marks them `search_needed=True` --
meaning the crash is reachable from "delete my briefing", a typo'd `run_now`, and
any unparseable scheduled-task phrasing. That is AGENTS.md's critical path #2, and
it had zero coverage: `grep skip_route assistant/tests/` was empty.

These tests pin behaviour, not implementation: a skip_route turn whose plan wants a
search must produce a response, not raise.
"""

from __future__ import annotations

import pytest

from assistant.backend.memory.retrieval import Retriever
from assistant.backend.pipeline.orchestrator import (
    ChatRequest,
    Orchestrator,
    OrchestratorDeps,
)
from assistant.backend.pipeline.reasoner import Action, MemorySufficiency, Plan
from assistant.backend.pipeline.search import SearchInfo

from .conftest import StubLLMClient


class SearchSpy:
    """Records the exact query string it is handed; returns no results."""

    def __init__(self) -> None:
        self.queries: list[str] = []
        self.enabled = False
        self.backend_name = "test"
        self.max_results_for_extraction = 3

    async def search(self, query: str, num_results: int = 5):
        self.queries.append(query)
        return []

    async def search_with_info(
        self, query: str, num_results: int = 5, llm_client=None, user_consent=False
    ):
        self.queries.append(query)
        return [], SearchInfo(backend="test", query=query, results=[])

    def close(self):
        pass


class _AlwaysSearchStub(StubLLMClient):
    """Stub LLM whose answer is fixed; search intent is forced by the plan below."""

    async def chat(self, messages, **kwargs):
        return await super().chat(messages, **kwargs)


# The exact strings the four `_handle_scheduled_task` fallbacks write into
# request.message before re-entering chat(skip_route=True).
SKIP_ROUTE_FALLBACK_MESSAGES = [
    "I couldn't understand the task details. Try phrasing it like: 'set up a daily "
    "briefing on AI news at 9am' or 'list my scheduled tasks'.",
    "I couldn't find a task named 'foo'.",
    "Which task do you want to run now?",
    "I'm not sure what to do with that. Try something like 'set up a daily AI news "
    "briefing' or 'list my scheduled tasks'.",
]


@pytest.mark.parametrize("message", SKIP_ROUTE_FALLBACK_MESSAGES)
def test_skip_route_fallback_messages_are_marked_search_needed(message):
    """Pin the precondition: these strings really do trigger a search.

    If this test ever starts failing, the crash has become unreachable and the fix
    in `chat()` is defensive-only -- worth knowing before someone re-narrows it.
    """
    from assistant.backend.memory.retrieval import MemoryContext
    from assistant.backend.pipeline.reasoner import classify_intent

    ctx = MemoryContext(
        query="", retrieved_frames=[], recent_episodes=[], formatted=""
    )
    plan = classify_intent(query=message, task_type="functional", memory=ctx)
    assert plan.search_needed is True, (
        f"fallback message no longer triggers search: {message[:60]!r}"
    )


def _force_search_plan(monkeypatch) -> None:
    """Make the reasoner always plan a search, so the crash line is reached."""
    from assistant.backend.pipeline import orchestrator as orch_mod

    def _plan(query, task_type, memory):
        return Plan(
            action=Action.SEARCH,
            search_needed=True,
            sufficiency=MemorySufficiency.NONE,
        )

    monkeypatch.setattr(orch_mod, "classify_intent", _plan)


async def _build(store, spy, monkeypatch, message: str):
    """Build an orchestrator whose reasoner always plans a search.

    Creates the owning user first: `episodes.user_id` is a foreign key, so a turn
    for a nonexistent user fails on episode logging before ever reaching search.
    """
    _force_search_plan(monkeypatch)
    await store.create_user("tester")
    llm = _AlwaysSearchStub()
    orchestrator = Orchestrator(
        deps=OrchestratorDeps(
            store=store,
            retriever=Retriever(store, llm),
            llm_client=llm,
            search_tool=spy,
        )
    )
    return orchestrator, ChatRequest(message=message, session_id="s-1", user_id=1)


@pytest.mark.asyncio
@pytest.mark.parametrize("message", SKIP_ROUTE_FALLBACK_MESSAGES)
async def test_chat_skip_route_does_not_crash_when_search_planned(
    store, monkeypatch, message
):
    """The crash itself: `chat(skip_route=True)` must not dereference None.

    Before the fix this raised
    `AttributeError: 'NoneType' object has no attribute 'search_query'`.
    """
    spy = SearchSpy()
    orchestrator, request = await _build(store, spy, monkeypatch, message)

    response = await orchestrator.chat(request, skip_route=True)

    assert response.response, "expected a response, got an empty answer"
    # The search branch must actually have been entered, or the test proves nothing.
    assert spy.queries, "search branch never ran; the test is not exercising it"
    for q in spy.queries:
        assert isinstance(q, str) and q.strip(), f"query was not a usable string: {q!r}"


@pytest.mark.asyncio
async def test_skip_route_search_query_falls_back_to_sanitized_message(
    store, monkeypatch
):
    """With no classification, the query must fall back to the sanitized message.

    This is the specific contract the fix establishes: `skip_route` turns search on
    the *user's* (sanitized) text, never on a router field that does not exist.
    """
    spy = SearchSpy()
    message = "I couldn't find a task named 'foo'."
    orchestrator, request = await _build(store, spy, monkeypatch, message)

    await orchestrator.chat(request, skip_route=True)

    assert spy.queries, "search never ran"
    assert spy.queries[0].strip(), "fallback query was empty"


@pytest.mark.asyncio
async def test_chat_stream_skip_route_does_not_crash(store, monkeypatch):
    """`chat_stream` carries the identical dereference and needs the same fix."""
    spy = SearchSpy()
    _force_search_plan(monkeypatch)
    await store.create_user("streamer")
    llm = _AlwaysSearchStub()
    orchestrator = Orchestrator(
        deps=OrchestratorDeps(
            store=store,
            retriever=Retriever(store, llm),
            llm_client=llm,
            search_tool=spy,
        )
    )
    request = ChatRequest(
        message="I couldn't find a task named 'foo'.", session_id="s-2", user_id=1
    )

    events = []
    async for event in orchestrator.chat_stream(request, skip_route=True):
        events.append(event)

    assert events, "stream produced no events"
    assert spy.queries, "search branch never ran on the streaming path"
