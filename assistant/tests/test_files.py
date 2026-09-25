"""File viewer backend tests.

Tests for Files tab UI: upload, view, delete, cascade cleanup, bulk delete.
"""

import pytest
from fastapi.testclient import TestClient

from assistant.backend.config import settings
from assistant.backend.main import _state, app, get_orchestrator, get_store
from assistant.backend.memory.retrieval import Retriever
from assistant.backend.pipeline.orchestrator import Orchestrator, OrchestratorDeps
from assistant.backend.pipeline.tool_executor import init_store


@pytest.fixture
async def client(store, stub_llm, stub_search):
    """FastAPI TestClient with test dependencies wired in."""
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

    # Initialize tool executor with store and embed function
    # Use nomic-embed-text (768 dims) to match test embeddings
    init_store(store.db_path, embed_fn=orchestrator.embed_fn(), embedding_model="nomic-embed-text")

    # Create user 1 (required by file upload endpoint)
    await store.create_user("test_user")

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


@pytest.fixture
def tool_executor(store, stub_llm, stub_search):
    """Initialize tool executor for direct tool testing."""
    retriever = Retriever(store=store, llm_client=stub_llm)
    orchestrator = Orchestrator(
        deps=OrchestratorDeps(
            store=store,
            retriever=retriever,
            llm_client=stub_llm,
            search_tool=stub_search,
        )
    )
    init_store(store.db_path, embed_fn=orchestrator.embed_fn(), embedding_model="nomic-embed-text")
    # Create test user for foreign key constraints
    import asyncio
    asyncio.run(store.create_user("test_user"))
    return orchestrator


@pytest.fixture(autouse=True)
async def clean_sandbox():
    """Clean sandbox directory before each test."""
    import shutil
    from pathlib import Path
    sandbox = Path("/app/data")
    if sandbox.exists():
        for item in sandbox.iterdir():
            if item.is_file():
                item.unlink()
            else:
                shutil.rmtree(item)
    yield


class TestFileExtraction:
    """CSV/text extraction correctness tests.

    These test the core CSV extraction enhancement without needing
    the full TestClient infrastructure.
    """

    def test_csv_extraction_returns_row_data(self):
        """Enhanced CSV extraction returns row_data list of dicts."""
        from assistant.backend.pipeline.files import extract_text_from_csv

        csv_bytes = b"name,email,status\nAlice,a@b.com,active\nBob,b@c.com,pending"
        plain_text, key_entities, open_questions, row_data = extract_text_from_csv(csv_bytes)

        assert plain_text != "", "plain_text should not be empty"
        assert len(key_entities) > 0, "should extract key entities"
        assert open_questions != [], "should have open questions"
        assert row_data is not None, "should return row_data"
        assert isinstance(row_data, list), "row_data should be a list"
        assert len(row_data) == 2, f"expected 2 rows, got {len(row_data)}"
        assert row_data[0] == {"name": "Alice", "email": "a@b.com", "status": "active"}
        assert row_data[1] == {"name": "Bob", "email": "b@c.com", "status": "pending"}

    def test_csv_extraction_empty(self):
        """Empty CSV returns empty row_data."""
        from assistant.backend.pipeline.files import extract_text_from_csv

        plain_text, key_entities, open_questions, row_data = extract_text_from_csv(b"")
        assert row_data is None or row_data == []
        assert plain_text == ""

    def test_csv_extraction_no_data_rows(self):
        """CSV with only header returns row_data with no rows."""
        from assistant.backend.pipeline.files import extract_text_from_csv

        csv_bytes = b"name,email,status"
        plain_text, key_entities, open_questions, row_data = extract_text_from_csv(csv_bytes)
        assert len(row_data) == 0
        assert plain_text != ""


class TestFileViewerBackend:
    """Backend tests for Files tab functionality.

    Uses the `client` fixture with wired dependencies.
    """

    @pytest.mark.asyncio
    async def test_upload_txt_file(self, client, store, tmp_path):
        """Upload a .txt file and verify frame/slot creation."""

        # Use the TestClient from the fixture
        test_file = tmp_path / "test_hello.txt"
        test_file.write_text("Hello, world!\nThis is a test file.")

        with open(test_file, "rb") as f:
            resp = client.post(
                "/files/upload",
                files={"file": ("test_hello.txt", f.read(), "text/plain")},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["file_ext"] == "txt"
        assert data["file_name"] == "test_hello.txt"
        assert "frame_id" in data
        assert "frame_name" in data

        # Verify frame and slot exist in memory store
        frame_id = data["frame_id"]
        frame = await store.get_frame(frame_id)
        assert frame is not None
        assert frame.name == data["frame_name"]

        # Verify slots were created
        slots = await store.get_slots_for_frame(frame_id)
        slot_keys = [s.key for s in slots]
        assert "file_name" in slot_keys
        assert "file_content_preview" in slot_keys

    @pytest.mark.asyncio
    async def test_upload_csv_file_creates_parent_and_row_frames(self, client, store, tmp_path):
        """CSV upload creates parent document frame + row record frames
        with part_of associations."""
        test_file = tmp_path / "users.csv"
        test_file.write_text(
            "name,email,status\n"
            "Alice,a@b.com,active\n"
            "Bob,b@c.com,pending\n"
        )

        with open(test_file, "rb") as f:
            resp = client.post(
                "/files/upload",
                files={"file": ("users.csv", f.read(), "text/csv")},
            )

        assert resp.status_code == 200
        data = resp.json()

        assert data["status"] == "ok"
        assert data["file_ext"] == "csv"
        assert data["row_count"] == 2
        assert "parent_frame_id" in data
        assert "row_frame_ids" in data

        parent_frame_id = data["parent_frame_id"]
        row_frame_ids = data["row_frame_ids"]

        # Verify parent frame
        parent_frame = await store.get_frame(parent_frame_id)
        assert parent_frame is not None
        assert parent_frame.type == "entity"

        # Verify row frames
        assert len(row_frame_ids) == 2
        for row_frame_id in row_frame_ids:
            row_frame = await store.get_frame(row_frame_id)
            assert row_frame is not None
            assert row_frame.type == "record"

        # Verify part_of associations
        associations = await store.get_all_associations_for_frame(parent_frame_id)
        part_of_links = [a for a in associations if a.relation_type == "part_of"]
        assert len(part_of_links) == 2, (
            f"expected 2 part_of associations, got {len(part_of_links)}"
        )

        # Verify parent has columns and row_count slots
        columns_slot = await store.get_slot(parent_frame_id, "columns")
        row_count_slot = await store.get_slot(parent_frame_id, "row_count")
        assert columns_slot is not None
        assert row_count_slot is not None
        assert columns_slot.value == '["name", "email", "status"]'
        assert row_count_slot.value == "2"

        # Verify row frame slots have column values
        for i, row_frame_id in enumerate(row_frame_ids):
            row_frame = await store.get_frame(row_frame_id)
            name_slot = await store.get_slot(row_frame_id, "name")
            assert name_slot is not None
            if i == 0:
                assert "Alice" in name_slot.value, (
                f"row {i} should have Alice, got '{name_slot.value}'"
            )
            elif i == 1:
                assert "Bob" in name_slot.value, (
                f"row {i} should have Bob, got '{name_slot.value}'"
            )

    @pytest.mark.asyncio
    async def test_delete_file_cascades(self, client, store, tmp_path):
        """DELETE /files/{frame_id} removes the frame, its memory, and the file.

        Regression: file delete used to soft-delete (priority 0) while leaving
        the frame in list_frames()/the brain graph, dangling after the physical
        file was gone.
        """
        test_file = tmp_path / "to_delete.txt"
        test_file.write_text("Delete me.")

        with open(test_file, "rb") as f:
            resp = client.post(
                "/files/upload",
                files={"file": ("to_delete.txt", f.read(), "text/plain")},
            )

        assert resp.status_code == 200
        frame_id = resp.json()["frame_id"]

        # Verify memory exists before delete
        frame_before = await store.get_frame(frame_id)
        assert frame_before is not None

        slots_before = await store.get_slots_for_frame(frame_id)
        assert len(slots_before) > 0

        # Delete the file
        del_resp = client.delete(f"/files/{frame_id}")
        assert del_resp.status_code == 200

        # Frame is permanently gone — not soft-deleted to priority 0
        assert await store.get_frame(frame_id) is None, "frame should be removed"

        # All its slots cascade away
        async with store._connect() as db:
            slot_count = (
                await db.execute_fetchall(
                    "SELECT COUNT(*) FROM slots WHERE frame_id = ?", (frame_id,)
                )
            )[0][0]
            assert slot_count == 0, "slots should be cascaded away"

        # Physical file gone — the upload wrote upload_<ts>_to_delete.txt into
        # /app/data and the delete must have unlinked it.
        from pathlib import Path
        assert not list(Path("/app/data").glob("*to_delete.txt")), (
            "physical file should be removed"
        )

    @pytest.mark.asyncio
    async def test_delete_csv_removes_row_frames(self, client, store, tmp_path):
        """DELETE parent CSV frame also removes all row frames and part_of associations."""
        test_file = tmp_path / "users.csv"
        test_file.write_text(
            "name,email,status\n"
            "Alice,a@b.com,active\n"
            "Bob,b@c.com,pending\n"
        )

        with open(test_file, "rb") as f:
            resp = client.post(
                "/files/upload",
                files={"file": ("users.csv", f.read(), "text/csv")},
            )

        assert resp.status_code == 200
        parent_frame_id = resp.json()["parent_frame_id"]
        row_frame_ids = resp.json()["row_frame_ids"]
        assert len(row_frame_ids) == 2

        # Delete parent frame
        del_resp = client.delete(f"/files/{parent_frame_id}")
        assert del_resp.status_code == 200

        # Parent is permanently gone
        assert await store.get_frame(parent_frame_id) is None, (
            "parent frame should be removed"
        )

        # Regression: row frames used to survive the delete, so the whole CSV
        # cluster kept polluting memory after the file was gone.
        for row_frame_id in row_frame_ids:
            assert await store.get_frame(row_frame_id) is None, (
                f"row frame {row_frame_id} should be removed with the file"
            )

        # part_of associations cascade away with the frames
        async with store._connect() as db:
            assoc_count = (
                await db.execute_fetchall(
                    "SELECT COUNT(*) FROM associations "
                    "WHERE from_frame_id = ? OR to_frame_id = ?",
                    (parent_frame_id, parent_frame_id),
                )
            )[0][0]
            assert assoc_count == 0, "part_of associations should be removed"


class TestFileSandboxTools:
    """Regression tests for agent file sandbox tools (write, read, edit, delete, glob, recall)."""

    @pytest.fixture(autouse=True)
    async def _init_tools(self, tool_executor):
        """Ensure tool executor is initialized for each test."""
        self.tool_executor = tool_executor

    @pytest.mark.asyncio
    async def test_write_file_creates_file_and_frame(self, store, stub_llm):
        """write_file tool creates file in sandbox and memory frame."""
        from pathlib import Path

        from assistant.backend.pipeline.tool_executor import execute_write_file

        result = await execute_write_file(
            {"path": "test_agent.txt", "content": "Hello from agent"},
            user_id="1", session_id="test"
        )
        assert result.success
        assert result.data["path"] == "test_agent.txt"

        # Verify physical file
        assert Path("/app/data/test_agent.txt").read_text() == "Hello from agent"

        # Verify memory frame
        frame = await store.get_frame_by_name("file_test_agent.txt")
        assert frame is not None
        slots = await store.get_slots_for_frame(frame.id)
        assert any(s.key == "file_content_preview" and "Hello" in s.value for s in slots)

    @pytest.mark.asyncio
    async def test_write_file_rejects_traversal(self, store, stub_llm):
        """write_file rejects paths escaping sandbox."""
        from assistant.backend.pipeline.tool_executor import execute_write_file

        result = await execute_write_file(
            {"path": "../../../etc/passwd", "content": "evil"},
            user_id="1", session_id="test"
        )
        assert not result.success
        assert "escapes sandbox" in result.error

    @pytest.mark.asyncio
    async def test_write_file_overwrite_protection(self, store, stub_llm):
        """write_file rejects overwrite without flag."""
        from assistant.backend.pipeline.tool_executor import execute_write_file

        await execute_write_file({"path": "same.txt", "content": "first"}, "1", "test")
        result = await execute_write_file({"path": "same.txt", "content": "second"}, "1", "test")
        assert not result.success
        assert "exists" in result.error

        # With overwrite=true should work
        result = await execute_write_file(
            {"path": "same.txt", "content": "second", "overwrite": True}, "1", "test"
        )
        assert result.success

    @pytest.mark.asyncio
    async def test_read_file_by_user_visible_name(self, store, stub_llm):
        """read_file reads by relative path (not frame_id)."""
        from assistant.backend.pipeline.tool_executor import execute_read_file, execute_write_file

        await execute_write_file(
            {"path": "notes/readme.md", "content": "# Readme\nContent here"}, "1", "test"
        )

        result = await execute_read_file({"path": "notes/readme.md"}, "1", "test")
        assert result.success
        assert result.data["content"] == "# Readme\nContent here"

    @pytest.mark.asyncio
    async def test_read_file_rejects_traversal(self, store, stub_llm):
        """read_file rejects paths escaping sandbox."""
        from assistant.backend.pipeline.tool_executor import execute_read_file

        result = await execute_read_file({"path": "../../../etc/passwd"}, "1", "test")
        assert not result.success
        assert "escapes sandbox" in result.error

    @pytest.mark.asyncio
    async def test_edit_file_surgical_replace(self, store, stub_llm):
        """edit_file replaces exact text match."""
        from assistant.backend.pipeline.tool_executor import (
            execute_edit_file,
            execute_read_file,
            execute_write_file,
        )

        await execute_write_file({"path": "edit_test.txt", "content": "foo bar baz"}, "1", "test")

        result = await execute_edit_file(
            {"path": "edit_test.txt", "old_text": "bar", "new_text": "BAR"},
            "1", "test"
        )
        assert result.success
        assert result.data["changes"] == 1

        read_result = await execute_read_file({"path": "edit_test.txt"}, "1", "test")
        assert "foo BAR baz" in read_result.data["content"]

    @pytest.mark.asyncio
    async def test_edit_file_replace_all_false(self, store, stub_llm):
        """edit_file with replace_all=false replaces only first occurrence."""
        from assistant.backend.pipeline.tool_executor import (
            execute_edit_file,
            execute_read_file,
            execute_write_file,
        )

        await execute_write_file({"path": "multi.txt", "content": "foo bar baz bar"}, "1", "test")

        result = await execute_edit_file(
            {"path": "multi.txt", "old_text": "bar", "new_text": "BAR", "replace_all": False},
            "1", "test"
        )
        assert result.success
        assert result.data["changes"] == 1

        read_result = await execute_read_file({"path": "multi.txt"}, "1", "test")
        assert read_result.data["content"] == "foo BAR baz bar"

    @pytest.mark.asyncio
    async def test_delete_file_removes_file_and_frame(self, store, stub_llm):
        """delete_file removes physical file and prunes the memory frame."""
        from pathlib import Path

        from assistant.backend.pipeline.tool_executor import execute_delete_file, execute_write_file

        await execute_write_file({"path": "to_delete.txt", "content": "delete me"}, "1", "test")

        result = await execute_delete_file({"path": "to_delete.txt"}, "1", "test")
        assert result.success

        # Physical file gone
        assert not Path("/app/data/to_delete.txt").exists()

        # Regression: frame used to be soft-deleted (priority 0) and stayed
        # visible in list_frames()/the brain graph after the file was gone.
        assert await store.get_frame_by_name("file_to_delete.txt") is None, (
            "frame should be removed with the file"
        )

    @pytest.mark.asyncio
    async def test_glob_finds_files_by_pattern(self, store, stub_llm):
        """glob tool finds files matching pattern."""
        from assistant.backend.pipeline.tool_executor import execute_glob, execute_write_file

        await execute_write_file({"path": "notes/a.txt", "content": "a"}, "1", "test")
        await execute_write_file({"path": "notes/b.txt", "content": "b"}, "1", "test")
        await execute_write_file({"path": "scripts/c.py", "content": "c"}, "1", "test")

        result = await execute_glob({"pattern": "notes/*.txt"}, "1", "test")
        assert result.success
        assert result.data["count"] == 2
        paths = [f["path"] for f in result.data["files"]]
        assert "notes/a.txt" in paths
        assert "notes/b.txt" in paths
        assert "scripts/c.py" not in paths

    @pytest.mark.asyncio
    async def test_glob_rejects_traversal(self, store, stub_llm):
        """glob rejects patterns with traversal."""
        from assistant.backend.pipeline.tool_executor import execute_glob

        result = await execute_glob({"pattern": "../../../etc/*"}, "1", "test")
        assert not result.success
        assert "escapes sandbox" in result.error or "traversal" in result.error

    @pytest.mark.asyncio
    async def test_recall_returns_semantic_matches(self, store, stub_llm):
        """recall tool embeds query and searches frames."""
        from assistant.backend.pipeline.tool_executor import execute_recall

        # Create some frames with known content
        frame = await store.create_frame("person_alice", "person", owner_user_id=1)
        await store.upsert_slot(frame.id, "name", "Alice", source_type="test")
        await store.upsert_slot(frame.id, "role", "engineer", source_type="test")

        # Embed and index using stub_llm's embed method (deterministic)
        embed_resp = await stub_llm.embed("Alice engineer")
        await store.store_frame_embedding(frame.id, embed_resp.embedding, "nomic-embed-text")

        result = await execute_recall({"query": "Alice engineer"}, "1", "test")
        assert result.success
        assert result.data["count"] >= 1
        assert any("Alice" in str(r["slots"]) for r in result.data["results"])

    @pytest.mark.asyncio
    async def test_csv_write_creates_row_frames(self, store, stub_llm):
        """write_file with CSV content creates parent + row frames."""
        from assistant.backend.pipeline.tool_executor import execute_write_file

        csv_content = "name,email\nAlice,a@b.com\nBob,b@c.com"
        result = await execute_write_file(
            {"path": "users.csv", "content": csv_content},
            "1", "test"
        )
        assert result.success

        # Verify parent frame has row_count
        frame = await store.get_frame_by_name("file_users.csv")
        row_count = await store.get_slot(frame.id, "row_count")
        assert row_count.value == "2"

        # Verify row frames exist
        associations = await store.get_all_associations_for_frame(frame.id)
        part_of = [a for a in associations if a.relation_type == "part_of"]
        assert len(part_of) == 2

    @pytest.mark.asyncio
    async def test_large_csv_write_keeps_metadata_only(self, store, stub_llm, monkeypatch):
        """write_file CSV past CSV_MAX_ROW_FRAMES stores metadata only — no row frames."""
        from assistant.backend.pipeline.tool_executor import execute_write_file

        monkeypatch.setattr(settings, "csv_max_row_frames", 2)
        csv_content = "name,email\n" + "".join(f"u{i},u{i}@x.com\n" for i in range(4))
        result = await execute_write_file(
            {"path": "big.csv", "content": csv_content}, "1", "test"
        )
        assert result.success

        frame = await store.get_frame_by_name("file_big.csv")
        assert frame is not None
        row_count = await store.get_slot(frame.id, "row_count")
        columns = await store.get_slot(frame.id, "columns")
        assert row_count is not None and row_count.value == "4"
        assert columns is not None and "email" in columns.value

        # No per-row part_of frames past the cap — row data stays on disk.
        associations = await store.get_all_associations_for_frame(frame.id)
        assert not [a for a in associations if a.relation_type == "part_of"]

    @pytest.mark.asyncio
    async def test_csv_delete_cascades_to_row_frames(self, store, stub_llm):
        """delete_file on CSV parent also prunes its per-row frames."""
        from assistant.backend.pipeline.tool_executor import execute_delete_file, execute_write_file

        csv_content = "name,email\nAlice,a@b.com\nBob,b@c.com"
        await execute_write_file({"path": "users.csv", "content": csv_content}, "1", "test")

        frame = await store.get_frame_by_name("file_users.csv")
        associations = await store.get_all_associations_for_frame(frame.id)
        row_frame_ids = [a.to_frame_id for a in associations if a.relation_type == "part_of"]
        assert len(row_frame_ids) == 2

        # Delete parent
        await execute_delete_file({"path": "users.csv"}, "1", "test")

        # Regression: row frames used to be soft-deleted (priority 0) and kept
        # the entire CSV cluster alive in memory after the file was gone.
        assert await store.get_frame_by_name("file_users.csv") is None, (
            "parent frame should be removed"
        )
        for row_id in row_frame_ids:
            assert await store.get_frame(row_id) is None, (
                f"row frame {row_id} should be removed with the file"
            )

    @pytest.mark.asyncio
    async def test_list_files_shows_sandbox_files(self, store, stub_llm):
        """list_files returns files from sandbox with memory enrichment."""
        from assistant.backend.pipeline.tool_executor import execute_list_files, execute_write_file

        await execute_write_file({"path": "notes/list_test.txt", "content": "listed"}, "1", "test")

        result = await execute_list_files({}, "1", "test")
        assert result.success
        assert result.data["count"] >= 1
        paths = [f.get("path") for f in result.data["files"]]
        assert "notes/list_test.txt" in paths