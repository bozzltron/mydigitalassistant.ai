"""Tests for the M5 tool framework: registry, handlers, and the tool loop."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from assistant.backend.pipeline.llm_client import ChatResponse, ToolCall
from assistant.backend.pipeline.search import WebSearchTool
from assistant.backend.pipeline.tools import (
    _make_fetch_url_handler,
    builtin_tools,
    run_tool_loop,
)


class FakeToolLLM:
    """Scripted chat client: yields queued responses in order."""

    def __init__(self, responses: list[ChatResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    async def chat(self, messages, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        return self.responses.pop(0)

    async def close(self):
        pass


class EnabledSearch:
    enabled = True

    async def search(self, query: str, num_results: int = 5):
        from assistant.backend.pipeline.search import SearchResult

        return [
            SearchResult(
                title=f"Result for {query}",
                url="http://127.0.0.1:8080/x",
                snippet="snip",
            )
        ]

    async def search_with_info(
        self, query: str, num_results: int = 5, llm_client=None, user_consent=False
    ):
        from assistant.backend.pipeline.search import SearchInfo, SearchResult
        return [
            SearchResult(
                title=f"Result for {query}",
                url="http://127.0.0.1:8080/x",
                snippet="snip",
            )
        ], SearchInfo(backend="test", query=query, results=[])

    def close(self):
        pass


def test_builtin_tools_gated_on_search():
    with_search = builtin_tools(EnabledSearch())
    without_search = builtin_tools(WebSearchTool(enabled=False))
    names_with = {t["function"]["name"] for t in with_search}
    names_without = {t["function"]["name"] for t in without_search}
    assert "web_search" in names_with
    assert "web_search" not in names_without
    # Core tools always available
    assert {"upsert_slot", "recall", "finalize"} <= names_without


def test_tool_def_shape():
    tools = builtin_tools(WebSearchTool(enabled=False))
    upsert_tool = next(t for t in tools if t["function"]["name"] == "upsert_slot")
    d = upsert_tool
    assert d["type"] == "function"
    assert d["function"]["name"] == "upsert_slot"
    assert "parameters" in d["function"]


async def test_upsert_slot_handler(store):
    from assistant.backend.pipeline.tool_executor import execute_tool, init_store
    init_store(str(store.db_path))
    # Create a test user
    await store.create_user("test_user")

    result = await execute_tool(
        "upsert_slot",
        {
            "frame_name": "test_entity",
            "slot_key": "test_key",
            "slot_value": "test_value",
        },
        "1",  # user_id must match created user
        "test_session"
    )
    assert result.success
    assert result.data["slot_key"] == "test_key"
    assert result.data["new_value"] == "test_value"


async def test_recall_handler(store):
    from assistant.backend.pipeline.tool_executor import execute_tool, init_store
    init_store(str(store.db_path))
    await store.create_user("test_user")

    result = await execute_tool("recall", {"query": "test query"}, "1", "test_session")
    assert result.success
    assert "results" in result.data


async def test_run_tool_loop_executes_and_answers(store):
    from assistant.backend.pipeline.tool_executor import init_store
    init_store(str(store.db_path))
    await store.create_user("test_user")
    
    scripted = [
        ChatResponse(
            content="",
            model="m",
            done=True,
tool_calls=[
            ToolCall(
                name="upsert_slot",
                arguments={
                    "frame_name": "test",
                    "slot_key": "key",
                    "slot_value": "value"
                }
            )
        ],
        ),
        ChatResponse(content="Stored successfully.", model="m", done=True),
    ]
    llm = FakeToolLLM(scripted)
    content, _resp, tool_msgs = await _run(llm)
    assert "Stored successfully" in content
    assert tool_msgs[-1]["role"] == "tool"


async def test_run_tool_loop_unknown_tool():
    scripted = [
        ChatResponse(
            content="",
            model="m",
            done=True,
            tool_calls=[ToolCall(name="does_not_exist", arguments={})],
        ),
        ChatResponse(content="OK then.", model="m", done=True),
    ]
    llm = FakeToolLLM(scripted)
    content, _, tool_msgs = await _run(llm)
    assert content == "OK then."
    # Unknown tool is logged as warning by tool_executor (verified in test output)
    # The tool result shows empty content since executor returns error but loop continues
    tool_result = tool_msgs[-1]
    assert tool_result["role"] == "tool"


async def test_run_tool_loop_bounded_rounds(store):
    from assistant.backend.pipeline.tool_executor import init_store
    init_store(str(store.db_path))
    await store.create_user("test_user")

    looping = ChatResponse(
        content="",
        model="m",
        done=True,
        tool_calls=[
            ToolCall(
                name="upsert_slot",
                arguments={"frame_name": "test", "slot_key": "key", "slot_value": "value"}
            )
        ],
    )
    llm = FakeToolLLM([looping] * 2 + [ChatResponse(content="done", model="m", done=True)])
    content, resp, _ = await _run(llm, max_rounds=2)
    # The loop runs 2 iterations, then one model-synthesized wrap-up call.
    assert len(llm.calls) == 3
    assert resp["loop_terminated"] == "max_turns"
    # The final answer is the model's wrap-up text, not raw tool metadata.
    assert content == "done"
    assert "Here's what I found" not in content


async def test_run_tool_loop_max_turns_synthesizes_wrap_up(store):
    """Regression: exhausting tool turns must not leak raw tool metadata.

    Before the fix, run_tool_loop returned a hardcoded dump of the tool-call
    records ("I've considered this for N turns. Here's what I found:
    [{'name': ...}]"). Now it makes one final text-only model call to wrap up
    in plain language, and failed tool calls surface their error to the model
    so it can recover (e.g. pivot to list_files) instead of repeating them.
    """
    from assistant.backend.pipeline.tool_executor import init_store
    init_store(str(store.db_path))
    await store.create_user("test_user")

    failing = ChatResponse(
        content="",
        model="m",
        done=True,
        tool_calls=[
            ToolCall(name="read_file", arguments={"path": "no_such_file.csv"})
        ],
    )
    wrap_up = ChatResponse(
        content="I couldn't find a file named no_such_file.csv in your sandbox.",
        model="m",
        done=True,
    )
    llm = FakeToolLLM([failing] * 3 + [wrap_up])
    content, resp, last_messages = await _run(llm)

    assert resp["loop_terminated"] == "max_turns"
    assert content == wrap_up.content
    assert "Here's what I found" not in content
    assert "read_file" not in content

    # The three failed reads reached the loop model as errors.
    tool_contents = [
        str(m.get("content", ""))
        for m in last_messages
        if m.get("role") == "tool"
    ]
    assert len(tool_contents) == 3
    assert all(
        c.startswith("ERROR:") and "File not found" in c for c in tool_contents
    )


async def test_tool_failures_are_surfaced_to_the_loop(store):
    """Regression: a failed tool call must tell the model *why* it failed.

    Before the fix only result.data reached the model, so a missing-file read
    came back as an empty {} and the model repeated the exact same call.
    """
    from assistant.backend.pipeline.tool_executor import init_store
    init_store(str(store.db_path))
    await store.create_user("test_user")

    scripted = [
        ChatResponse(
            content="",
            model="m",
            done=True,
            tool_calls=[
                ToolCall(name="read_file", arguments={"path": "missing.csv"})
            ],
        ),
        ChatResponse(content="Let me check what files exist first.", model="m", done=True),
    ]
    llm = FakeToolLLM(scripted)
    _, resp, last_llm_messages = await _run(llm)

    tool_msg = next(m for m in last_llm_messages if m.get("role") == "tool")
    assert "ERROR" in str(tool_msg.get("content", ""))
    assert "File not found" in str(tool_msg.get("content", ""))


async def test_tool_results_not_in_thinking_chain(store):
    """§6.3 hygiene: thinking is returned for audit but never re-sent as input."""
    from assistant.backend.pipeline.tool_executor import init_store
    init_store(str(store.db_path))
    await store.create_user("test_user")
    
    scripted = [
        ChatResponse(
            content="", model="m", done=True,
            thinking="pondering",
            tool_calls=[
                ToolCall(
                    name="upsert_slot",
                    arguments={"frame_name": "test", "slot_key": "key", "slot_value": "value"}
                )
            ],
        ),
        ChatResponse(content="2", model="m", done=True),
    ]
    llm = FakeToolLLM(scripted)
    _, resp, _ = await _run(llm)
    # run_tool_loop returns a dict with the answer
    assert resp.get("reasoning_effort") is not None
    second_call_messages = llm.calls[1]["messages"]
    assert all(m.get("content", "") != "pondering" for m in second_call_messages)


async def _run(llm, max_rounds=3):
    """Helper: run the loop and return (content, response, convo_messages)."""
    from assistant.backend.pipeline.tools import MAX_TOOL_ROUNDS

    tools = builtin_tools(EnabledSearch())
    messages = [{"role": "user", "content": "q"}]
    resp = await run_tool_loop(llm, messages, tools, max_rounds=max_rounds or MAX_TOOL_ROUNDS)
    last_llm_messages = llm.calls[-1]["messages"]
    return resp.get("answer", ""), resp, last_llm_messages


# ---------------------------------------------------------------------------
# fetch_url integration tests
# ---------------------------------------------------------------------------


class TestFetchUrlHandlerIntegration:
    """Test the fetch_url tool's integration with memory and extraction.

    These tests mock httpx at the module level so we control the network
    layer without requiring a live server.
    """

    @pytest.fixture
    def mock_httpx_get(self):
        """Patch httpx.AsyncClient.get to return controlled responses."""
        async def mock_get(self, url, **kwargs):
            return MockResponse(200, "Article text.", {"content-type": "text/plain"})

        with patch.object(httpx.AsyncClient, "get", new=mock_get):
            yield

    @pytest.mark.asyncio
    async def test_no_store_no_extraction(self, mock_httpx_get):
        """Without store/llm_client, fetch returns content without calling LLM."""
        handler = _make_fetch_url_handler(store=None, llm_client=None)
        result = await handler("http://example.com/article")
        assert "Article text" in result

    @pytest.mark.asyncio
    async def test_with_store_and_llm_calls_extraction(self, mock_httpx_get, store, stub_llm):
        """With store + llm_client, extraction is called and facts stored."""
        stub_llm.set_extraction_result(
            slots=[
                {
                    "frame_name": "test_article",
                    "frame_type": "entity",
                    "key": "author",
                    "value": "Jane Doe",
                },
            ],
            associations=[],
        )

        handler = _make_fetch_url_handler(store=store, llm_client=stub_llm)
        result = await handler("http://example.com/article")
        assert "Article text" in result

        frame = await store.get_frame_by_name("test_article")
        assert frame is not None
        assert frame.id is not None
        slots = await store.get_slots_for_frame(frame.id)
        slot = next((s for s in slots if s.key == "author"), None)
        assert slot is not None
        assert slot.value == "Jane Doe"

    @pytest.mark.asyncio
    async def test_extraction_error_does_not_break_fetch(self, mock_httpx_get, store):
        """If extraction raises, fetched content is still returned."""
        bad_llm = type("BadLLM", (), {
            "utility_model": "none",
            "chat": AsyncMock(side_effect=RuntimeError("LLM down")),
            "close": lambda self: None,
        })()

        handler = _make_fetch_url_handler(store=store, llm_client=bad_llm)
        result = await handler("http://example.com/page")
        assert "Article text" in result

    @pytest.mark.asyncio
    async def test_fetch_error_returns_error_message(self, store, stub_llm):
        """Network errors are returned as error strings, not raised."""
        async def failing_get(self, url, **kwargs):
            raise httpx.ConnectError("connection refused")

        with patch.object(httpx.AsyncClient, "get", new=failing_get):
            handler = _make_fetch_url_handler(store=store, llm_client=stub_llm)
            result = await handler("http://example.com/page")
            assert result.startswith("Error fetching")


async def test_list_files_tool(store):
    """Test the list_files tool returns uploaded files."""
    from assistant.backend.pipeline.tool_executor import execute_tool, init_store
    init_store(str(store.db_path))
    await store.create_user("test_user")

    # Create a file frame directly
    frame = await store.create_frame(
        "file_test_upload.txt",
        "entity",
        source_type="file_upload",
        owner_user_id=1,
        source_reliability=0.7,
    )
    
    await store.upsert_slot(
        frame_id=frame.id,
        key="file_name",
        value="test_upload.txt",
        essential=0,
        priority=0.5,
        source_type="file_upload",
        source_reliability=0.8,
    )
    await store.upsert_slot(
        frame_id=frame.id,
        key="file_content_preview",
        value="Hello world",
        essential=0,
        priority=0.5,
        source_type="file_upload",
        source_reliability=0.8,
    )
    await store.upsert_slot(
        frame_id=frame.id,
        key="file_size",
        value="11",
        essential=0,
        priority=0.5,
        source_type="file_upload",
        source_reliability=0.8,
    )
    await store.upsert_slot(
        frame_id=frame.id,
        key="file_ext",
        value="txt",
        essential=0,
        priority=0.5,
        source_type="file_upload",
        source_reliability=0.8,
    )
    await store.upsert_slot(
        frame_id=frame.id,
        key="file_safe_name",
        value="test_upload.txt",
        essential=0,
        priority=0.5,
        source_type="file_upload",
        source_reliability=0.7,
    )

    result = await execute_tool("list_files", {}, "1", "test_session")
    assert result.success
    assert "files" in result.data
    assert result.data["count"] >= 1
    assert any(f["file_name"] == "test_upload.txt" for f in result.data["files"])


async def test_list_files_filters_by_user(store):
    """Test list_files only returns files for the requesting user."""
    from assistant.backend.pipeline.tool_executor import execute_tool, init_store
    init_store(str(store.db_path))
    await store.create_user("test_user")
    await store.create_user("other_user")

    # Create file for user 1
    frame1 = await store.create_frame(
        "file_user1.txt",
        "entity",
        source_type="file_upload",
        owner_user_id=1,
        source_reliability=0.7,
    )
    await store.upsert_slot(frame_id=frame1.id, key="file_name", value="user1.txt",
        essential=0, priority=0.5, source_type="file_upload", source_reliability=0.8)
    await store.upsert_slot(frame_id=frame1.id, key="file_content_preview", value="User 1 file",
        essential=0, priority=0.5, source_type="file_upload", source_reliability=0.8)
    await store.upsert_slot(frame_id=frame1.id, key="file_size", value="11",
        essential=0, priority=0.5, source_type="file_upload", source_reliability=0.8)
    await store.upsert_slot(frame_id=frame1.id, key="file_ext", value="txt",
        essential=0, priority=0.5, source_type="file_upload", source_reliability=0.8)
    await store.upsert_slot(frame_id=frame1.id, key="file_safe_name", value="user1.txt",
        essential=0, priority=0.5, source_type="file_upload", source_reliability=0.7)

    # Create file for user 2
    frame2 = await store.create_frame(
        "file_user2.txt",
        "entity",
        source_type="file_upload",
        owner_user_id=2,
        source_reliability=0.7,
    )
    await store.upsert_slot(frame_id=frame2.id, key="file_name", value="user2.txt",
        essential=0, priority=0.5, source_type="file_upload", source_reliability=0.8)
    await store.upsert_slot(frame_id=frame2.id, key="file_content_preview", value="User 2 file",
        essential=0, priority=0.5, source_type="file_upload", source_reliability=0.8)
    await store.upsert_slot(frame_id=frame2.id, key="file_size", value="11",
        essential=0, priority=0.5, source_type="file_upload", source_reliability=0.8)
    await store.upsert_slot(frame_id=frame2.id, key="file_ext", value="txt",
        essential=0, priority=0.5, source_type="file_upload", source_reliability=0.8)
    await store.upsert_slot(frame_id=frame2.id, key="file_safe_name", value="user2.txt",
        essential=0, priority=0.5, source_type="file_upload", source_reliability=0.7)

    # User 1 should only see their file
    result = await execute_tool("list_files", {}, "1", "test_session")
    assert result.success
    assert result.data["count"] == 1
    assert result.data["files"][0]["file_name"] == "user1.txt"

    # User 2 should only see their file
    result = await execute_tool("list_files", {}, "2", "test_session")
    assert result.success
    assert result.data["count"] == 1
    assert result.data["files"][0]["file_name"] == "user2.txt"


async def test_list_files_excludes_soft_deleted(store):
    """Test list_files excludes files with frame priority=0 (soft deleted)."""
    from assistant.backend.pipeline.tool_executor import execute_tool, init_store
    init_store(str(store.db_path))
    await store.create_user("test_user")

    # Create active file (default frame priority is 0.5)
    frame1 = await store.create_frame(
        "file_active.txt",
        "entity",
        source_type="file_upload",
        owner_user_id=1,
        source_reliability=0.7,
    )
    await store.upsert_slot(frame_id=frame1.id, key="file_name", value="active.txt",
        essential=0, priority=0.5, source_type="file_upload", source_reliability=0.8)
    await store.upsert_slot(frame_id=frame1.id, key="file_content_preview", value="Active file",
        essential=0, priority=0.5, source_type="file_upload", source_reliability=0.8)
    await store.upsert_slot(frame_id=frame1.id, key="file_size", value="11",
        essential=0, priority=0.5, source_type="file_upload", source_reliability=0.8)
    await store.upsert_slot(frame_id=frame1.id, key="file_ext", value="txt",
        essential=0, priority=0.5, source_type="file_upload", source_reliability=0.8)
    await store.upsert_slot(frame_id=frame1.id, key="file_safe_name", value="active.txt",
        essential=0, priority=0.5, source_type="file_upload", source_reliability=0.7)

    # Create soft-deleted file (frame priority=0)
    frame2 = await store.create_frame(
        "file_deleted.txt",
        "entity",
        source_type="file_upload",
        owner_user_id=1,
        source_reliability=0.7,
        priority=0.0,  # Frame-level soft delete
    )
    await store.upsert_slot(frame_id=frame2.id, key="file_name", value="deleted.txt",
        essential=0, priority=0.5, source_type="file_upload", source_reliability=0.8)
    await store.upsert_slot(frame_id=frame2.id, key="file_content_preview", value="Deleted file",
        essential=0, priority=0.5, source_type="file_upload", source_reliability=0.8)
    await store.upsert_slot(frame_id=frame2.id, key="file_size", value="11",
        essential=0, priority=0.5, source_type="file_upload", source_reliability=0.8)
    await store.upsert_slot(frame_id=frame2.id, key="file_ext", value="txt",
        essential=0, priority=0.5, source_type="file_upload", source_reliability=0.8)
    await store.upsert_slot(frame_id=frame2.id, key="file_safe_name", value="deleted.txt",
        essential=0, priority=0.5, source_type="file_upload", source_reliability=0.7)

    # Should only see active file
    result = await execute_tool("list_files", {}, "1", "test_session")
    assert result.success
    assert result.data["count"] == 1
    assert result.data["files"][0]["file_name"] == "active.txt"


async def test_read_file_after_list_files(store):
    """Test the full flow: list_files -> read_file."""
    import tempfile
    from pathlib import Path

    from assistant.backend.pipeline.tool_executor import execute_tool, init_store
    init_store(str(store.db_path))
    await store.create_user("test_user")

    # Create a temp file on disk
    with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, dir="/app/data") as f:
        f.write("name,email\nAlice,a@b.com\nBob,b@c.com")
        temp_path = Path(f.name)
        safe_name = temp_path.name

    try:
        # Create file frame
        frame = await store.create_frame(
            f"file_{safe_name}",
            "entity",
            source_type="file_upload",
            owner_user_id=1,
            source_reliability=0.7,
        )
        await store.upsert_slot(frame_id=frame.id, key="file_name", value="test.csv",
            essential=0, priority=0.5, source_type="file_upload", source_reliability=0.8)
        await store.upsert_slot(
            frame_id=frame.id,
            key="file_content_preview",
            value="name,email...",
            essential=0,
            priority=0.5,
            source_type="file_upload",
            source_reliability=0.8,
        )
        await store.upsert_slot(frame_id=frame.id, key="file_size", value="30",
            essential=0, priority=0.5, source_type="file_upload", source_reliability=0.8)
        await store.upsert_slot(frame_id=frame.id, key="file_ext", value="csv",
            essential=0, priority=0.5, source_type="file_upload", source_reliability=0.8)
        await store.upsert_slot(frame_id=frame.id, key="file_safe_name", value=safe_name,
            essential=0, priority=0.5, source_type="file_upload", source_reliability=0.7)

        # List files
        list_result = await execute_tool("list_files", {}, "1", "test_session")
        assert list_result.success
        assert list_result.data["count"] >= 1
        csv_file = next(f for f in list_result.data["files"] if f["file_ext"] == "csv")

        # Read the file using frame_id from list
        read_result = await execute_tool(
            "read_file",
            {"frame_id": csv_file["frame_id"]},
            "1",
            "test_session",
        )
        assert read_result.success
        assert "Alice" in read_result.data["content"]
        assert "Bob" in read_result.data["content"]
        assert read_result.data["file_ext"] == "csv"
    finally:
        temp_path.unlink(missing_ok=True)


async def test_read_file_resolves_frame_name_given_as_path(store):
    """Regression: read_file(path='file_<name>') must resolve via the file frame.

    The model historically passed the frame name (which carries a 'file_'
    prefix) as the sandbox path. That literal lookup failed even though the
    uploaded file existed on disk under its exact name.
    """
    import tempfile
    from pathlib import Path

    from assistant.backend.pipeline.tool_executor import execute_tool, init_store
    init_store(str(store.db_path))
    await store.create_user("test_user")

    with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, dir="/app/data") as f:
        f.write("name,email\nAlice,a@b.com\nBob,b@c.com")
        temp_path = Path(f.name)
        safe_name = temp_path.name

    try:
        frame = await store.create_frame(
            f"file_{safe_name}",
            "entity",
            source_type="file_upload",
            owner_user_id=1,
            source_reliability=0.7,
        )
        await store.upsert_slot(frame_id=frame.id, key="file_name", value=safe_name,
            essential=0, priority=0.5, source_type="file_upload", source_reliability=0.8)
        await store.upsert_slot(frame_id=frame.id, key="file_ext", value="csv",
            essential=0, priority=0.5, source_type="file_upload", source_reliability=0.8)
        await store.upsert_slot(frame_id=frame.id, key="file_safe_name", value=safe_name,
            essential=0, priority=0.5, source_type="file_upload", source_reliability=0.8)

        # The frame name (with 'file_' prefix) is passed as the path.
        result = await execute_tool("read_file", {"path": frame.name}, "1", "test_session")
        assert result.success
        assert "Alice" in result.data["content"]
        assert result.data["frame_name"] == frame.name
        assert result.data["path"] == safe_name
    finally:
        temp_path.unlink(missing_ok=True)


async def test_read_file_resolves_stale_frame_name_from_old_conversation(store):
    """Regression (logged bug): the model recalled the OLD frame name from a
    past conversation and passed it to read_file(path=...).

    The current frame is 'file_subscribers_active.csv' but past episodes still
    quote 'file_upload_20260917_172108_..._subscribers_active.csv'. The stale
    name must resolve to the current file, not fail the literal lookup.
    """
    import tempfile
    from pathlib import Path

    from assistant.backend.pipeline.tool_executor import execute_tool, init_store
    init_store(str(store.db_path))
    await store.create_user("test_user")

    with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, dir="/app/data") as f:
        f.write("email,status\na@b.com,active\nc@d.com,inactive")
        temp_path = Path(f.name)
        safe_name = temp_path.name

    try:
        frame = await store.create_frame(
            "file_subscribers_active.csv",
            "entity",
            source_type="file_upload",
            owner_user_id=1,
            source_reliability=0.7,
        )
        await store.upsert_slot(frame_id=frame.id, key="file_name", value="subscribers_active.csv",
            essential=0, priority=0.5, source_type="file_upload", source_reliability=0.8)
        await store.upsert_slot(frame_id=frame.id, key="file_ext", value="csv",
            essential=0, priority=0.5, source_type="file_upload", source_reliability=0.8)
        await store.upsert_slot(frame_id=frame.id, key="file_safe_name", value=safe_name,
            essential=0, priority=0.5, source_type="file_upload", source_reliability=0.8)

        stale = "file_upload_20260917_172108_1789683647_198897719522100940_subscribers_active.csv"
        result = await execute_tool("read_file", {"path": stale}, "1", "test_session")
        assert result.success
        assert "a@b.com" in result.data["content"]
        assert result.data["frame_name"] == "file_subscribers_active.csv"
        assert result.data["path"] == safe_name
    finally:
        temp_path.unlink(missing_ok=True)


async def test_read_file_miss_reports_available_files(store):
    """A failed read_file must say what the user actually has, so the model
    can self-correct with a real name instead of retrying blindly."""
    import tempfile
    from pathlib import Path

    from assistant.backend.pipeline.tool_executor import execute_tool, init_store
    init_store(str(store.db_path))
    await store.create_user("test_user")

    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, dir="/app/data") as f:
        f.write("hello world")
        temp_path = Path(f.name)
        safe_name = temp_path.name

    try:
        frame = await store.create_frame(
            f"file_{safe_name}",
            "entity",
            source_type="file_upload",
            owner_user_id=1,
            source_reliability=0.7,
        )
        await store.upsert_slot(frame_id=frame.id, key="file_name", value=safe_name,
            essential=0, priority=0.5, source_type="file_upload", source_reliability=0.8)
        await store.upsert_slot(frame_id=frame.id, key="file_safe_name", value=safe_name,
            essential=0, priority=0.5, source_type="file_upload", source_reliability=0.8)

        result = await execute_tool("read_file", {"path": "no_such_thing.csv"}, "1", "test_session")
        assert not result.success
        assert "File not found" in result.error
        assert safe_name in result.error  # the available file is named in the error
    finally:
        temp_path.unlink(missing_ok=True)


class MockResponse:
    """Minimal httpx response stand-in for mock_httpx_get."""

    def __init__(self, status_code: int, text: str, headers: dict):
        self.status_code = status_code
        self.text = text
        self.content = text.encode("utf-8")
        self.headers = headers
        self.encoding = "utf-8"

    def raise_for_status(self):
        pass