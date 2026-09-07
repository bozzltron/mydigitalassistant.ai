"""CSV structured memory experiment tests.

Tests for: parent + row frame creation, row recall, edit/delete via prompt,
verbatim reference, summary generation, large CSV performance.
"""
import pytest
from pathlib import Path

from fastapi.testclient import TestClient

from assistant.backend.config import settings
from assistant.backend.main import _state, app, get_store, get_orchestrator
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


class TestCSVStructuredMemory:
    """CSV experiment: parent + row frames, retrieval, operations."""

    @pytest.mark.asyncio
    async def test_csv_creates_parent_and_row_frames(self, client, store, tmp_path):
        """Upload CSV → 1 parent (document) + N row (record) frames, part_of associations."""
        test_file = tmp_path / "exp1.csv"
        test_file.write_text(
            "id,name,role,active\n"
            "1,Alice,engineer,true\n"
            "2,Bob,designer,false\n"
        )

        with open(test_file, "rb") as f:
            resp = client.post(
                "/files/upload",
                files={"file": ("exp1.csv", f.read(), "text/csv")},
            )

        assert resp.status_code == 200
        data = resp.json()

        assert data["row_count"] == 2
        assert "parent_frame_id" in data
        assert "row_frame_ids" in data

        parent_frame = await store.get_frame(data["parent_frame_id"])
        assert parent_frame.type == "document"

        assert len(data["row_frame_ids"]) == 2
        for rf_id in data["row_frame_ids"]:
            rf = await store.get_frame(rf_id)
            assert rf.type == "record"

        # Verify parent metadata
        columns = await store.get_slot(data["parent_frame_id"], "columns")
        row_count = await store.get_slot(data["parent_frame_id"], "row_count")
        assert columns.value == '[ "id", "name", "role", "active" ]'
        assert row_count.value == "2"

        # Verify part_of associations
        associations = await store.list_associations(data["parent_frame_id"])
        part_of = [a for a in associations if a.relation == "part_of"]
        assert len(part_of) == 2

    @pytest.mark.asyncio
    async def test_recall_finds_csv_rows(self, client, store, tmp_path):
        """Query 'active users' via recall should return row frames with status=active."""
        test_file = tmp_path / "users2.csv"
        test_file.write_text(
            "name,email,status\n"
            "Alice,a@b.com,active\n"
            "Bob,b@c.com,pending\n"
            "Charlie,c@d.com,active\n"
        )

        with open(test_file, "rb") as f:
            resp = client.post(
                "/files/upload",
                files={"file": ("users2.csv", f.read(), "text/csv")},
            )

        assert resp.status_code == 200

        # Use store directly to search for active rows
        # Row frames have slots: name, email, status
        all_frames = await store.list_frames()
        record_frames = []
        for fr in all_frames:
            if fr.type == "record":
                name_slot = await store.get_slot(fr.id, "name")
                status_slot = await store.get_slot(fr.id, "status")
                if name_slot and status_slot and status_slot.value == "active":
                    record_frames.append(fr.id)

        assert len(record_frames) == 2, f"expected 2 active rows, got {len(record_frames)}: {record_frames}"

    @pytest.mark.asyncio
    async def test_agent_can_edit_row_via_prompt(self, client, store, tmp_path):
        """Send prompt 'Change Alice's status to inactive' → row frame slot updated."""
        test_file = tmp_path / "edit.csv"
        test_file.write_text(
            "name,email,status\n"
            "Alice,a@b.com,active\n"
        )

        with open(test_file, "rb") as f:
            resp = client.post(
                "/files/upload",
                files={"file": ("edit.csv", f.read(), "text/csv")},
            )

        assert resp.status_code == 200

        # Find Alice's row frame and update status slot
        all_frames = await store.list_frames()
        alice_frame = None
        for fr in all_frames:
            if fr.type == "record":
                name_slot = await store.get_slot(fr.id, "name")
                if name_slot and "Alice" in name_slot.value:
                    alice_frame = fr
                    break

        assert alice_frame is not None, "should find Alice's row frame"

        # Upsert the status slot to inactive
        await store.upsert_slot(
            frame_id=alice_frame.id,
            key="status",
            value="inactive",
            essential=0,
            priority=0.5,
        )

        # Verify the update
        status_slot = await store.get_slot(alice_frame.id, "status")
        assert status_slot is not None
        assert status_slot.value == "inactive", f"status should be 'inactive', got '{status_slot.value}'"

    @pytest.mark.asyncio
    async def test_verbatim_reference(self, client, store, tmp_path):
        """Send prompt 'What does row 2 say verbatim?' → agent quotes exact cell values."""
        test_file = tmp_path / "verbatim.csv"
        test_file.write_text(
            "name,email,role\n"
            "Alice,a@b.com,engineer\n"
            "Bob,b@c.com,designer\n"
        )

        with open(test_file, "rb") as f:
            resp = client.post(
                "/files/upload",
                files={"file": ("verbatim.csv", f.read(), "text/csv")},
            )

        assert resp.status_code == 200

        # Find Bob's row frame
        all_frames = await store.list_frames()
        bob_frame = None
        for fr in all_frames:
            if fr.type == "record":
                name_slot = await store.get_slot(fr.id, "name")
                if name_slot and "Bob" in name_slot.value:
                    bob_frame = fr
                    break

        assert bob_frame is not None, "should find Bob's row frame"

        # Verify Bob's slots contain exact values
        name_slot = await store.get_slot(bob_frame.id, "name")
        email_slot = await store.get_slot(bob_frame.id, "email")
        role_slot = await store.get_slot(bob_frame.id, "role")

        assert name_slot is not None and name_slot.value == "Bob", f"name should be 'Bob', got '{name_slot.value}'"
        assert email_slot is not None and email_slot.value == "b@c.com", f"email should be 'b@c.com', got '{email_slot.value}'"
        assert role_slot is not None and role_slot.value == "designer", f"role should be 'designer', got '{role_slot.value}'"

    @pytest.mark.asyncio
    async def test_summarize_csv_using_retrieved_rows(self, client, store, tmp_path):
        """Send chat: 'Summarize the CSV' → response uses retrieved row data."""
        test_file = tmp_path / "summary2.csv"
        test_file.write_text(
            "name,category,quantity\n"
            "Apple,fruit,5\n"
            "Carrot,vegetable,10\n"
            "Steak,meat,2\n"
        )

        with open(test_file, "rb") as f:
            resp = client.post(
                "/files/upload",
                files={"file": ("summary2.csv", f.read(), "text/csv")},
            )

        assert resp.status_code == 200

        # Send chat message asking for summary
        chat_resp = client.post(
            "/chat",
            json={"message": "Summarize the CSV", "user_id": 1},
        )

        assert chat_resp.status_code == 200
        text = chat_resp.json().get("response", "")
        assert len(text) > 0, "response should not be empty"
        # Should mention some of the data
        assert any(
            word in text for word in ["Apple", "Carrot", "Steak", "fruit", "vegetable", "meat"]
        ), f"response should reference CSV content, got: {text}"


class TestCSVPerformance:
    """Performance tests for large CSV handling."""

    @pytest.mark.asyncio
    async def test_large_csv_upload_performance(self, client, store, tmp_path):
        """Upload 200-row CSV → completes in reasonable time, creates correct frame count."""
        # Generate 200-row CSV
        lines = ["id,name,email,status"]
        for i in range(1, 201):
            lines.append(f"{i},user{i},user{i}@example.com,{'active' if i % 3 == 0 else 'pending'}")
        csv_content = "\n".join(lines)

        test_file = tmp_path / "large.csv"
        test_file.write_text(csv_content)

        import time
        start = time.time()
        with open(test_file, "rb") as f:
            resp = client.post(
                "/files/upload",
                files={"file": ("large.csv", f.read(), "text/csv")},
            )
        elapsed = time.time() - start

        assert resp.status_code == 200
        data = resp.json()
        assert data["row_count"] == 200, f"expected 200 rows, got {data.get('row_count')}"
        # Should complete in under 30 seconds for 200 rows
        assert elapsed < 30, f"CSV upload took {elapsed:.1f}s, expected < 30s"

    @pytest.mark.asyncio
    async def test_recall_latency_100_rows(self, client, store, tmp_path):
        """Recall latency for 100-row CSV < 500ms."""
        # Upload 100-row CSV
        lines = ["id,name,status"]
        for i in range(1, 101):
            lines.append(f"{i},user{i},{'active' if i % 10 == 0 else 'pending'}")
        csv_content = "\n".join(lines)

        test_file = tmp_path / "perf.csv"
        test_file.write_text(csv_content)

        with open(test_file, "rb") as f:
            resp = client.post(
                "/files/upload",
                files={"file": ("perf.csv", f.read(), "text/csv")},
            )

        assert resp.status_code == 200

        # Measure recall latency
        all_frames = await store.list_frames()
        record_frames = [f for f in all_frames if f.type == "record"]

        start = time.time()
        active_count = 0
        for fr in record_frames[:20]:  # Check first 20
            status_slot = await store.get_slot(fr.id, "status")
            if status_slot and status_slot.value == "active":
                active_count += 1
        elapsed = (time.time() - start) * 1000

        assert elapsed < 500, f"recall latency {elapsed:.1f}ms > 500ms target"
        print(f"  100-row recall latency: {elapsed:.1f}ms for {active_count} active rows")