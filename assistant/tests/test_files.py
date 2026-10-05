"""File viewer backend tests.

Tests for Files tab UI: upload, view, delete, cascade cleanup, bulk delete.
"""

from pathlib import Path

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

        # Verify the identity slots were created.
        slots = await store.get_slots_for_frame(frame_id)
        slot_keys = [s.key for s in slots]
        assert "file_name" in slot_keys
        assert "file_size" in slot_keys
        # And that no content slot was: memory holds what a file IS, not what it
        # CONTAINS. The bytes are on disk and read verbatim.
        assert "file_content_preview" not in slot_keys
        assert "file_content" not in slot_keys

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
    async def test_upload_frame_name_is_readable(self, client, store, tmp_path):
        """Upload frames are named from the file's own name, and the on-disk
        copy keeps the exact uploaded filename too — nothing is renamed."""
        test_file = tmp_path / "members.csv"
        test_file.write_text("name,email\nAlice,a@b.com\n")

        with open(test_file, "rb") as f:
            resp = client.post(
                "/files/upload",
                files={"file": ("members.csv", f.read(), "text/csv")},
            )

        assert resp.status_code == 200
        data = resp.json()
        # Readable frame name, no upload_<timestamp>_ prefix
        assert data["frame_name"] == "file_members.csv"
        frame = await store.get_frame(data["parent_frame_id"])
        assert frame is not None and frame.name == "file_members.csv"

        # The on-disk copy keeps the exact uploaded name, tracked via the slot
        safe_name = await store.get_slot(frame.id, "file_safe_name")
        assert safe_name is not None and safe_name.value == "members.csv"

        # /files/list exposes the real uploaded filename for the Files page
        entries = client.get("/files/list").json()
        entry = next(e for e in entries if e["id"] == data["parent_frame_id"])
        assert entry["file_name"] == "members.csv"

    @pytest.mark.asyncio
    async def test_upload_preserves_exact_filename_on_disk(self, client, store, tmp_path):
        """Uploaded files keep their exact name on disk (no upload_<ts>_ prefix),
        and same-name re-uploads overwrite that single copy instead of leaving
        timestamped duplicates behind."""
        from pathlib import Path

        data_dir = Path("/app/data")
        disk = data_dir / "playlist.csv"

        # Upload -> stored exactly as "playlist.csv"
        test_file = tmp_path / "playlist.csv"
        test_file.write_text("song,artist\nA,X\n")
        with open(test_file, "rb") as f:
            resp = client.post(
                "/files/upload",
                files={"file": ("playlist.csv", f.read(), "text/csv")},
            )
        assert resp.status_code == 200
        first = resp.json()
        assert disk.exists(), "must be stored under its exact uploaded name"
        assert disk.read_text() == "song,artist\nA,X\n"

        # Same-name re-upload -> same frame, same disk copy, new content
        test_file.write_text("song,artist\nB,Y\n")
        with open(test_file, "rb") as f:
            resp2 = client.post(
                "/files/upload",
                files={"file": ("playlist.csv", f.read(), "text/csv")},
            )
        assert resp2.status_code == 200
        second = resp2.json()
        assert second["parent_frame_id"] == first["parent_frame_id"]
        assert disk.read_text() == "song,artist\nB,Y\n"

        safe = await store.get_slot(first["parent_frame_id"], "file_safe_name")
        assert safe is not None and safe.value == "playlist.csv"

    @pytest.mark.asyncio
    async def test_reupload_same_name_rebuilds_rows(self, client, store, tmp_path):
        """Re-uploading the same filename merges into the existing frame and
        replaces its CSV row frames instead of accumulating duplicates."""
        test_file = tmp_path / "users.csv"
        test_file.write_text("name,email\nAlice,a@b.com\n")

        def upload():
            with open(test_file, "rb") as f:
                return client.post(
                    "/files/upload",
                    files={"file": ("users.csv", f.read(), "text/csv")},
                )

        first = upload()
        assert first.status_code == 200
        first = first.json()
        second = upload()
        assert second.status_code == 200
        second = second.json()

        # Merged into the same frame (frame names are UNIQUE)
        assert second["parent_frame_id"] == first["parent_frame_id"]
        assert second["frame_name"] == first["frame_name"] == "file_users.csv"

        # Only one set of row frames remains — the second upload replaces the first
        associations = await store.get_all_associations_for_frame(first["parent_frame_id"])
        part_of = [a for a in associations if a.relation_type == "part_of"]
        assert len(part_of) == 1, "row frames from the first upload must not linger"
        assert {a.to_frame_id for a in part_of} == set(second["row_frame_ids"])

    @pytest.mark.asyncio
    async def test_upload_entity_slots_are_capped(self, client, store, tmp_path):
        """A large CSV must not dump one entity_* slot per unique cell onto the
        file frame — the upload path caps entities (FILE_MAX_ENTITY_SLOTS=50)."""
        lines = ["name,email\n"] + [f"person{i},p{i}@x.com\n" for i in range(60)]
        test_file = tmp_path / "big.csv"
        test_file.write_text("".join(lines))

        with open(test_file, "rb") as f:
            resp = client.post(
                "/files/upload",
                files={"file": ("big.csv", f.read(), "text/csv")},
            )

        assert resp.status_code == 200
        data = resp.json()
        # Response mirrors what's actually stored
        assert len(data["key_entities"]) == 50

        slots = await store.get_slots_for_frame(data["parent_frame_id"])
        entity_count = sum(1 for s in slots if s.key.startswith("entity_"))
        assert entity_count == 50, f"expected 50 entity slots, got {entity_count}"

        # File identity slots survive untouched. No content slot appears: content
        # lives on disk, never in memory.
        keys = {s.key for s in slots}
        assert {
            "file_name", "file_size", "file_ext",
            "file_safe_name", "row_count", "columns",
        } <= keys
        assert "file_content_preview" not in keys

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


class TestFileSearchEndpoint:
    """`GET /files/search` — semantic search over uploaded files.

    Regression: the endpoint constructed its own `OllamaClient(url=..., model=...)`
    and then called a nonexistent `embed_query`, so *every* request raised
    TypeError → 500. Nothing in the UI calls this endpoint, so the failure was
    invisible. It also returned non-file frames and left `file_name` null.
    """

    async def _embed(self, store, stub_llm, frame_id):
        """Give a frame a vector under the model the endpoint searches with.

        Uploads do not embed inline (consolidation tops up misses), so without
        this every query matches nothing and the test would pass vacuously.
        """
        stored = await store.embed_frames(
            [frame_id], stub_llm.embed_one, settings.embedding_model
        )
        assert stored == 1, "test setup: frame should have one stored vector"

    @pytest.mark.asyncio
    async def test_search_finds_uploaded_file(self, client, store, stub_llm, tmp_path):
        test_file = tmp_path / "guitar_lesson.txt"
        test_file.write_text("My guitar practice log: alternate picking drills.")

        with open(test_file, "rb") as f:
            resp = client.post(
                "/files/upload",
                files={"file": ("guitar_lesson.txt", f.read(), "text/plain")},
            )
        assert resp.status_code == 200
        frame_id = resp.json()["frame_id"]
        await self._embed(store, stub_llm, frame_id)

        found = client.get("/files/search", params={"query": "guitar"})

        assert found.status_code == 200, found.text
        body = found.json()
        assert body["query"] == "guitar"
        assert [f["id"] for f in body["frames"]] == [frame_id]
        # The user's own filename, not the internal frame handle.
        assert body["frames"][0]["file_name"] == "guitar_lesson.txt"

    @pytest.mark.asyncio
    async def test_search_returns_only_files(self, client, store, stub_llm, tmp_path):
        """A memory frame that matches the query is not a file.

        It shares the embedding cluster on purpose, so only a file-only filter
        keeps it out of a "search my files" result.
        """
        test_file = tmp_path / "guitar_amp_notes.txt"
        test_file.write_text("Notes about my guitar amp settings.")

        with open(test_file, "rb") as f:
            resp = client.post(
                "/files/upload",
                files={"file": ("guitar_amp_notes.txt", f.read(), "text/plain")},
            )
        file_frame_id = resp.json()["frame_id"]
        await self._embed(store, stub_llm, file_frame_id)

        memory_frame = await store.create_frame(
            "guitar_amp", "entity", owner_user_id=1, source_type="conversation"
        )
        await store.upsert_slot(
            frame_id=memory_frame.id, key="model", value="fender amp"
        )
        await self._embed(store, stub_llm, memory_frame.id)

        found = client.get("/files/search", params={"query": "guitar"})

        assert found.status_code == 200, found.text
        assert [f["id"] for f in found.json()["frames"]] == [file_frame_id]

    @pytest.mark.asyncio
    async def test_search_filters_by_file_type(self, client, store, stub_llm, tmp_path):
        text_file = tmp_path / "guitar_lesson.txt"
        text_file.write_text("My guitar practice log.")
        csv_file = tmp_path / "guitar_tabs.csv"
        csv_file.write_text("bar,note\n1,E\n2,B\n")

        frame_ids = {}
        for path, ctype in ((text_file, "text/plain"), (csv_file, "text/csv")):
            with open(path, "rb") as f:
                resp = client.post(
                    "/files/upload", files={"file": (path.name, f.read(), ctype)}
                )
            assert resp.status_code == 200
            frame_ids[resp.json()["file_ext"]] = resp.json()["frame_id"]
            await self._embed(store, stub_llm, resp.json()["frame_id"])

        found = client.get(
            "/files/search", params={"query": "guitar", "file_type": "csv"}
        )

        assert found.status_code == 200, found.text
        assert [f["id"] for f in found.json()["frames"]] == [frame_ids["csv"]]


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
        # The content is on disk and read verbatim; memory records identity only.
        assert not any(s.key == "file_content_preview" for s in slots)
        assert Path("/app/data/test_agent.txt").read_text() == "Hello from agent"

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
    async def test_edit_file_refuses_a_binary_document(self, store, stub_llm):
        """A .docx cannot be edited in place; the refusal redirects to rewrite.

        `read_file` extracts a document to text, but `edit_file` operates on the
        bytes (a zip), so an exact match can never be found. It used to report a
        misleading "old_text not found"; it now names the real problem.
        """
        from assistant.backend.pipeline.tool_executor import (
            execute_edit_file,
            execute_write_file,
        )

        await execute_write_file({"path": "report.docx", "content": "Ada Lovelace"}, "1", "test")

        result = await execute_edit_file(
            {"path": "report.docx", "old_text": "Ada", "new_text": "Grace"},
            "1", "test",
        )
        assert not result.success
        assert "binary document" in result.error
        assert "write_file" in result.error

    @pytest.mark.asyncio
    async def test_edit_file_tolerates_whitespace_differences(self, store, stub_llm):
        """A collapsed blank line or trailing space must not defeat the edit.

        The most common reason an otherwise-correct old_text fails to match.
        """
        from assistant.backend.pipeline.tool_executor import (
            execute_edit_file,
            execute_read_file,
            execute_write_file,
        )

        await execute_write_file(
            {"path": "notes_ws.md", "content": "line one\n\nline two  \nline three"},
            "1", "test",
        )

        # old_text has the blank line collapsed to a single newline and no
        # trailing spaces; it must still match.
        result = await execute_edit_file(
            {"path": "notes_ws.md", "old_text": "line one\nline two", "new_text": "CHANGED"},
            "1", "test",
        )
        assert result.success, result.error

        read_result = await execute_read_file({"path": "notes_ws.md"}, "1", "test")
        assert read_result.data["content"].startswith("CHANGED")

    @pytest.mark.asyncio
    async def test_edit_file_failure_names_the_closest_region(self, store, stub_llm):
        """A no-match failure must hand the model something to correct with."""
        from assistant.backend.pipeline.tool_executor import (
            execute_edit_file,
            execute_write_file,
        )

        await execute_write_file(
            {"path": "closest.md", "content": "alpha beta\ngamma delta\nepsilon zeta"},
            "1", "test",
        )

        result = await execute_edit_file(
            {"path": "closest.md", "old_text": "gamma omega", "new_text": "X"},
            "1", "test",
        )
        assert not result.success
        assert "old_text not found" in result.error
        assert "Closest region" in result.error
        assert "gamma delta" in result.error

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
    async def test_large_csv_write_caps_row_frames(self, store, stub_llm, monkeypatch):
        """write_file CSV past CSV_MAX_ROW_FRAMES creates only the first `cap` rows.

        Regression: upload created `cap` row frames while the tool created NONE —
        a silent divergence. Both now create up to the cap; the rest stays on disk
        for read_file, so memory cannot explode per-row.
        """
        from assistant.backend.pipeline.tool_executor import execute_write_file, init_store

        user = await store.create_user("big_csv_user")
        init_store(store.db_path, embed_fn=stub_llm.embed, embedding_model="nomic-embed-text")
        monkeypatch.setattr(settings, "csv_max_row_frames", 2)
        csv_content = "name,email\n" + "".join(f"u{i},u{i}@x.com\n" for i in range(4))
        result = await execute_write_file(
            {"path": "big.csv", "content": csv_content}, str(user.id), "test"
        )
        assert result.success

        frame = await store.get_frame_by_name("file_big.csv")
        assert frame is not None
        row_count = await store.get_slot(frame.id, "row_count")
        columns = await store.get_slot(frame.id, "columns")
        assert row_count is not None and row_count.value == "4"
        assert columns is not None and "email" in columns.value

        # Exactly `cap` row frames — the rest stays on disk.
        associations = await store.get_all_associations_for_frame(frame.id)
        part_of = [a for a in associations if a.relation_type == "part_of"]
        assert len(part_of) == 2

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


class TestFileWriteParity:
    """Regression tests for assistant/experiments/file_write_parity/.

    The experiment found write_file could not produce a valid binary document
    (it wrote the string into a .docx and Word/its library refused it), and that
    memory diverged from upload for csv/json. These pin the fixes.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "ext",
        ["docx", "xlsx", "xls", "odt", "ods", "odp", "pptx", "pdf"],
    )
    async def test_write_file_produces_a_real_document(self, store, stub_llm, ext):
        """write_file writes bytes the owning library can open, not a string.

        Regression: `write_file` used `write_text`, so a `.docx` was the literal
        string and python-docx raised PackageNotFoundError.
        """
        import io

        from assistant.backend.pipeline.tool_executor import execute_write_file, init_store

        user = await store.create_user(f"parity_doc_{ext}")
        init_store(store.db_path, embed_fn=stub_llm.embed, embedding_model="nomic-embed-text")

        result = await execute_write_file(
            {"path": f"parity_doc.{ext}", "content": "Quarterly Report\nAda Lovelace.",
             "overwrite": True},
            str(user.id), "test",
        )
        assert result.success, result.error

        data = Path(f"/app/data/parity_doc.{ext}").read_bytes()
        # Zip-based Office/ODF formats must start with the PK signature; PDF with
        # %PDF; legacy xls with the OLE header. A text file fails all of these —
        # which is exactly the bug.
        if ext in ("docx", "xlsx", "odt", "ods", "odp", "pptx"):
            assert data[:2] == b"PK", f"{ext} is not a zip container"
        elif ext == "pdf":
            assert data[:4] == b"%PDF"
        elif ext == "xls":
            assert data[:4] == b"\xd0\xcf\x11\xe0"

        # And the owning library actually opens it.
        if ext == "docx":
            from docx import Document

            assert "Ada Lovelace" in "\n".join(
                p.text for p in Document(io.BytesIO(data)).paragraphs
            )
        elif ext == "pdf":
            from pypdf import PdfReader

            text = " ".join(
                (p.extract_text() or "") for p in PdfReader(io.BytesIO(data)).pages
            )
            assert "Ada Lovelace" in text

    @pytest.mark.asyncio
    async def test_write_file_refuses_unwritable_extension(self, store, stub_llm):
        """An extension with no writer is refused, not written as a fake file."""
        from assistant.backend.pipeline.tool_executor import execute_write_file

        result = await execute_write_file(
            {"path": "parity_script.py", "content": "print('hi')"}, "1", "test"
        )
        assert not result.success
        assert "no writer" in result.error

    @pytest.mark.asyncio
    async def test_write_file_csv_matches_upload_memory(self, store, stub_llm):
        """Agent-written and uploaded CSV produce the same frame slot set.

        Regression: upload wrote entity_* slots and row frames; write_file wrote
        neither, so identical bytes produced two different memories.
        """
        from assistant.backend.main import upload_file_to_memory
        from assistant.backend.pipeline.tool_executor import execute_write_file, init_store

        user = await store.create_user("parity_csv_user")
        init_store(store.db_path, embed_fn=stub_llm.embed, embedding_model="nomic-embed-text")
        csv = "name,role\nAda Lovelace,engineer\nGrace Hopper,admiral\n"

        await execute_write_file(
            {"path": "parity.csv", "content": csv}, str(user.id), "test"
        )
        written = await store.get_frame_by_name("file_parity.csv")

        uploaded_result = await upload_file_to_memory(
            "parity.csv", csv.encode(), "csv", store, user_id=user.id
        )
        uploaded = await store.get_frame(uploaded_result["frame_id"])

        async def slot_keys(frame_id):
            return sorted(s.key for s in await store.get_slots_for_frame(frame_id))

        async def safe_name(frame_id):
            slot = await store.get_slot(frame_id, "file_safe_name")
            return slot.value if slot else None

        assert written is not None and uploaded is not None
        assert await slot_keys(written.id) == await slot_keys(uploaded.id)
        # Values too, not just keys: file_safe_name must agree between the paths,
        # or read_file resolves one of them to the wrong on-disk location.
        assert await safe_name(written.id) == await safe_name(uploaded.id)
        # Entity slots now exist on the written frame (they did not before).
        assert any(k.startswith("entity_") for k in await slot_keys(written.id))

    @pytest.mark.asyncio
    async def test_write_file_json_matches_upload_memory(self, store, stub_llm):
        """Agent-written and uploaded JSON produce the same frame slot set."""
        import json

        from assistant.backend.main import upload_file_to_memory
        from assistant.backend.pipeline.tool_executor import execute_write_file, init_store

        user = await store.create_user("parity_json_user")
        init_store(store.db_path, embed_fn=stub_llm.embed, embedding_model="nomic-embed-text")
        payload = json.dumps({"lead": "Ada Lovelace", "project": "engine"})

        await execute_write_file(
            {"path": "parity.json", "content": payload}, str(user.id), "test"
        )
        written = await store.get_frame_by_name("file_parity.json")

        uploaded_result = await upload_file_to_memory(
            "parity.json", payload.encode(), "json", store, user_id=user.id
        )
        uploaded = await store.get_frame(uploaded_result["frame_id"])

        async def slot_keys(frame_id):
            return sorted(s.key for s in await store.get_slots_for_frame(frame_id))

        assert written is not None and uploaded is not None
        assert await slot_keys(written.id) == await slot_keys(uploaded.id)

    @pytest.mark.asyncio
    async def test_write_file_nested_path_keeps_relative_safe_name(self, store, stub_llm):
        """A nested file stores its sandbox-relative path, so read resolves it."""
        from assistant.backend.pipeline.tool_executor import execute_write_file, init_store

        user = await store.create_user("parity_nested_user")
        init_store(store.db_path, embed_fn=stub_llm.embed, embedding_model="nomic-embed-text")

        await execute_write_file(
            {"path": "parity_notes/weekly.md", "content": "hello"},
            str(user.id), "test",
        )
        frame = await store.get_frame_by_name("file_weekly.md")
        assert frame is not None, "frame is named from the basename"
        safe = await store.get_slot(frame.id, "file_safe_name")
        assert safe is not None
        assert safe.value == "parity_notes/weekly.md"

    @pytest.mark.asyncio
    async def test_write_file_requires_extension(self, store, stub_llm):
        """A path with no extension is refused (no writer can be chosen)."""
        from assistant.backend.pipeline.tool_executor import execute_write_file

        result = await execute_write_file({"path": "noext", "content": "x"}, "1", "test")
        assert not result.success
        assert "extension" in result.error

    @pytest.mark.asyncio
    async def test_agent_written_file_appears_in_files_list(self, client, store):
        """A write_file'd file shows up in GET /files/list.

        Regression: /files/list filtered to source_type=='file_upload', so every
        agent-written file (file_create) was invisible in the Files page even
        though it was on disk and in the graph. The user saw an empty list after
        asking the agent to save something.
        """
        from assistant.backend.pipeline.tool_executor import execute_write_file

        user_id = client.post("/users", params={"name": "lister"}).json()["id"]
        await execute_write_file(
            {"path": "visible_note.md", "content": "# hi"}, str(user_id), "sess"
        )

        listed = client.get("/files/list", params={"user_id": user_id}).json()
        names = {entry["file_name"] for entry in listed}
        assert "visible_note.md" in names
        assert any(e["source_type"] == "file_create" for e in listed)


class TestFileListMetadataAndDownload:
    """The Files page needs a size and extension per row, and a real download.

    Regression: `/files/list` returned only the frame, so the grid rendered the
    frame type as the extension and `NaN` as the size, and there was no route
    that served a file's original bytes -- the `/content` route decodes as UTF-8,
    which is garbage for a binary document.
    """

    @pytest.mark.asyncio
    async def test_list_includes_ext_and_size(self, client, store, tmp_path):
        body = "# Hello\n"
        test_file = tmp_path / "notes.md"
        test_file.write_text(body)
        with open(test_file, "rb") as f:
            resp = client.post(
                "/files/upload",
                files={"file": ("notes.md", f.read(), "text/markdown")},
            )
        assert resp.status_code == 200
        frame_id = resp.json()["frame_id"]

        entries = client.get("/files/list").json()
        entry = next(e for e in entries if e["id"] == frame_id)
        assert entry["file_ext"] == "md"
        assert entry["file_size"] == len(body.encode())

    @pytest.mark.asyncio
    async def test_download_returns_the_original_bytes(self, client, store, tmp_path):
        body = "# Hello\n"
        test_file = tmp_path / "notes.md"
        test_file.write_text(body)
        with open(test_file, "rb") as f:
            resp = client.post(
                "/files/upload",
                files={"file": ("notes.md", f.read(), "text/markdown")},
            )
        assert resp.status_code == 200
        frame_id = resp.json()["frame_id"]

        download = client.get(f"/files/{frame_id}/download")
        assert download.status_code == 200
        assert download.content == body.encode()
        disposition = download.headers["content-disposition"]
        assert "attachment" in disposition
        assert "notes.md" in disposition

    @pytest.mark.asyncio
    async def test_download_missing_file_is_404(self, client, store):
        assert client.get("/files/999999/download").status_code == 404

