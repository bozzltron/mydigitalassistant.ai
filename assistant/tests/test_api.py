import pytest
from fastapi.testclient import TestClient

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

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.clear()
    _state.clear()


def test_health_endpoint(client):
    r = client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"


def test_create_user(client):
    r = client.post("/users", params={"name": "alice"})
    assert r.status_code == 200
    data = r.json()
    assert data["name"] == "alice"
    assert "id" in data


def test_create_user_duplicate_returns_409(client):
    client.post("/users", params={"name": "alice"})
    r = client.post("/users", params={"name": "alice"})
    assert r.status_code == 409


def test_list_users(client):
    client.post("/users", params={"name": "alice"})
    client.post("/users", params={"name": "bob"})
    r = client.get("/users")
    assert r.status_code == 200
    names = {u["name"] for u in r.json()}
    assert names == {"alice", "bob"}


def test_get_user(client):
    r = client.post("/users", params={"name": "alice"})
    user_id = r.json()["id"]
    r = client.get(f"/users/{user_id}")
    assert r.status_code == 200
    assert r.json()["name"] == "alice"


def test_get_user_not_found(client):
    r = client.get("/users/9999")
    assert r.status_code == 404


def test_chat_endpoint_returns_response(client):
    r = client.post("/users", params={"name": "alice"})
    user_id = r.json()["id"]

    r = client.post("/chat", json={"user_id": user_id, "message": "Hello!"})
    assert r.status_code == 200
    data = r.json()
    assert "response" in data
    assert "session_id" in data
    assert data["task_type"] in ("functional", "introspective")


def test_chat_creates_episodes(client):
    r = client.post("/users", params={"name": "alice"})
    user_id = r.json()["id"]

    client.post("/chat", json={"user_id": user_id, "message": "Hello there!"})

    r = client.get(f"/users/{user_id}/episodes")
    episodes = r.json()
    assert len(episodes) >= 2  # user + assistant
    roles = {ep["role"] for ep in episodes}
    assert roles == {"user", "assistant"}


def test_chat_session_persists_across_calls(client):
    r = client.post("/users", params={"name": "alice"})
    user_id = r.json()["id"]

    r1 = client.post("/chat", json={"user_id": user_id, "message": "Hi"})
    session_id = r1.json()["session_id"]

    r2 = client.post(
        "/chat",
        json={"user_id": user_id, "message": "How does X work?", "session_id": session_id},
    )
    assert r2.json()["session_id"] == session_id


def test_list_frames_empty(client):
    r = client.get("/memory/frames")
    assert r.status_code == 200
    assert r.json() == []


def test_get_frame_by_name_not_found(client):
    r = client.get("/memory/frames/by-name/nonexistent")
    assert r.status_code == 404


def test_list_conflicts_empty(client):
    r = client.get("/memory/conflicts")
    assert r.status_code == 200
    assert r.json() == []


def test_resolve_conflict_endpoint(client, store):
    """End-to-end: create a conflict via the store, resolve via the API."""
    import asyncio

    loop = asyncio.get_event_loop()
    f = loop.run_until_complete(store.create_frame("guitar", "entity"))
    loop.run_until_complete(store.upsert_slot(f.id, "strings", "6"))
    loop.run_until_complete(store.upsert_slot(f.id, "strings", "12"))

    conflicts = loop.run_until_complete(store.get_conflicts())
    assert len(conflicts) == 1
    conflict_id = conflicts[0].id

    r = client.post(f"/memory/conflicts/{conflict_id}/resolve", params={"value": "7"})
    assert r.status_code == 200

    slot = loop.run_until_complete(store.get_slot(f.id, "strings"))
    assert slot.value == "7"


def test_db_backup_creates_file(client, store):
    """Backup endpoint creates a backup file."""
    r = client.post("/db/backup")
    assert r.status_code == 200
    data = r.json()
    assert "backup_filename" in data
    assert data["backup_filename"].startswith("backup-")
    assert data["backup_filename"].endswith(".db")


def test_db_backup_preserves_data(client, store):
    """Backup should contain the same data as the original."""
    import asyncio
    import sqlite3
    from pathlib import Path

    asyncio.get_event_loop().run_until_complete(store.create_frame("guitar", "entity"))

    r = client.post("/db/backup")
    data = r.json()
    backup_path = data["backup_path"]

    assert Path(backup_path).exists()

    conn = sqlite3.connect(str(backup_path))
    row = conn.execute("SELECT name FROM frames WHERE name = ?", ("guitar",)).fetchone()
    assert row is not None
    assert row[0] == "guitar"
    conn.close()


def test_list_backups_empty(client, store):
    # Clean any existing backups from prior tests
    from pathlib import Path

    from assistant.backend.config import settings

    data_dir = Path(settings.database_path).parent
    for b in data_dir.glob("backup-*.db"):
        b.unlink()

    r = client.get("/db/backups")
    assert r.status_code == 200
    assert r.json()["backups"] == []


def test_list_backups_after_backup(client, store):
    client.post("/db/backup")
    r = client.get("/db/backups")
    assert r.status_code == 200
    backups = r.json()["backups"]
    assert len(backups) >= 1


def test_restore_backup(client, store):
    """Restore should overwrite the DB."""
    import asyncio

    loop = asyncio.get_event_loop()
    loop.run_until_complete(store.create_frame("guitar", "entity"))

    r = client.post("/db/backup")
    backup_filename = r.json()["backup_filename"]

    loop.run_until_complete(store.create_frame("pasta", "entity"))

    r = client.post("/db/restore", params={"backup_filename": backup_filename})
    assert r.status_code == 200

    frames = loop.run_until_complete(store.list_frames())
    names = {f.name for f in frames}
    assert "guitar" in names
    assert "pasta" not in names


def test_restore_nonexistent_backup_returns_404(client, store):
    r = client.post("/db/restore", params={"backup_filename": "nonexistent.db"})
    assert r.status_code == 404


def test_restore_path_traversal_blocked(client, store):
    """Restore should refuse files outside the data directory."""
    r = client.post("/db/restore", params={"backup_filename": "../../../etc/passwd"})
    assert r.status_code == 400


def test_health_includes_reasoning_model(client):
    """Health endpoint should include reasoning_model."""
    r = client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert "reasoning_model" in data


def test_chat_ui_returns_html(client):
    """GET /chat-ui should serve the web chat interface."""
    r = client.get("/chat-ui")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")
    assert "Cognitive Assistant" in r.text


def test_static_files_served(client):
    """Static files including marked.min.js should be served."""
    r = client.get("/static/marked.min.js")
    assert r.status_code == 200
    assert "application/javascript" in r.headers.get("content-type", "")
