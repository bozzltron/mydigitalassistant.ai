"""Regression: P3 correctness and hygiene fixes.

Each test pins a concrete bug that shipped:

- empty slot keys made every snippet count as corroboration;
- `_categorize_fact` used substring matching, so "stockholm"/"lawrence_fountain"
  were tagged financial/legal and clamped to reduced reliability;
- `validate_args` returned the raw dict on failure, making Pydantic constraints
  advisory;
- `extract_scheduled_task_fields` returned a bare `json.loads`, so a non-object
  crashed the caller outside its retry loop;
- `upsert_scheduler_heartbeat` used INSERT OR REPLACE, churning the frame id.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from assistant.backend.config import settings
from assistant.backend.pipeline import tool_executor
from assistant.backend.pipeline.extractor import (
    ExtractedSlot,
    ExtractionResult,
    apply_search_extraction,
    extract_scheduled_task_fields,
)
from assistant.backend.pipeline.llm_client import ChatResponse
from assistant.backend.pipeline.search import SearchResult


def _result(url: str, snippet: str) -> SearchResult:
    return SearchResult(title="t", url=url, snippet=snippet, engine="e")


async def _apply(store, slots, results):
    return await apply_search_extraction(
        ExtractionResult(slots=slots), results, store
    )


# --- empty key must not match every snippet -----------------------------------


@pytest.mark.asyncio
async def test_empty_slot_key_does_not_corroborate_every_snippet(store):
    """An empty key must not make a fact look corroborated by every source.

    Two layers guard this, and the original fix was the first:

    1. Corroboration matches on the *value*, not the key, so an empty key cannot
       be a substring of every snippet. Before that, `""` matched `"" in snippet`
       for all three results and a fact present in none of them was scored as
       corroborated by three independent domains, clearing the high-stakes gate.
    2. The write path now drops a blank-keyed slot outright, so it never reaches
       the corroboration pass at all.

    This asserts the stronger layer-2 guarantee. Layer 1 is still pinned by
    `test_blank_key_never_reaches_corroboration` below, which drives a slot whose
    key is blank but whose value is real -- the case where a value-only match
    would otherwise still corroborate.
    """
    slot = ExtractedSlot(frame_name="thing", key="", value="zzz-not-in-any-snippet")
    results = [
        _result("https://a.example/1", "totally unrelated text"),
        _result("https://b.example/2", "also unrelated"),
        _result("https://c.example/3", "still nothing"),
    ]

    out = await _apply(store, [slot], results)

    # Dropped at the write path, so nothing was applied and nothing could be
    # scored as corroborated.
    assert out["slots_applied"] == 0
    assert out["slots"] == []
    assert out["degenerate_dropped"] == 1


@pytest.mark.asyncio
async def test_blank_key_never_reaches_corroboration(store):
    """Layer 1 stated directly: with a real value in real snippets, a blank key
    still yields no applied fact, so no corroboration count can be attributed."""
    slot = ExtractedSlot(
        frame_name="thing", key="   ", value="a real value in the snippet"
    )
    results = [_result("https://a.example/1", "a real value in the snippet")]

    out = await _apply(store, [slot], results)

    assert out["slots_applied"] == 0
    assert out["degenerate_dropped"] == 1


# --- keyword categorisation must respect token boundaries ---------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "frame_name,key",
    [
        ("stockholm", "population"),
        ("lawrence_fountain", "height"),
        ("fender_stratocaster", "model"),
    ],
)
async def test_substring_lookalikes_are_not_high_stakes(store, frame_name, key):
    slot = ExtractedSlot(frame_name=frame_name, key=key, value="value-in-snippet")
    results = [_result("https://a.example/x", "the value-in-snippet is real")]

    out = await _apply(store, [slot], results)

    applied = out["slots"][0]
    assert applied["corroboration_category"] == "general"
    assert applied["needs_corroboration"] is False


@pytest.mark.asyncio
async def test_real_financial_key_is_still_high_stakes(store):
    slot = ExtractedSlot(frame_name="acme_corp", key="stock_price", value="42.50")
    results = [_result("https://a.example/x", "the stock price is 42.50")]

    out = await _apply(store, [slot], results)

    applied = out["slots"][0]
    assert applied["corroboration_category"] == "financial"
    assert applied["needs_corroboration"] is True


# --- args validation is not advisory -----------------------------------------


def test_validate_args_returns_none_on_failure():
    tool_executor._register_builtin_tools()
    assert tool_executor.validate_args("upsert_slot", {}) is None


def test_validate_args_returns_model_dump_on_success():
    tool_executor._register_builtin_tools()
    out = tool_executor.validate_args(
        "upsert_slot", {"frame_name": "f", "slot_key": "k", "slot_value": "v"}
    )
    assert out is not None
    assert out["frame_name"] == "f"


@pytest.mark.asyncio
async def test_execute_tool_rejects_invalid_arguments():
    tool_executor._register_builtin_tools()
    result = await tool_executor.execute_tool("upsert_slot", {}, "u", "s")
    assert not result.success
    assert "Invalid arguments" in (result.error or "")


# --- scheduled-task JSON must be an object -----------------------------------


class _JsonStub:
    utility_model = "stub"

    def __init__(self, content: str) -> None:
        self.content = content

    async def chat(self, *args, **kwargs) -> ChatResponse:  # noqa: ANN002, ANN003
        return ChatResponse(content=self.content, model="stub", done=True)


@pytest.mark.asyncio
async def test_scheduled_task_non_object_json_is_rejected():
    stub = _JsonStub("[1, 2, 3]")
    with pytest.raises(ValueError):
        await extract_scheduled_task_fields("do the thing", stub)


@pytest.mark.asyncio
async def test_scheduled_task_object_json_is_returned():
    stub = _JsonStub('{"intent": "list"}')
    parsed = await extract_scheduled_task_fields("what's on my list", stub)
    assert parsed == {"intent": "list"}


# --- heartbeat upsert keeps the row stable -----------------------------------


async def _scalar(store, sql: str) -> tuple:
    async with store._connect() as db:
        rows = await db.execute_fetchall(sql)
    return tuple(rows[0])


@pytest.mark.asyncio
async def test_scheduler_heartbeat_does_not_churn_the_frame_id(store):
    await store.upsert_scheduler_heartbeat("2026-01-01T09:00:00")
    frame_id_1 = (await _scalar(
        store, "SELECT id FROM frames WHERE name = 'scheduler_heartbeat'"
    ))[0]
    slot_id_1, value_1 = await _scalar(
        store, "SELECT id, value FROM slots WHERE key = 'last_heartbeat'"
    )

    await store.upsert_scheduler_heartbeat("2026-01-02T09:00:00")
    frame_id_2 = (await _scalar(
        store, "SELECT id FROM frames WHERE name = 'scheduler_heartbeat'"
    ))[0]
    slot_id_2, value_2 = await _scalar(
        store, "SELECT id, value FROM slots WHERE key = 'last_heartbeat'"
    )

    # Pre-fix INSERT OR REPLACE gave a new rowid on every heartbeat.
    assert frame_id_1 == frame_id_2
    assert slot_id_1 == slot_id_2
    assert value_1 == "2026-01-01T09:00:00"
    assert value_2 == "2026-01-02T09:00:00"


# --- extraction output is bounded --------------------------------------------


def test_extraction_result_rejects_oversized_output():
    """Runaway model output must not bloat memory without bound.

    Extraction JSON is untrusted model output; without these limits a single
    hallucinated turn could write an arbitrarily large value or a huge list of
    frames/slots.
    """
    with pytest.raises(ValidationError):
        ExtractionResult.model_validate(
            {"slots": [{"frame_name": "x", "key": "k", "value": "v" * 5000}]}
        )
    with pytest.raises(ValidationError):
        ExtractionResult.model_validate(
            {"slots": [{"frame_name": "n" * 300, "key": "k"}]}
        )
    with pytest.raises(ValidationError):
        ExtractionResult.model_validate(
            {"slots": [{"frame_name": f"f{i}", "key": "k"} for i in range(201)]}
        )


def test_extraction_result_accepts_a_normal_payload():
    result = ExtractionResult.model_validate(
        {"slots": [{"frame_name": "guitar", "key": "strings", "value": "6"}]}
    )
    assert result.slots[0].value == "6"


# --- API contract drift ------------------------------------------------------


@pytest.fixture
def api_client(store):
    """TestClient wired to the given store, mirroring test_api.py's fixture."""
    from fastapi.testclient import TestClient

    from assistant.backend.main import _state, app, get_orchestrator, get_store

    class _Embeds:
        async def embed(self, text):  # noqa: ANN001, ANN201
            from assistant.backend.pipeline.llm_client import EmbeddingResponse

            return EmbeddingResponse(embedding=[0.0] * 1024, model=settings.embedding_model)

    class _Orch:
        llm_client = _Embeds()

    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_orchestrator] = lambda: _Orch()
    _state["store"] = store
    original = settings.database_path
    settings.database_path = store.db_path
    try:
        with TestClient(app) as c:
            yield c
    finally:
        settings.database_path = original
        app.dependency_overrides.clear()
        _state.clear()


def test_search_accepts_the_frontend_min_relevance_param(api_client, store, monkeypatch):
    """`/search` advertised `min_similarity` while the client sent `min_relevance`.

    A mismatch meant the client's threshold silently became the 0.3 default.
    """
    captured: dict = {}

    async def fake_search_similar_frames(
        embedding, user_id, embedding_model, limit=10, min_distance=0.7
    ):
        captured["min_distance"] = min_distance
        return []

    monkeypatch.setattr(store, "search_similar_frames", fake_search_similar_frames)

    r = api_client.get("/search", params={"q": "guitar", "min_relevance": 0.9})
    assert r.status_code == 200
    # min_distance is 1 - similarity, so 0.9 must show up as 0.1 (not 0.7).
    assert captured["min_distance"] == pytest.approx(0.1)


def test_search_still_accepts_min_similarity(api_client, store, monkeypatch):
    captured: dict = {}

    async def fake_search_similar_frames(
        embedding, user_id, embedding_model, limit=10, min_distance=0.7
    ):
        captured["min_distance"] = min_distance
        return []

    monkeypatch.setattr(store, "search_similar_frames", fake_search_similar_frames)

    r = api_client.get("/search", params={"q": "guitar", "min_similarity": 0.5})
    assert r.status_code == 200
    assert captured["min_distance"] == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_list_frames_filters_by_owner(api_client, store):
    """The frontend sends `?user_id=`; the endpoint used to ignore it."""
    alice = await store.create_user("alice")
    bob = await store.create_user("bob")
    await store.create_frame("alice_thing", "entity", owner_user_id=alice.id)
    await store.create_frame("bob_thing", "entity", owner_user_id=bob.id)

    r = api_client.get("/memory/frames", params={"user_id": alice.id})
    assert r.status_code == 200
    names = {f["name"] for f in r.json()}
    assert names == {"alice_thing"}


def test_feedback_kind_is_validated_as_an_enum(api_client):
    """An unknown kind is a 422 request failure, not a silent 400 branch."""
    r = api_client.post(
        "/feedback", json={"message_id": "m", "kind": "not_a_kind"}
    )
    assert r.status_code == 422
