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


# ---- guard: a pronoun is not a name ----


def test_guard_drops_pronoun_as_name():
    """`you` appears in almost every message, but it is not the agent's name."""
    from assistant.backend.pipeline.extractor import _is_name_like

    assert not _is_name_like("you")
    assert not _is_name_like("The assistant")
    assert _is_name_like("Carl")
    assert _is_name_like("Carl Sagan")
    # An article plus a real name is still a name.
    assert _is_name_like("The Edge")

    slots = [_slot("identity_name", "full_name", "you")]
    kept = drop_unstated_identity_slots(slots, user_message="who are you?")
    assert kept == []


# ---- rename must beat an entrenched name (regression: the "Carl" freeze) ----


async def test_user_rename_overrides_entrenched_name(store, stub_llm):
    """A user-stated rename wins even against a 0.99 correction.

    Measured on the live brain: `full_name` held "Echo" from a user correction
    (source_reliability 0.99). Two conversational renames ("Carl Sagan", "Carl")
    were both resolved EXISTING_WINS because extraction writes reliability 0.5,
    so the name was frozen. The name is the user's to choose; it must stick.
    """
    from assistant.backend.pipeline.extractor import extract_and_apply

    frame = await store.create_frame("identity_name", "entity")
    await store.upsert_slot(
        frame.id,
        "full_name",
        "Echo",
        source_type="user_correction",
        source_reliability=0.99,
    )
    user = await store.create_user("alice")
    turn = 'Your name is now "Carl"'
    episode = await store.create_episode(user.id, "s1", "user", turn, frame_ids=[])
    stub_llm.set_extraction_result(
        [{"frame_name": "identity_name", "frame_type": "entity",
          "key": "full_name", "value": "Carl"}]
    )

    await extract_and_apply(turn, "I'm Carl now.", store, stub_llm, episode.id)

    slot = await store.get_slot(frame.id, "full_name")
    assert slot is not None
    assert slot.value == "Carl"


async def test_correction_rename_overrides_entrenched_name(store):
    """The correction path can also rename, and must beat the stored name."""
    from assistant.backend.pipeline.extractor import CorrectionResult, apply_correction

    frame = await store.create_frame("identity_name", "entity")
    await store.upsert_slot(
        frame.id,
        "full_name",
        "Echo",
        source_type="user_correction",
        source_reliability=0.99,
    )

    await apply_correction(
        CorrectionResult(
            frame_name="identity_name",
            slot_key="full_name",
            new_value="Carl",
        ),
        store,
    )

    slot = await store.get_slot(frame.id, "full_name")
    assert slot is not None
    assert slot.value == "Carl"


# ---- first-person self-naming belongs to the user, not the assistant ----
# Regression: on the live brain "My name is not Carl. I go by Boz." set
# identity_name.full_name = "Boz" -- the assistant took the user's own name.


def test_cross_check_drops_assistant_claim_on_the_users_name():
    from assistant.backend.pipeline.extractor import drop_user_duplicated_identity_slots

    slots = [
        _slot("user_identity", "full_name", "Boz"),
        _slot("identity_name", "full_name", "Boz"),
        _slot("identity_name", "working_agreement", "keep it short"),
    ]
    kept = drop_user_duplicated_identity_slots(slots)
    assert [(s.frame_name, s.key, s.value) for s in kept] == [
        ("user_identity", "full_name", "Boz"),
        ("identity_name", "working_agreement", "keep it short"),
    ]


def test_cross_check_leaves_unrelated_assistant_name_alone():
    from assistant.backend.pipeline.extractor import drop_user_duplicated_identity_slots

    slots = [
        _slot("user_identity", "full_name", "Boz"),
        _slot("identity_name", "full_name", "Carl"),
    ]
    assert drop_user_duplicated_identity_slots(slots) == slots


async def test_user_self_naming_does_not_rename_assistant(store, stub_llm):
    """The user introducing themselves routes to user_identity, never identity_name."""
    frame = await store.create_frame("identity_name", "entity")
    await store.upsert_slot(
        frame.id, "full_name", "Carl", source_type="user", source_reliability=1.0
    )
    user = await store.create_user("alice")
    turn = "My name is not Carl. I go by Boz."
    episode = await store.create_episode(user.id, "s1", "user", turn, frame_ids=[])
    stub_llm.set_extraction_result(
        [
            {"frame_name": "user_identity", "frame_type": "entity",
             "key": "full_name", "value": "Boz"},
            {"frame_name": "identity_name", "frame_type": "entity",
             "key": "full_name", "value": "Boz"},
        ]
    )

    await extract_and_apply(turn, "Nice to meet you, Boz.", store, stub_llm, episode.id)

    assistant_slot = await store.get_slot(frame.id, "full_name")
    assert assistant_slot is not None
    assert assistant_slot.value == "Carl"

    user_frame = await store.get_frame_by_name("user_identity")
    assert user_frame is not None
    user_slot = await store.get_slot(user_frame.id, "full_name")
    assert user_slot is not None
    assert user_slot.value == "Boz"


async def test_reserved_identity_frames_never_fuzzy_merge(store):
    """user_identity must not canonicalize onto identity_name by embedding similarity."""
    from assistant.backend.config import settings
    from assistant.backend.pipeline.extractor import resolve_or_create_frame

    identity = await store.create_frame("identity_name", "entity")
    await store.store_frame_embedding(
        identity.id, [1.0] + [0.0] * 767, settings.embedding_model
    )

    async def same_vector(_text: str) -> list[float]:
        # A vector identical to identity_name's: without the reserved-frame guard
        # the resolver would return identity.id for "user_identity".
        return [1.0] + [0.0] * 767

    new_id = await resolve_or_create_frame(
        store,
        "user_identity",
        "entity",
        embed_fn=same_vector,
        embedding_model=settings.embedding_model,
    )
    assert new_id != identity.id
    created = await store.get_frame_by_name("user_identity")
    assert created is not None
    assert created.id == new_id


async def test_search_extraction_never_creates_identity_frames(store):
    """A web page can never name the assistant or the user."""
    from assistant.backend.pipeline.extractor import (
        ExtractionResult,
        apply_search_extraction,
    )

    extraction = ExtractionResult(
        slots=[
            _slot("identity_name", "full_name", "Carl"),
            _slot("user_identity", "full_name", "Boz"),
            _slot("guitar", "strings", "6"),
        ]
    )
    summary = await apply_search_extraction(extraction, [], store)

    applied = {s["frame_name"] for s in summary["slots"]}
    assert "identity_name" not in applied
    assert "user_identity" not in applied
    assert await store.get_frame_by_name("user_identity") is None


# ---- correction routing: the model says who, the code says where ----
# Regression: on the live brain the correction path wrote `user_identity.name =
# "Carl"` (the assistant's name on the user's frame, unreadable key) while the
# user's name sat on identity_name. The two names were swapped because the
# correction writer used the model's frame/key verbatim.


async def test_correction_subject_assistant_renames_the_assistant(store):
    from assistant.backend.pipeline.extractor import CorrectionResult, apply_correction

    await apply_correction(
        CorrectionResult(
            frame_name="user_identity",  # model's frame is ignored
            slot_key="name",             # alias, normalized to full_name
            new_value="Carl",
            subject="assistant",
        ),
        store,
    )

    identity = await store.get_frame_by_name("identity_name")
    assert identity is not None
    slot = await store.get_slot(identity.id, "full_name")
    assert slot is not None
    assert slot.value == "Carl"
    # The user's frame was not created or written.
    assert await store.get_frame_by_name("user_identity") is None


async def test_correction_subject_user_sets_the_users_name(store):
    from assistant.backend.pipeline.extractor import CorrectionResult, apply_correction

    await apply_correction(
        CorrectionResult(
            frame_name="name",
            slot_key="full_name",
            new_value="Boz",
            subject="user",
        ),
        store,
    )

    identity = await store.get_frame_by_name("identity_name")
    assert identity is None
    user_frame = await store.get_frame_by_name("user_identity")
    assert user_frame is not None
    slot = await store.get_slot(user_frame.id, "full_name")
    assert slot is not None
    assert slot.value == "Boz"


async def test_correction_topic_is_unchanged_apart_from_name_alias(store):
    from assistant.backend.pipeline.extractor import CorrectionResult, apply_correction

    await apply_correction(
        CorrectionResult(frame_name="guitar", slot_key="strings", new_value="12"),
        store,
    )
    guitar = await store.get_frame_by_name("guitar")
    assert guitar is not None
    assert (await store.get_slot(guitar.id, "strings")).value == "12"


async def test_correction_assistant_name_alias_becomes_full_name(store):
    """A `name` correction about the assistant must land on the slot /assistant/name reads."""
    from assistant.backend.pipeline.extractor import CorrectionResult, apply_correction

    await apply_correction(
        CorrectionResult(
            frame_name="identity_name",
            slot_key="name",
            new_value="Echo",
            subject="assistant",
        ),
        store,
    )

    identity = await store.get_frame_by_name("identity_name")
    assert (await store.get_slot(identity.id, "full_name")).value == "Echo"
    # The alias did not create a second, unreadable slot.
    assert await store.get_slot(identity.id, "name") is None

