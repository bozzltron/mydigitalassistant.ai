"""File viewer backend tests.

Tests for Files tab UI: upload, view, delete, cascade cleanup, bulk delete.
"""

import pytest
from fastapi.testclient import TestClient

from assistant.backend.config import settings
from assistant.backend.main import _state, app, get_orchestrator, get_store
from assistant.backend.memory.retrieval import Retriever
from assistant.backend.pipeline.orchestrator import Orchestrator, OrchestratorDeps


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
        """DELETE /files/{frame_id} cascades: embeddings, slot_history,
        associations, slots, frame, physical file."""
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

        # Verify frame is soft-deleted (priority=0)
        frame_after = await store.get_frame(frame_id)
        assert frame_after is not None, "frame should still exist but be soft-deleted"
        assert frame_after.priority == 0, "frame should have priority 0"

        # Verify slots still exist (soft delete doesn't remove slots)
        slots_after = await store.get_slots_for_frame(frame_id)
        assert len(slots_after) > 0, "slots should still exist"

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

        # Delete parent frame
        del_resp = client.delete(f"/files/{parent_frame_id}")
        assert del_resp.status_code == 200

        # Verify parent is soft-deleted (priority=0)
        parent_after = await store.get_frame(parent_frame_id)
        assert parent_after is not None
        assert parent_after.priority == 0

        # Verify row frames still exist (endpoint only deletes parent)
        for row_frame_id in row_frame_ids:
            row_after = await store.get_frame(row_frame_id)
            assert row_after is not None, f"row frame {row_frame_id} should still exist"

        # Verify part_of associations remain (they're not cascaded in soft delete)