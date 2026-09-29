"""Regression: thumbs up/down must actually reinforce or weaken memory.

The feedback pipeline documented in AGENTS.md ("Positive: bump_confidence ...
Negative: lower_confidence") had never run from the web UI. Three defects lined up
to make it a silent no-op:

1. `MessageList.tsx` called `postFeedback(null, ...)` -- hardcoded null.
2. `apply_positive_feedback` / `apply_negative_feedback` return 0 immediately
   when the id is falsy, before doing any work.
3. `POST /feedback` reported `{"status":"ok"}` unconditionally.

So every reaction wrote a `feedback` audit row, changed zero confidences, and
looked like it worked. The reaction button is the only user-facing way to tell
the agent a fact was right or wrong; with this broken the agent never learns from
correction-by-reaction at all.

These tests drive the real endpoint and assert the *effect on confidence*, not the
status string, so a future refactor that keeps returning "ok" cannot hide the bug
again.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from assistant.backend.config import settings
from assistant.backend.main import _state, app, get_orchestrator, get_store
from assistant.backend.memory.retrieval import Retriever
from assistant.backend.pipeline.orchestrator import Orchestrator, OrchestratorDeps

from .conftest import StubLLMClient


@pytest.fixture
async def client(store):
    """TestClient with hermetic LLM/search deps, mirroring test_api.py."""
    from assistant.backend.pipeline.search import WebSearchTool

    llm = StubLLMClient()
    search = WebSearchTool(base_url="http://127.0.0.1:1", enabled=False)
    retriever = Retriever(store=store, llm_client=llm)
    orchestrator = Orchestrator(
        deps=OrchestratorDeps(
            store=store, retriever=retriever, llm_client=llm, search_tool=search
        )
    )

    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_orchestrator] = lambda: orchestrator
    _state.update(
        {
            "store": store,
            "llm_client": llm,
            "retriever": retriever,
            "orchestrator": orchestrator,
            "search_tool": search,
        }
    )

    original_db_path = settings.database_path
    original_scheduler = settings.scheduler_enabled
    settings.database_path = store.db_path
    settings.scheduler_enabled = False
    try:
        with TestClient(app) as c:
            _state["llm_client"] = llm
            _state["search_tool"] = search
            yield c
    finally:
        settings.database_path = original_db_path
        settings.scheduler_enabled = original_scheduler
        app.dependency_overrides.clear()
        _state.clear()


async def _session_that_touched_memory(store, user_id: int) -> str:
    """A session with a turn that actually wrote frames, as a real chat turn does.

    `apply_*_feedback` deliberately targets the most recent turn with a non-empty
    `frame_ids`, so the fixture has to look like one.
    """
    session_id = "feedback-session"
    frame = await store.create_frame("alice", "person", owner_user_id=user_id)
    await store.upsert_slot(frame.id, "birthday", "1990-04-01")

    ep = await store.create_episode(user_id, session_id, "assistant", "Got it.")
    await store.update_episode_frame_ids(ep.id, [frame.id])
    return session_id


@pytest.mark.asyncio
async def test_positive_feedback_raises_confidence(client, store):
    user = await store.create_user("alice")
    session_id = await _session_that_touched_memory(store, user.id)

    frame = await store.get_frame_by_name("alice")
    before = {s.key: s.confidence for s in await store.get_slots_for_frame(frame.id)}

    r = client.post(
        "/feedback",
        json={
            "episode_id": session_id,
            "message_id": "m-1",
            "kind": "positive",
            "comment": None,
        },
    )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["slots_updated"] > 0, f"no confidence changed: {body}"
    assert body["status"] == "ok"

    after = {s.key: s.confidence for s in await store.get_slots_for_frame(frame.id)}
    assert after["birthday"] > before["birthday"], (
        f"confidence did not rise: {before} -> {after}"
    )


@pytest.mark.asyncio
async def test_negative_feedback_lowers_confidence(client, store):
    user = await store.create_user("alice")
    session_id = await _session_that_touched_memory(store, user.id)

    frame = await store.get_frame_by_name("alice")
    # `lower_confidence` floors at INITIAL_CONFIDENCE (0.5), so a brand-new slot
    # cannot move -- that floor is the documented rule in confidence.py, not the
    # bug under test. Reinforce through a repeat observation (which is what
    # actually raises confidence in production) so there is room to fall.
    await store.upsert_slot(frame.id, "birthday", "1990-04-01")
    before = {s.key: s.confidence for s in await store.get_slots_for_frame(frame.id)}
    raised = before["birthday"]
    assert raised > 0.5, f"fixture did not reinforce: {raised}"

    r = client.post(
        "/feedback",
        json={
            "episode_id": session_id,
            "message_id": "m-1",
            "kind": "negative",
            "comment": None,
        },
    )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["slots_updated"] > 0, f"no confidence changed: {body}"

    after = {s.key: s.confidence for s in await store.get_slots_for_frame(frame.id)}
    assert after["birthday"] < raised, (
        f"confidence did not fall: {raised} -> {after['birthday']}"
    )


@pytest.mark.asyncio
async def test_negative_feedback_respects_the_initial_confidence_floor(store):
    """A fresh 0.5 slot must not be pushed below the floor.

    Pins the documented rule so a future 'let dislikes go lower' change is a
    deliberate decision rather than an accident.
    """
    from assistant.backend.memory.confidence import lower_confidence

    assert lower_confidence(0.5) == 0.5


@pytest.mark.asyncio
async def test_feedback_without_a_session_reports_honestly(client, store):
    """The null-episode case the UI used to send.

    The row is still recorded (it is the audit trail) but the status must not
    claim the reinforcement happened, or this bug is invisible forever.
    """
    r = client.post(
        "/feedback",
        json={"episode_id": None, "message_id": "m-1", "kind": "positive"},
    )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["slots_updated"] == 0
    assert body["status"] != "ok", (
        "reported success while updating nothing -- this is the original bug"
    )
    assert body["feedback"] is not None, "the audit row must still be written"


@pytest.mark.asyncio
async def test_correction_kind_records_without_touching_confidence(client, store):
    """Corrections route through /correction; recording must not move confidence."""
    user = await store.create_user("alice")
    session_id = await _session_that_touched_memory(store, user.id)
    frame = await store.get_frame_by_name("alice")
    before = {s.key: s.confidence for s in await store.get_slots_for_frame(frame.id)}

    r = client.post(
        "/feedback",
        json={
            "episode_id": session_id,
            "message_id": "m-1",
            "kind": "correction",
            "comment": "it's 1991-04-01",
        },
    )

    assert r.status_code == 200, r.text
    assert r.json()["slots_updated"] == 0

    after = {s.key: s.confidence for s in await store.get_slots_for_frame(frame.id)}
    assert after == before, "a recorded correction changed confidence"


@pytest.mark.asyncio
async def test_invalid_kind_is_rejected(client):
    r = client.post(
        "/feedback",
        json={"episode_id": "s", "message_id": "m", "kind": "love_it"},
    )
    # kind is a validated enum, so an unknown value is a request-validation
    # failure (422), not a reachable 400 branch.
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_store_returns_zero_rather_than_raising_on_a_missing_session(store):
    """The store-level contract the endpoint now depends on."""
    assert await store.apply_positive_feedback(None) == 0
    assert await store.apply_negative_feedback("no-such-session") == 0
