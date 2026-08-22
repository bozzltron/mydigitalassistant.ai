"""Regression tests for the assistant's own name (identity_name.full_name).

History: the user named the agent several times ("Luna", "Echo") and the name
never stuck, while the extractor mined the assistant's OWN generic
self-description ("my full name is cognitive digital assistant") into memory.
These tests pin the fixed behavior:

- only names stated by the USER may land on identity_name,
- the /assistant/name endpoint reflects memory and falls back cleanly.
"""


import pytest
from fastapi.testclient import TestClient

from assistant.backend.config import settings
from assistant.backend.db.schema import init_db
from assistant.backend.main import _state, app, get_orchestrator, get_store
from assistant.backend.memory.retrieval import Retriever
from assistant.backend.memory.store import MemoryStore
from assistant.backend.pipeline.extractor import (
    ExtractedSlot,
    drop_unstated_identity_slots,
    extract_and_apply,
    value_stated_by_user,
)
from assistant.backend.pipeline.orchestrator import Orchestrator, OrchestratorDeps

# ---- unit: value_stated_by_user ----


def test_value_stated_by_user_handles_quotes_and_case():
    assert value_stated_by_user("Echo", 'Your name is now "Echo".')
    assert value_stated_by_user("echo", "Let's call you ECHO from now on")
    assert value_stated_by_user("Digital Helper", "I will name you Digital Helper")


def test_value_stated_by_user_rejects_absent_and_substring_matches():
    assert not value_stated_by_user("cognitive digital assistant", "What is your name?")
    # "6" must not match inside "1965" (token-wise, not substring).
    assert not value_stated_by_user("6", "my guitar was made in 1965")
    assert value_stated_by_user("6", "it has 6 strings")
    assert not value_stated_by_user(None, "anything")
    assert not value_stated_by_user("", "anything")


# ---- unit: drop_unstated_identity_slots ----


def _slot(frame: str, key: str, value: str) -> ExtractedSlot:
    return ExtractedSlot(frame_name=frame, key=key, value=value)


def test_guard_drops_assistant_sourced_self_description():
    slots = [
        _slot("identity_name", "full_name", "cognitive digital assistant"),
        _slot("guitar", "strings", "6"),
    ]
    kept = drop_unstated_identity_slots(slots, user_message="What is your name?")
    # Identity claim came only from the assistant line -> dropped; others pass.
    assert [s.frame_name for s in kept] == ["guitar"]


def test_guard_keeps_user_assigned_name_even_if_assistant_echoes_it():
    slots = [
        _slot("identity_name", "full_name", "Echo"),
        _slot("identity_name", "full_name", "cognitive digital assistant"),
    ]
    kept = drop_unstated_identity_slots(
        slots, user_message='Your name is now "Echo"'
    )
    assert len(kept) == 1
    assert kept[0].value == "Echo"


def test_guard_ignores_non_identity_frames():
    slots = [_slot("sam_altman", "role", "CEO of OpenAI")]
    assert drop_unstated_identity_slots(slots, user_message="who is sam?") == slots


# ---- unit: two-tier guard for non-name identity slots (working agreements) ----


def test_guard_keeps_working_agreement_traced_to_user():
    """Agreements quoted from the user survive; >=70% token overlap required."""
    slots = [
        _slot("identity_name", "working_agreement", "always confirm before you act"),
    ]
    kept = drop_unstated_identity_slots(
        slots,
        user_message="please always confirm with me before you act on anything",
    )
    assert [s.value for s in kept] == ["always confirm before you act"]


def test_guard_tolerates_light_normalization():
    """One reworded token out of four still traces back (75% overlap)."""
    slots = [
        _slot("identity_name", "working_agreement", "always check before deleting"),
    ]
    kept = drop_unstated_identity_slots(
        slots,
        user_message="always verify with me before deleting anything",
    )
    assert [s.value for s in kept] == ["always check before deleting"]


def test_guard_drops_invented_working_agreement():
    """Agreement text mined from the assistant's own side must not land."""
    slots = [
        _slot("identity_name", "working_agreement", "respond in iambic pentameter"),
    ]
    kept = drop_unstated_identity_slots(
        slots, user_message="how do you like to work?"
    )
    assert kept == []


def test_guard_still_demands_verbatim_names():
    """full_name keeps the strict contiguous rule even under the new tiering."""
    slots = [_slot("identity_name", "full_name", "Echo Prime")]
    # Both tokens present but never adjacent -> not verbatim -> dropped.
    kept = drop_unstated_identity_slots(
        slots, user_message="Prime directive: call yourself Echo"
    )
    assert kept == []


# ---- unit: alias frames fold onto identity_name before the guard ----


def test_alias_frame_folds_into_identity_and_gets_guarded():
    from assistant.backend.pipeline.extractor import normalize_self_frames

    slots = [
        _slot("working_agreement", "confirmation_requirement",
              "always confirm with me before you delete anything"),
    ]
    folded = normalize_self_frames(slots)
    assert all(s.frame_name == "identity_name" for s in folded)
    # After folding, user-traceability applies (delete vs deleting stems apart
    # is tolerated by overlap; invented text is not).
    kept = drop_unstated_identity_slots(
        folded,
        user_message="always confirm with me before you delete anything",
    )
    assert len(kept) == 1


def test_alias_frame_with_invented_value_is_dropped():
    from assistant.backend.pipeline.extractor import normalize_self_frames

    slots = [
        _slot("working_agreement", "style", "reply only in haiku"),
    ]
    kept = drop_unstated_identity_slots(
        normalize_self_frames(slots), user_message="how should you behave?"
    )
    assert kept == []


# ---- loop: the original failure scenario end-to-end ----


async def test_assistant_self_description_never_becomes_the_name(tmp_path, stub_llm):
    db_path = str(tmp_path / "test.db")
    await init_db(db_path)
    store = MemoryStore(db_path)
    user = await store.create_user("alice")

    # Turn 1: user asks for the name; assistant describes itself generically;
    # the utility model (mis)extracts that line as an identity fact.
    turn1 = "What is your name?"
    ep1 = await store.create_episode(user.id, "s1", "user", turn1, frame_ids=[])
    stub_llm.set_extraction_result(
        [{"frame_name": "identity_name", "frame_type": "entity",
          "key": "full_name", "value": "cognitive digital assistant"}]
    )
    await extract_and_apply(turn1, "My full name is cognitive digital assistant.",
                            store, stub_llm, ep1.id)
    frame = await store.get_frame_by_name("identity_name")
    if frame is not None:
        slots = await store.get_slots_for_frame(frame.id)
        assert not any(s.key == "full_name" for s in slots)

    # Turn 2: the user assigns a name — this must stick.
    turn2 = 'Your name is now "Echo"'
    ep2 = await store.create_episode(user.id, "s1", "user", turn2, frame_ids=[])
    stub_llm.set_extraction_result(
        [{"frame_name": "identity_name", "frame_type": "entity",
          "key": "full_name", "value": "Echo"}]
    )
    await extract_and_apply(turn2, "Great, I'm Echo now!", store, stub_llm, ep2.id)
    frame = await store.get_frame_by_name("identity_name")
    assert frame is not None
    full_name = {s.key: s.value for s in await store.get_slots_for_frame(frame.id)}
    assert full_name["full_name"] == "Echo"


# ---- API: /assistant/name ----


@pytest.fixture
async def client(store, stub_llm, stub_search):
    """FastAPI TestClient with test dependencies wired in (same as test_api)."""
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
    settings.database_path = store.db_path
    try:
        with TestClient(app) as c:
            yield c
    finally:
        settings.database_path = original_db_path
        app.dependency_overrides.clear()
        _state.clear()


def test_assistant_name_defaults_when_no_memory(client):
    r = client.get("/assistant/name")
    assert r.status_code == 200
    assert r.json() == {"name": "Cognitive Assistant"}


def test_assistant_name_reflects_learned_name_after_chat_turn(client, stub_llm):
    user_id = client.post("/users", params={"name": "alice"}).json()["id"]
    stub_llm.set_extraction_result(
        [{"frame_name": "identity_name", "frame_type": "entity",
          "key": "full_name", "value": "Echo"}]
    )
    r = client.post("/chat", json={"user_id": user_id, "message": 'Your name is now "Echo"'})
    assert r.status_code == 200

    r = client.get("/assistant/name")
    assert r.status_code == 200
    assert r.json() == {"name": "Echo"}
