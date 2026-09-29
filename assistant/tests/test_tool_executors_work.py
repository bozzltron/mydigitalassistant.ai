"""Regression: every registered tool must be able to actually succeed.

Four tools were advertised to the model in `builtin_tools()` and could not work.
In each case the executor body raised, and the executor's `except Exception`
turned that into a `ToolResult(success=False)` -- so from the model's side these
looked like transient tool failures to be retried, not broken features. Nothing
surfaced them: the suite passed, and the trace panel showed a normal tool error.

- `search_episodes` imported `consolidate.search_episodes`, which does not exist.
- `fetch_url` imported `assistant.backend.pipeline.fetch`, a module that does not
  exist. The working implementation is `_make_fetch_url_handler` in tools.py.
- `run_scheduled_task` imported `scheduler.run_now`, which that package does not
  export. This is AGENTS.md critical path #2 and had never worked.
- `upsert_association` passed frame *names* to `create_association`, which takes
  int frame ids. With `PRAGMA foreign_keys = ON` every call raised IntegrityError.

Each test below asserts `result.success is True` against real implementations, so
a future rename of the underlying function fails here rather than in production.

Note on how the bugs hid: `execute_tool` dispatches through the same
`except Exception` handler, so a broken import is indistinguishable from a
transient error at the call site. The only reliable detector is asserting success.
"""

from __future__ import annotations

import pytest

from assistant.backend.pipeline import tool_executor as te
from assistant.backend.pipeline.tools import builtin_tools


@pytest.fixture
def wired(store, monkeypatch):
    """Tool executor globals pointed at a real store, embedder, and orchestrator.

    Restores every global afterwards so these tests cannot leak into the rest of
    the suite -- `init_store` mutates module-level state.
    """
    saved = (te._store, te._embed_fn, te._orchestrator, te._search_tool)

    async def _embed(text):
        # Deterministic 1024-dim unit-ish vector; the store only needs the right
        # length for the sqlite-vec insert.
        return [((hash(text) >> i) % 1000) / 1000.0 for i in range(1024)]

    class FakeOrchestrator:
        def __init__(self):
            self.calls: list[tuple] = []
            self.llm_client = object()

        async def run_scheduled_task(self, prompt, user_id, task_name):
            self.calls.append((prompt, user_id, task_name))
            return f"ran {task_name}"

    orch = FakeOrchestrator()
    te._store = store
    te._embed_fn = _embed
    te._orchestrator = orch
    te._search_tool = None
    try:
        yield orch
    finally:
        te._store, te._embed_fn, te._orchestrator, te._search_tool = saved


class TestUpsertAssociation:
    """#7 -- names were passed where the store requires int frame ids."""

    @pytest.mark.asyncio
    async def test_association_by_frame_name_succeeds(self, store, wired):
        alice = await store.create_frame("alice", "person")
        await store.create_frame("bob", "person")

        result = await te.execute_upsert_association(
            {
                "source_frame": "alice",
                "target_frame": "bob",
                "relation_type": "knows",
                "confidence": 0.6,
            },
            user_id="1",
        )

        assert result.success is True, result.error
        edges = [
            (r.from_frame_id, r.to_frame_id, r.relation_type)
            for r in await store.get_all_associations()
        ]
        assert (alice.id, edges[0][1], "knows") in edges

    @pytest.mark.asyncio
    async def test_bidirectional_creates_both_edges(self, store, wired):
        await store.create_frame("alice", "person")
        await store.create_frame("bob", "person")

        result = await te.execute_upsert_association(
            {
                "source_frame": "alice",
                "target_frame": "bob",
                "relation_type": "married_to",
                "bidirectional": True,
            },
            user_id="1",
        )

        assert result.success is True, result.error
        assert len(await store.get_all_associations()) == 2

    @pytest.mark.asyncio
    async def test_unknown_frame_name_is_a_clear_error(self, store, wired):
        await store.create_frame("alice", "person")

        result = await te.execute_upsert_association(
            {"source_frame": "alice", "target_frame": "nobody", "relation_type": "knows"},
            user_id="1",
        )

        assert result.success is False
        # Must name the missing frame, not leak an opaque IntegrityError.
        assert "nobody" in result.error

    @pytest.mark.asyncio
    async def test_self_association_is_rejected(self, store, wired):
        await store.create_frame("alice", "person")

        result = await te.execute_upsert_association(
            {"source_frame": "alice", "target_frame": "alice", "relation_type": "knows"},
            user_id="1",
        )

        assert result.success is False
        assert "same frame" in result.error


class TestRunScheduledTask:
    """#6 -- the tool has never worked; it is AGENTS.md critical path #2."""

    @pytest.mark.asyncio
    async def test_run_now_executes_the_named_task(self, store, wired):
        user = await store.create_user("alice")
        await store.upsert_scheduled_task(
            name="daily briefing",
            description="morning AI news",
            schedule_cron="daily",
            prompt="summarize AI news",
            owner_user_id=user.id,
        )

        result = await te.execute_run_scheduled_task(
            {"task_name": "daily briefing"}, user_id=str(user.id)
        )

        assert result.success is True, result.error
        assert wired.calls == [("summarize AI news", user.id, "daily briefing")]

    @pytest.mark.asyncio
    async def test_run_now_records_last_run(self, store, wired):
        """Without this, 'what did my briefing find' drifts from reality."""
        user = await store.create_user("alice")
        task_id = await store.upsert_scheduled_task(
            name="briefing",
            description="d",
            schedule_cron="daily",
            prompt="p",
            owner_user_id=user.id,
        )

        await te.execute_run_scheduled_task(
            {"task_name": "briefing"}, user_id=str(user.id)
        )

        slots = {s.key: s.value for s in await store.get_slots_for_frame(task_id)}
        assert slots.get("last_run"), "last_run was not recorded"
        assert "ran briefing" in (slots.get("last_result_summary") or "")

    @pytest.mark.asyncio
    async def test_unknown_task_lists_the_available_ones(self, store, wired):
        user = await store.create_user("alice")
        await store.upsert_scheduled_task(
            name="briefing", description="d", schedule_cron="daily",
            prompt="p", owner_user_id=user.id,
        )

        result = await te.execute_run_scheduled_task(
            {"task_name": "nonexistent"}, user_id=str(user.id)
        )

        assert result.success is False
        # The model can only recover from this if it knows what exists.
        assert "briefing" in result.error

    @pytest.mark.asyncio
    async def test_task_is_not_found_without_orchestrator(self, store, wired):
        """A missing orchestrator must be reported, not raise."""
        te._orchestrator = None
        result = await te.execute_run_scheduled_task(
            {"task_name": "briefing"}, user_id="1"
        )
        assert result.success is False
        assert "orchestrator" in result.error.lower()


class TestSearchEpisodes:
    """#6 -- imported a function that does not exist."""

    @pytest.mark.asyncio
    async def test_search_episodes_returns_a_list_not_an_error(self, store, wired):
        user = await store.create_user("alice")
        await store.create_episode(user.id, "old-session", "user", "I love sourdough")

        result = await te.execute_search_episodes(
            {"query": "bread", "max_results": 3}, user_id=str(user.id)
        )

        assert result.success is True, result.error
        assert isinstance(result.data["results"], list)

    @pytest.mark.asyncio
    async def test_empty_query_is_rejected_with_a_reason(self, store, wired):
        result = await te.execute_search_episodes({"query": "  "}, user_id="1")
        assert result.success is False
        assert "query" in result.error

    @pytest.mark.asyncio
    async def test_missing_embedder_degrades_with_a_message(self, store, wired):
        """No embedder must be a clear error, not a crash -- the model can
        answer from the current conversation instead."""
        te._embed_fn = None
        result = await te.execute_search_episodes({"query": "bread"}, user_id="1")
        assert result.success is False
        assert "embed" in result.error.lower()


class TestFetchUrl:
    """#6 -- imported a module that does not exist."""

    @pytest.mark.asyncio
    async def test_fetch_url_rejects_a_missing_url(self, wired):
        result = await te.execute_fetch_url({}, user_id="1")
        assert result.success is False
        assert "url" in result.error

    @pytest.mark.asyncio
    async def test_fetch_url_surfaces_the_handler_error(self, wired, monkeypatch):
        """The handler signals failure by string prefix, not by raising.

        If that is not translated into a failed ToolResult, the model is handed
        a wall of error text as if it were page content.
        """
        from assistant.backend.pipeline import tools as tools_mod

        async def _failing(url: str) -> str:
            return f"Error: {url} is blocked by robots.txt"

        monkeypatch.setattr(tools_mod, "_fetch_single_url", _failing)

        result = await te.execute_fetch_url(
            {"url": "https://example.com", "extract_facts": False}, user_id="1"
        )

        assert result.success is False
        assert "robots.txt" in result.error

    @pytest.mark.asyncio
    async def test_fetch_url_returns_page_content(self, wired, monkeypatch):
        from assistant.backend.pipeline import tools as tools_mod

        async def _page(url: str) -> str:
            return "Sourdough needs a live starter."

        monkeypatch.setattr(tools_mod, "_fetch_single_url", _page)

        result = await te.execute_fetch_url(
            {"url": "https://example.com", "extract_facts": False}, user_id="1"
        )

        assert result.success is True, result.error
        assert "Sourdough" in result.data["content"]


class TestRegistrationMatchesReality:
    """Guard: a registered tool with no working executor is the failure mode.

    This is the structural check behind the four bugs above. It does not prove a
    tool works (only the per-tool tests do), but it does catch the case where a
    tool is advertised without an executor at all, and it keeps the timeout table
    and the registry in agreement.
    """

    def test_every_advertised_tool_has_a_registered_executor(self):
        te._register_builtin_tools()
        advertised = {t["function"]["name"] for t in builtin_tools()}
        registered = set(te.TOOL_REGISTRY)

        assert advertised <= registered, (
            f"advertised but not executable: {sorted(advertised - registered)}"
        )

    def test_every_registered_tool_has_a_timeout(self):
        te._register_builtin_tools()
        missing = [
            n for n, entry in te.TOOL_REGISTRY.items() if not entry.get("timeout")
        ]
        assert not missing, f"no timeout configured: {missing}"


class TestRaggedCsvIngestion:
    """P3 -- a ragged CSV aborted the whole row loop.

    The ingest used ``zip(headers, row, strict=True)``; a short row raised,
    which orphaned the row frames already written for that file (no ``part_of``
    association to the parent) and skipped ``row_count``/``columns`` entirely.
    Ragged rows are ordinary in hand-edited exports.
    """

    @pytest.fixture
    def sandbox(self, tmp_path, monkeypatch):
        from assistant.backend.pipeline import filesystem

        monkeypatch.setattr(filesystem, "SANDBOX_ROOT", tmp_path.resolve())
        (tmp_path / "notes").mkdir(exist_ok=True)
        return tmp_path

    @pytest.mark.asyncio
    async def test_short_and_long_rows_are_stored_not_aborted(
        self, store, wired, sandbox
    ):
        user = await store.create_user("alice")
        content = "name,age,city\nalice,30\nbob,40,london,extra\n"

        result = await te.execute_tool(
            "write_file",
            {"path": "ragged.csv", "content": content},
            str(user.id),
            "sess",
        )

        assert result.success is True, result.error
        rows = {
            f.name: f
            for f in await store.list_frames()
            if f.name.startswith("file_ragged.csv_row_")
        }
        assert set(rows) == {"file_ragged.csv_row_1", "file_ragged.csv_row_2"}

        first = {
            s.key: s.value
            for s in await store.get_slots_for_frame(rows["file_ragged.csv_row_1"].id)
        }
        second = {
            s.key: s.value
            for s in await store.get_slots_for_frame(rows["file_ragged.csv_row_2"].id)
        }
        # Short row padded with "", long row's extra cell ignored.
        assert first == {"name": "alice", "age": "30", "city": ""}
        assert second == {"name": "bob", "age": "40", "city": "london"}

    @pytest.mark.asyncio
    async def test_blank_header_column_gets_a_positional_key(
        self, store, wired, sandbox
    ):
        user = await store.create_user("alice")
        content = "name,,notes\nx,ignored,hello\n"

        result = await te.execute_tool(
            "write_file",
            {"path": "blank_col.csv", "content": content},
            str(user.id),
            "sess",
        )

        assert result.success is True, result.error
        row = next(
            f for f in await store.list_frames()
            if f.name == "file_blank_col.csv_row_1"
        )
        slots = {s.key: s.value for s in await store.get_slots_for_frame(row.id)}
        # An empty header must still get a stable key, not "" (which the
        # reserved-prefix guard and slot uniqueness both trip over).
        assert slots.get("col_1") == "ignored"
        assert slots.get("notes") == "hello"
