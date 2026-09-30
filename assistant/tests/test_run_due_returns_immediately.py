"""Regression: the manual task-run endpoint must not run tasks inside the request.

`POST /tasks/run-due` executed every due task serially, through the LLM, inside the
HTTP request. A full daily list is eleven tasks; measured against the live backend
on 2026-09-30, that outlasted Caddy's `response_header_timeout 300s` and returned
**504 at 300.013s** — while the tasks completed successfully anyway. The response
was misleading in both directions: it reported failure for work that succeeded, and
it held a connection open for five minutes. It also duplicated the runner's
execution path, so the two could drift.

The fix: the endpoint queues the due check and returns immediately, and the
scheduler loop executes the tasks through the one shared path (`_execute_task`,
via `execute_and_record_task`) that records last_run/next_run, the daily-run frame,
the output episode, and any agent alert.

These tests pin the contract:
  1. the endpoint returns without executing any task;
  2. it reports the tasks it queued;
  3. it says so honestly when the loop is not running (rather than claiming a
     hand-off that will never happen);
  4. `wake_scheduler()` cuts a poll wait short, which is what makes (1) safe.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from assistant.backend.config import settings
from assistant.backend.main import _state, app, get_orchestrator, get_store
from assistant.backend.memory.retrieval import Retriever
from assistant.backend.pipeline.orchestrator import Orchestrator, OrchestratorDeps


@pytest.fixture
def run_due_client(store, stub_llm, stub_search):
    """TestClient with test dependencies wired in (mirrors test_api.py)."""
    retriever = Retriever(store=store, llm_client=stub_llm)
    orchestrator = Orchestrator(
        deps=OrchestratorDeps(
            store=store,
            retriever=retriever,
            llm_client=stub_llm,
            search_tool=stub_search,
        )
    )
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_orchestrator] = lambda: orchestrator

    _state["store"] = store
    _state["llm_client"] = stub_llm
    _state["retriever"] = retriever
    _state["orchestrator"] = orchestrator
    _state["search_tool"] = stub_search

    original_db_path = settings.database_path
    original_scheduler = settings.scheduler_enabled
    settings.database_path = store.db_path
    settings.scheduler_enabled = False
    try:
        with TestClient(app) as c:
            _state["llm_client"] = stub_llm
            _state["search_tool"] = stub_search
            yield c
    finally:
        settings.database_path = original_db_path
        settings.scheduler_enabled = original_scheduler
        app.dependency_overrides.clear()
        _state.clear()


async def _make_due_task(store, name: str) -> None:
    user = await store.create_user("alice")
    await store.upsert_scheduled_task(
        name=name,
        description="",
        schedule_cron="daily",
        prompt="report on things",
        owner_user_id=user.id,
        next_run="2020-01-01T00:00:00+00:00",
    )


class TestEndpointDoesNotExecuteInline:
    """The core regression: no task runs while the request is open."""

    def test_due_task_is_queued_not_executed(self, run_due_client, monkeypatch, store):
        from assistant.backend.scheduler import runner as runner_mod

        monkeypatch.setattr(runner_mod, "_LOOP_RUNNING", True)
        executed: list[str] = []

        async def _fake_execute(**kwargs):
            executed.append(kwargs.get("task_name"))
            return True, "done"

        monkeypatch.setattr(runner_mod, "_execute_task", _fake_execute, raising=False)
        asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
            _make_due_task(store, "test_task")
        )

        resp = run_due_client.post("/tasks/run-due")
        assert resp.status_code == 200
        body = resp.json()

        assert body["status"] == "queued"
        assert "test_task" in body["tasks_queued"]
        # The decisive assertion: nothing ran during the request.
        assert executed == [], "the endpoint executed a task inline"

    def test_reports_when_scheduler_is_not_running(
        self, run_due_client, monkeypatch, store
    ):
        """SCHEDULER_ENABLED=false is the live configuration. The endpoint must
        not claim it queued work that nothing will pick up."""
        from assistant.backend.scheduler import runner as runner_mod

        monkeypatch.setattr(runner_mod, "_LOOP_RUNNING", False)
        asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
            _make_due_task(store, "test_task2")
        )

        resp = run_due_client.post("/tasks/run-due")
        body = resp.json()
        assert body["status"] == "scheduler_not_running"
        assert body["tasks_queued"] == []
        assert "test_task2" in body["tasks_due"]

    def test_nothing_due_is_reported(self, run_due_client):
        resp = run_due_client.post("/tasks/run-due")
        assert resp.status_code == 200
        assert resp.json()["status"] == "nothing_due"


class TestWakeMechanism:
    """`wake_scheduler` is what lets the endpoint return safely."""

    def test_wake_returns_false_when_loop_down(self, monkeypatch):
        from assistant.backend.scheduler import runner as runner_mod

        monkeypatch.setattr(runner_mod, "_LOOP_RUNNING", False)
        assert runner_mod.wake_scheduler() is False

    def test_wake_returns_true_and_sets_flag(self, monkeypatch):
        from assistant.backend.scheduler import runner as runner_mod

        monkeypatch.setattr(runner_mod, "_LOOP_RUNNING", True)
        monkeypatch.setattr(runner_mod, "_WAKE", False)
        assert runner_mod.wake_scheduler() is True
        assert runner_mod._WAKE is True

    @pytest.mark.asyncio
    async def test_wait_for_poll_returns_early_on_wake(self, monkeypatch):
        """A wake cuts the poll short, so a queued run starts promptly instead of
        waiting out the 20s interval."""
        from assistant.backend.scheduler import runner as runner_mod

        monkeypatch.setattr(runner_mod, "_WAKE", False)
        state = {"wake": False}

        async def _wake_soon():
            await asyncio.sleep(0.05)
            runner_mod.__dict__["_WAKE"] = True
            state["wake"] = True

        asyncio.create_task(_wake_soon())
        loop = asyncio.get_running_loop()
        start = loop.time()
        await runner_mod._wait_for_poll(seconds=10)
        elapsed = loop.time() - start

        assert state["wake"], "the wake task did not run"
        assert elapsed < 1.0, f"wake did not cut the wait short ({elapsed:.2f}s)"
        # And the flag is cleared so the next poll waits normally.
        assert runner_mod._WAKE is False

    @pytest.mark.asyncio
    async def test_wait_for_poll_times_out_normally(self, monkeypatch):
        from assistant.backend.scheduler import runner as runner_mod

        monkeypatch.setattr(runner_mod, "_WAKE", False)
        loop = asyncio.get_running_loop()
        start = loop.time()
        await runner_mod._wait_for_poll(seconds=0.1)
        assert loop.time() - start >= 0.09
