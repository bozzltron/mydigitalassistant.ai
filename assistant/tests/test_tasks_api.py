"""The tasks API: list, create, update, delete — and who is allowed to.

A task is a `scheduled_task` frame; the page is a view over memory, and every write
goes through the store methods chat already uses. These tests pin the three things the
endpoints got wrong:

- `GET /tasks` had `user_id: int | None = None`, so leaving the parameter off returned
  **every** task in the install, system ones included.
- `DELETE /tasks/{id}` took no `user_id` at all: any caller could soft-delete any task
  frame.
- `GET /tasks/{id}/result` scanned with `include_system=True`, so it would return the
  result of a task the caller does not own. It has since been **removed** rather than
  fixed: the list carries `last_result_summary` on every row, so the endpoint had no
  consumer left (the review found only the test that pinned its ownership).

Plus the two the store had (delete-then-recreate, and two accounts sharing a name),
which a Create button is what makes reachable.
"""

import pytest
from fastapi.testclient import TestClient

from assistant.backend.config import settings
from assistant.backend.main import _state, app, get_orchestrator, get_store
from assistant.backend.memory.retrieval import Retriever
from assistant.backend.pipeline.orchestrator import Orchestrator, OrchestratorDeps
from assistant.tests.conftest import SignedInClient


@pytest.fixture
async def client(store, stub_llm, stub_search):
    """FastAPI TestClient acting as one household account (mirrors test_maps.py)."""
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

    user = await store.create_user("tasker")

    original_db_path = settings.database_path
    original_scheduler = settings.scheduler_enabled
    settings.database_path = store.db_path
    settings.scheduler_enabled = False
    try:
        with TestClient(app) as c:
            _state["llm_client"] = stub_llm
            _state["search_tool"] = stub_search
            yield SignedInClient(c, user.id)
    finally:
        settings.database_path = original_db_path
        settings.scheduler_enabled = original_scheduler
        app.dependency_overrides.clear()
        _state.clear()


class TestListRequiresAnAccount:
    def test_without_a_user_id_it_is_refused(self, client):
        """`user_id` is required, not defaulted to "everything".

        Regression: the default was `None`, and the store reads `None` as "no owner
        filter" — so a caller who forgot the parameter got every task in the install,
        system ones included.
        """
        assert client.raw.get("/tasks/list").status_code == 422

    def test_it_lists_the_callers_tasks(self, client):
        client.post("/tasks/create", json={"name": "briefing", "prompt": "do the thing"})
        listed = client.get("/tasks/list").json()
        assert [t["name"] for t in listed] == ["briefing"]

    def test_an_empty_list_is_an_empty_list(self, client):
        assert client.get("/tasks/list").json() == []


class TestCreate:
    def test_creates_a_daily_task(self, client):
        r = client.post("/tasks/create", json={"name": "briefing", "prompt": "summarise AI news"})
        assert r.status_code == 200
        task = r.json()
        assert task["name"] == "briefing"
        assert task["prompt"] == "summarise AI news"
        # A task made in the UI is a standing daily task; `once` stays conversational.
        assert task["schedule_cron"] == "daily"
        assert task["enabled"] is True
        assert task["next_run"], "a new enabled task must have a next run"

    def test_creating_the_same_name_twice_updates_rather_than_duplicating(self, client):
        client.post("/tasks/create", json={"name": "briefing", "prompt": "v1"})
        client.post("/tasks/create", json={"name": "briefing", "prompt": "v2"})
        listed = client.get("/tasks/list").json()
        assert [t["prompt"] for t in listed] == ["v2"]

    def test_frequency_cannot_be_set_from_the_page(self, client):
        """`extra="forbid"` — a client asking for `once` is told, not silently obeyed."""
        r = client.post("/tasks/create", json={"name": "x", "prompt": "y", "frequency": "once"})
        assert r.status_code == 422

    def test_a_blank_prompt_is_refused(self, client):
        assert client.post("/tasks/create", json={"name": "x", "prompt": "   "}).status_code == 422
        assert client.post("/tasks/create", json={"name": "  ", "prompt": "y"}).status_code == 422

    def test_delete_then_recreate_the_same_name(self, client):
        """The first thing a user does on a page with a Create button.

        Regression: `delete_scheduled_task` soft-deletes and `frames.name` is UNIQUE,
        so re-creating raised `UNIQUE constraint failed: frames.name` — a 500 on the
        obvious path.
        """
        created = client.post("/tasks/create", json={"name": "briefing", "prompt": "v1"}).json()
        assert client.delete(f"/tasks/{created['id']}").status_code == 200

        again = client.post("/tasks/create", json={"name": "briefing", "prompt": "v2"})
        assert again.status_code == 200
        assert again.json()["prompt"] == "v2"
        assert [t["name"] for t in client.get("/tasks/list").json()] == ["briefing"]


class TestUpdate:
    def test_edits_the_prompt(self, client):
        created = client.post("/tasks/create", json={"name": "briefing", "prompt": "old"}).json()
        r = client.patch(f"/tasks/{created['id']}", json={"prompt": "new"})
        assert r.status_code == 200
        assert r.json()["prompt"] == "new"
        # Only the prompt moved.
        assert r.json()["name"] == "briefing"
        assert r.json()["schedule_cron"] == "daily"

    def test_pause_clears_next_run_and_resume_sets_it(self, client):
        created = client.post("/tasks/create", json={"name": "briefing", "prompt": "x"}).json()

        paused = client.patch(f"/tasks/{created['id']}", json={"enabled": False}).json()
        assert paused["enabled"] is False
        assert paused["next_run"] is None, "a paused task has no next run"

        resumed = client.patch(f"/tasks/{created['id']}", json={"enabled": True}).json()
        assert resumed["enabled"] is True
        assert resumed["next_run"], "resuming schedules the next tick, not the missed one"

    def test_only_prompt_and_enabled_are_writable(self, client):
        """The write surface is two fields, and the contract says so out loud."""
        created = client.post("/tasks/create", json={"name": "briefing", "prompt": "x"}).json()
        for body in (
            {"name": "renamed"},
            {"frequency": "once"},
            {"description": "nope"},
        ):
            r = client.patch(f"/tasks/{created['id']}", json=body)
            assert r.status_code == 422, body

        assert client.patch(f"/tasks/{created['id']}", json={}).status_code == 422

    def test_a_blank_prompt_is_refused(self, client):
        created = client.post("/tasks/create", json={"name": "briefing", "prompt": "x"}).json()
        assert client.patch(f"/tasks/{created['id']}", json={"prompt": "  "}).status_code == 422


class TestOwnership:
    """A task belonging to another account cannot be read, changed or deleted.

    Regression: `DELETE /tasks/{id}` took no `user_id` at all, and `GET
    /tasks/{id}/result` scanned with `include_system=True` before it was removed as
    unconsumed.
    """

    @pytest.fixture
    async def other(self, client, store):
        """A second account with its own task."""
        them = await store.create_user("someone-else")
        their_client = SignedInClient(client.raw, them.id)
        created = their_client.post("/tasks/create", json={"name": "theirs", "prompt": "secret"})
        assert created.status_code == 200
        return their_client, created.json()["id"]

    async def test_it_is_not_listed(self, client, other):
        _theirs, _task_id = other
        assert client.get("/tasks/list").json() == []

    async def test_it_cannot_be_edited(self, client, other):
        theirs, task_id = other
        assert client.patch(f"/tasks/{task_id}", json={"prompt": "hijacked"}).status_code == 404
        # And the prompt is untouched.
        assert theirs.get("/tasks/list").json()[0]["prompt"] == "secret"

    async def test_it_cannot_be_deleted(self, client, other):
        theirs, task_id = other
        assert client.delete(f"/tasks/{task_id}").status_code == 404
        assert [t["name"] for t in theirs.get("/tasks/list").json()] == ["theirs"]

    async def test_two_accounts_can_each_own_a_task_of_the_same_name(self, client, store):
        them = await store.create_user("second")
        their_client = SignedInClient(client.raw, them.id)
        client.post("/tasks/create", json={"name": "morning", "prompt": "hers"})
        their_client.post("/tasks/create", json={"name": "morning", "prompt": "his"})

        assert [t["prompt"] for t in client.get("/tasks/list").json()] == ["hers"]
        assert [t["prompt"] for t in their_client.get("/tasks/list").json()] == ["his"]
        # Each sees the name they gave it, not an internal handle.
        assert client.get("/tasks/list").json()[0]["name"] == "morning"
        assert their_client.get("/tasks/list").json()[0]["name"] == "morning"
