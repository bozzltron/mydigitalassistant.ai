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

    original_db_path = settings.database_path
    original_scheduler = settings.scheduler_enabled
    settings.database_path = store.db_path
    settings.scheduler_enabled = False
    try:
        with TestClient(app) as c:
            # Lifespan startup rebuilds real clients into _state; re-inject
            # stubs so endpoints reading _state directly (/correction) stay
            # hermetic regardless of whether host Ollama is running.
            _state["llm_client"] = stub_llm
            _state["search_tool"] = stub_search
            yield c
    finally:
        settings.database_path = original_db_path
        settings.scheduler_enabled = original_scheduler
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


def test_frame_slots_and_associations_endpoints(client, store):
    """Brain-page detail contract: per-frame slots + associations and the flat
    associations list are served in the shapes FrameDetail/BrainGraph expect."""
    import asyncio

    async def seed():
        guitar = await store.create_frame("guitar", "entity")
        genre = await store.create_frame("blues", "concept")
        await store.upsert_slot(guitar.id, "strings", "6")
        await store.create_association(guitar.id, genre.id, "plays", confidence=0.8)
        return guitar.id, genre.id

    guitar_id, genre_id = asyncio.run(seed())

    # Per-frame slots: only the guitar has any.
    r = client.get(f"/memory/frames/{guitar_id}/slots")
    assert r.status_code == 200
    slots = r.json()
    assert len(slots) == 1
    assert slots[0]["frame_id"] == guitar_id
    assert slots[0]["key"] == "strings"
    assert slots[0]["value"] == "6"
    assert client.get(f"/memory/frames/{genre_id}/slots").json() == []

    # Per-frame associations (incoming + outgoing).
    r = client.get(f"/memory/frames/{guitar_id}/associations")
    assert r.status_code == 200
    assocs = r.json()
    assert len(assocs) == 1
    assert assocs[0]["to_frame_id"] == genre_id
    assert assocs[0]["relation_type"] == "plays"
    assert assocs[0]["confidence"] == pytest.approx(0.8)

    # Flat association list feeds the graph edges.
    r = client.get("/memory/associations")
    assert r.status_code == 200
    assert len(r.json()) == 1

    # Missing frame: slots degrade to [], the frame endpoint 404s.
    assert client.get("/memory/frames/999999/slots").json() == []
    assert client.get("/memory/frames/999999").status_code == 404


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
    from pathlib import Path

    from assistant.backend.db.sqlcipher import connect

    asyncio.get_event_loop().run_until_complete(store.create_frame("guitar", "entity"))

    r = client.post("/db/backup")
    data = r.json()
    backup_path = data["backup_path"]

    assert Path(backup_path).exists()

    conn = connect(str(backup_path))
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


def test_health_reports_model_fleet(client):
    """Health endpoint should report the model fleet roles."""
    r = client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert "models" in data
    assert "chat" in data["models"]
    assert "utility" in data["models"]
    assert "embedding" in data["models"]
    assert "coder" in data["models"]
    assert "thinking_supported" in data


def test_chat_ui_returns_html(client):
    """GET /chat-ui should serve the web chat interface."""
    r = client.get("/chat-ui")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")
    assert "MyDigitalAssistant.ai" in r.text


def test_static_files_served(client):
    """The /static mount serves the tracked static assets.

    This used to skip unconditionally ("not available in test env") and so never
    ran anywhere (test-suite audit finding). It now checks a *tracked* asset,
    which is present in every environment, so the mount is actually verified.
    (The built `index.html` is a gitignored artifact and is covered by
    test_html_endpoints.py instead.)
    """
    r = client.get("/static/favicon.svg")
    assert r.status_code == 200
    assert "<svg" in r.text.lower()


def test_correction_endpoint_applies_correction(client, stub_llm):
    """POST /correction should parse natural-language correction and apply it."""
    stub_llm.set_extraction_result(
        slots=[{"frame_name": "guitar", "slot_key": "strings", "value": "12"}],
        associations=[],
    )

    r = client.post("/correction", json={
        "message_id": "test-msg-1",
        "episode_id": None,
        "correction_text": "Actually the guitar has 12 strings, not 6.",
    })
    assert r.status_code == 200
    data = r.json()
    assert data["slots_corrected"] == 1
    assert data["frame_name"] == "guitar"
    assert data["slot_key"] == "strings"
    assert data["new_value"] == "12"


async def test_correction_endpoint_stores_feedback_record(client, store, stub_llm):
    """POST /correction should create a feedback record for audit."""
    stub_llm.set_extraction_result(
        slots=[{"frame_name": "email", "slot_key": "address", "value": "new@example.com"}],
        associations=[],
    )

    r = client.post("/correction", json={
        "message_id": "test-msg-2",
        "episode_id": None,
        "correction_text": "My email is new@example.com.",
    })
    assert r.status_code == 200

    async with store._connect() as db:
        feedbacks = await db.execute_fetchall(
            "SELECT kind, comment FROM feedback WHERE message_id = ?",
            ("test-msg-2",),
        )
    assert len(feedbacks) == 1
    assert feedbacks[0][0] == "correction"
    assert "new@example.com" in feedbacks[0][1]


def test_correction_endpoint_rejects_unparseable_correction(client, stub_llm):
    """If extract_correction returns None, endpoint returns an error status."""
    stub_llm.set_extraction_result(slots=[], associations=[])

    r = client.post("/correction", json={
        "message_id": "test-msg-3",
        "correction_text": "That was wrong.",
    })
    assert r.status_code == 200
    data = r.json()
    assert data["slots_corrected"] == 0
    assert "Could not understand" in data["status"] or data["status"].startswith("Correction")


def test_correction_endpoint_resolves_episode_id(client, stub_llm):
    """Regression: /correction with episode_id must not crash resolving the
    latest episode for that session (store.db AttributeError, Phase 8 B1)."""
    u = client.post("/users", params={"name": "corrector"})
    uid = u.json()["id"]
    chat = client.post("/chat", json={"user_id": uid, "message": "Hello!"})
    session_id = chat.json()["session_id"]

    stub_llm.set_extraction_result(
        slots=[{"frame_name": "guitar", "slot_key": "strings", "value": "12"}],
        associations=[],
    )
    r = client.post("/correction", json={
        "message_id": "test-msg-ep",
        "episode_id": session_id,
        "correction_text": "Actually the guitar has 12 strings.",
    })
    assert r.status_code == 200
    assert r.json()["slots_corrected"] == 1


def test_correction_endpoint_requires_correction_text(client):
    """Empty correction_text should return 400."""
    r = client.post("/correction", json={
        "message_id": "test-msg-4",
        "correction_text": "",
    })
    assert r.status_code == 400


def test_correction_endpoint_assistant_name_routes_to_identity(store, stub_llm, client):
    """End-to-end: a rename correction with subject=assistant updates identity_name.

    Regression for the live-brain swap: "I'm not Carl, you are" was applied to
    user_identity under key `name`, while the user's own name sat on
    identity_name. The endpoint must route by subject before writing.
    """
    stub_llm.set_extraction_result(
        [{
            "frame_name": "identity_name",
            "slot_key": "name",       # alias; must normalize to full_name
            "value": "Carl",
            "subject": "assistant",
        }]
    )
    r = client.post("/correction", json={
        "message_id": "test-msg-name-1",
        "correction_text": "I'm not Carl, you are Carl.",
    })
    assert r.status_code == 200
    assert r.json()["slots_corrected"] == 1

    identity = client.get("/assistant/name")
    assert identity.json() == {"name": "Carl"}


def test_correction_endpoint_user_name_does_not_rename_assistant(store, stub_llm, client):
    """End-to-end: subject=user sets the user's frame and leaves the agent alone.

    Regression for the v0.8.0 report: "My name is not Carl. I go by Boz." set
    identity_name.full_name = "Boz" and the header renamed the assistant.
    """
    stub_llm.set_extraction_result(
        [{
            "frame_name": "user_identity",
            "slot_key": "full_name",
            "value": "Boz",
            "subject": "user",
        }]
    )
    r = client.post("/correction", json={
        "message_id": "test-msg-name-2",
        "correction_text": "My name is not Carl. I go by Boz.",
    })
    assert r.status_code == 200

    # The assistant's name is untouched (still the default, no identity frame).
    assert client.get("/assistant/name").json() == {"name": "Cognitive Assistant"}
    user_frame = client.get("/memory/frames/by-name/user_identity")
    assert user_frame.status_code == 200


async def test_feedback_endpoint_positive_creates_record(client, store):
    """POST /feedback with kind=positive records the reaction.

    With no episode_id there is no turn to reinforce, so the status is explicitly
    NOT "ok" -- see test_feedback_pipeline.py for why the endpoint no longer
    claims success for a reaction that changed no confidences. The audit row is
    still written either way, which is what this test is about.
    """
    r = client.post("/feedback", json={
        "message_id": "fb-msg-1",
        "episode_id": None,
        "kind": "positive",
        "comment": None,
    })
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "recorded_no_memory_touched"
    assert data["slots_updated"] == 0
    assert data["feedback"]["kind"] == "positive"

    async with store._connect() as db:
        rows = await db.execute_fetchall(
            "SELECT kind FROM feedback WHERE message_id = ?",
            ("fb-msg-1",),
        )
    assert len(rows) == 1
    assert rows[0][0] == "positive"


async def test_feedback_endpoint_negative_creates_record(client, store):
    """POST /feedback with kind=negative records the reaction (see above)."""
    r = client.post("/feedback", json={
        "message_id": "fb-msg-2",
        "episode_id": None,
        "kind": "negative",
        "comment": None,
    })
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "recorded_no_memory_touched"
    assert data["slots_updated"] == 0
    assert data["feedback"]["kind"] == "negative"


def test_feedback_endpoint_rejects_unknown_kind(client):
    """Unknown kind is a validated-enum request failure (422), not a 400."""
    r = client.post("/feedback", json={
        "message_id": "fb-msg-3",
        "kind": "unknown_kind",
    })
    assert r.status_code == 422



def test_chat_status_roundtrip(client, stub_llm):
    """turn_id tracks stage progress; status returns a valid shape then done."""
    stub_llm.set_extraction_result(slots=[], associations=[])
    u = client.post("/users", params={"name": "status"})
    uid = u.json()["id"]
    tid = "status-test-turn"
    r = client.post("/chat", json={
        "user_id": uid, "message": "hello there", "session_id": None,
        "turn_id": tid,
    })
    assert r.status_code == 200

    s = client.get(f"/chat/status/{tid}")
    assert s.status_code == 200
    body = s.json()
    assert set(body) == {"stage", "detail", "elapsed_s", "done"}
    assert body["done"] is True

    missing = client.get("/chat/status/never-seen")
    assert missing.status_code == 404


def test_session_messages_restore(client):
    """Conversation restore: session turns come back oldest-first, scoped to
    the requesting user — a refreshed page can rebuild the visible thread."""
    import asyncio

    u1 = client.post("/users", params={"name": "restoreA"})
    uid1 = u1.json()["id"]
    u2 = client.post("/users", params={"name": "restoreB"})
    uid2 = u2.json()["id"]
    store = _state["store"]

    async def seed():
        await store.create_episode(user_id=uid1, session_id="sess-restore",
                                   role="user", content="what is a quokka")
        await store.create_episode(user_id=uid1, session_id="sess-restore",
                                   role="assistant", content="A small marsupial.")
        # Another household member's turn in the same session must NOT leak.
        await store.create_episode(user_id=uid2, session_id="sess-restore",
                                   role="user", content="secret note from bob")

    asyncio.run(seed())

    r = client.get("/chat/session/sess-restore/messages", params={"user_id": uid1})
    assert r.status_code == 200
    msgs = r.json()
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert [m["content"] for m in msgs] == ["what is a quokka", "A small marsupial."]
    assert all("secret" not in m["content"] for m in msgs)

    r2 = client.get("/chat/session/sess-restore/messages", params={"user_id": uid2})
    assert [m["content"] for m in r2.json()] == ["secret note from bob"]

    empty = client.get("/chat/session/unknown-session/messages", params={"user_id": uid1})
    assert empty.json() == []


def test_conversation_download_is_a_plain_text_transcript(client):
    """A conversation exports as a .txt attachment: the whole thread, owner-scoped,
    with the loop's footers stripped."""
    import asyncio

    u1 = client.post("/users", params={"name": "downloadA"})
    uid1 = u1.json()["id"]
    u2 = client.post("/users", params={"name": "downloadB"})
    uid2 = u2.json()["id"]
    store = _state["store"]

    async def seed():
        await store.create_session("sess-dl", uid1, title="Album planning")
        await store.create_episode(user_id=uid1, session_id="sess-dl",
                                   role="user", content="what is the plan?")
        await store.create_episode(
            user_id=uid1, session_id="sess-dl", role="assistant",
            content="Here you go.\n\n**Sources:**\n- https://example.com",
        )
        # Another household member's turn in the same session must NOT leak.
        await store.create_episode(user_id=uid2, session_id="sess-dl",
                                   role="user", content="secret note from bob")

    asyncio.run(seed())

    r = client.get("/chat/session/sess-dl/download", params={"user_id": uid1})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    assert "attachment" in r.headers["content-disposition"]
    assert ".txt" in r.headers["content-disposition"]
    body = r.text
    assert "Conversation: Album planning" in body
    assert "Turns: 2" in body
    assert "what is the plan?" in body
    assert "Here you go." in body
    assert "Sources" not in body and "example.com" not in body
    assert "secret note from bob" not in body

    # Owner-scoped: the other member exports only their own turn, not a 404.
    r2 = client.get("/chat/session/sess-dl/download", params={"user_id": uid2})
    assert "secret note from bob" in r2.text
    assert "what is the plan?" not in r2.text

    # An unknown session is a 404, not an empty file.
    missing = client.get("/chat/session/nope/download", params={"user_id": uid1})
    assert missing.status_code == 404


def test_topic_search_finds_frames_by_keyword_and_semantic(client, stub_llm):
    """Topic transparency: /memory/search unions semantic hits with keyword
    matches and returns slots, associations, episodes, conflicts per frame."""
    stub_llm.set_extraction_result(slots=[], associations=[])
    import asyncio

    u = client.post("/users", params={"name": "topicsearcher"})
    uid = u.json()["id"]
    store = _state["store"]

    async def seed():
        f = await store.create_frame(
            name="quokka", type="entity", owner_user_id=uid,
            source_type="conversation",
        )
        slot, _conflict = await store.upsert_slot(
            frame_id=f.id, key="habitat", value="Western Australia",
        )
        episode = await store.create_episode(
            user_id=uid, session_id="sess-topic", role="user",
            content="tell me about the quokka", frame_ids=[f.id],
        )
        return f, slot, episode

    frame, slot, episode = asyncio.run(seed())
    # Semantic pass needs a stored embedding for the frame.
    asyncio.run(store.store_frame_embedding(
        frame.id, [0.1] * 768, settings.embedding_model))

    r = client.get("/memory/search", params={"q": "quokka"})
    assert r.status_code == 200
    body = r.json()
    assert body["query"] == "quokka"
    names = [m["frame"]["name"] for m in body["matches"]]
    assert "quokka" in names
    top = next(m for m in body["matches"] if m["frame"]["name"] == "quokka")
    assert any(s["key"] == "habitat" for s in top["slots"])
    assert any(e["id"] == episode.id for e in top["episodes"])

    # Slot-value substring also matches (keyword pass), even when the term
    # appears nowhere in the frame name.
    r2 = client.get("/memory/search", params={"q": "Western Australia"})
    names2 = [m["frame"]["name"] for m in r2.json()["matches"]]
    assert "quokka" in names2

    r3 = client.get("/memory/search", params={"q": "zzz-no-such-topic"})
    assert r3.status_code == 200
    assert r3.json()["matches"] == []

    empty_q = client.get("/memory/search", params={"q": "   "})
    assert empty_q.status_code == 400



@pytest.mark.asyncio
async def test_correction_endpoint_basic(client, stub_llm, store):
    """POST /correction basic flow - verify correction pipeline runs.

    Regression test: ensure the correction pipeline doesn't crash and
    basic flow works (parse → validate → apply → response).
    """
    stub_llm.set_extraction_result(
        slots=[{"frame_name": "guitar", "slot_key": "strings", "value": "12"}],
        associations=[],
    )

    r = client.post("/correction", json={
        "message_id": "test-basic-1",
        "episode_id": None,
        "correction_text": "The guitar has 12 strings.",
    })
    assert r.status_code == 200
    data = r.json()
    assert data["slots_corrected"] == 1
    assert data["frame_name"] == "guitar"
    assert data["slot_key"] == "strings"
    assert data["new_value"] == "12"

    # Correction should be recorded as feedback
    async with store._connect() as db:
        feedbacks = await db.execute_fetchall(
            "SELECT kind, comment FROM feedback WHERE message_id = ?",
            ("test-basic-1",),
        )
    assert len(feedbacks) == 1
    assert feedbacks[0][0] == "correction"
