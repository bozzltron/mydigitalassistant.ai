import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from assistant.backend.pipeline.extractor import (
    ExtractedAssociation,
    ExtractedSlot,
    ExtractionResult,
    apply_extraction,
    extract_and_apply,
    extract_facts,
)


@pytest.mark.asyncio
async def test_extract_facts_parses_valid_json():
    mock_llm = AsyncMock()
    mock_llm.utility_model = "qwen2.5:3b"
    mock_llm.chat.return_value.content = json.dumps(
        {
            "slots": [
                {
                    "frame_name": "guitar",
                    "frame_type": "entity",
                    "key": "strings",
                    "value": "6",
                }
            ],
            "associations": [
                {"from_frame": "guitar", "to_frame": "music", "relation_type": "related_to"}
            ],
        }
    )

    result = await extract_facts("I have a guitar with 6 strings", "Cool!", mock_llm)
    assert len(result.slots) == 1
    assert result.slots[0].frame_name == "guitar"
    assert result.slots[0].value == "6"
    assert len(result.associations) == 1


@pytest.mark.asyncio
async def test_extract_facts_handles_empty_extraction():
    mock_llm = AsyncMock()
    mock_llm.utility_model = "qwen2.5:3b"
    mock_llm.chat.return_value.content = '{"slots": [], "associations": []}'

    result = await extract_facts("Hello", "Hi there!", mock_llm)
    assert result.slots == []
    assert result.associations == []


@pytest.mark.asyncio
async def test_extract_facts_retries_on_invalid_json():
    mock_llm = AsyncMock()
    mock_llm.utility_model = "qwen2.5:3b"
    mock_llm.chat.side_effect = [
        MagicMock(content="not valid json"),
        MagicMock(content='{"slots": [], "associations": []}'),
    ]

    result = await extract_facts("test", "test", mock_llm)
    assert result.slots == []
    assert mock_llm.chat.call_count == 2


@pytest.mark.asyncio
async def test_extract_facts_returns_empty_after_retry_failure():
    mock_llm = AsyncMock()
    mock_llm.utility_model = "qwen2.5:3b"
    mock_llm.chat.side_effect = [
        MagicMock(content="bad"),
        MagicMock(content="still bad"),
    ]

    result = await extract_facts("test", "test", mock_llm)
    assert result.slots == []
    assert result.associations == []
    assert mock_llm.chat.call_count == 2


@pytest.mark.asyncio
async def test_apply_extraction_creates_frames_and_slots(store):
    extraction = ExtractionResult(
        slots=[
            ExtractedSlot(frame_name="guitar", frame_type="entity", key="strings", value="6"),
            ExtractedSlot(frame_name="guitar", frame_type="entity", key="type", value="electric"),
        ],
        associations=[],
    )

    summary = await apply_extraction(extraction, store)
    assert summary["slots_applied"] == 2
    assert summary["associations_created"] == 0

    frame = await store.get_frame_by_name("guitar")
    assert frame is not None
    assert frame.type == "entity"

    slots = await store.get_slots_for_frame(frame.id)
    assert len(slots) == 2
    slot_dict = {s.key: s.value for s in slots}
    assert slot_dict["strings"] == "6"
    assert slot_dict["type"] == "electric"


@pytest.mark.asyncio
async def test_apply_extraction_creates_associations(store):
    extraction = ExtractionResult(
        slots=[
            ExtractedSlot(frame_name="guitar", frame_type="entity", key="strings", value="6"),
            ExtractedSlot(frame_name="music", frame_type="concept", key="genre", value="rock"),
        ],
        associations=[
            ExtractedAssociation(from_frame="guitar", to_frame="music", relation_type="related_to"),
        ],
    )

    summary = await apply_extraction(extraction, store)
    assert summary["slots_applied"] == 2
    assert summary["associations_created"] == 1

    guitar = await store.get_frame_by_name("guitar")
    music = await store.get_frame_by_name("music")
    assocs = await store.get_all_associations_for_frame(guitar.id)
    assert len(assocs) == 1
    assert assocs[0].to_frame_id == music.id


@pytest.mark.asyncio
async def test_apply_extraction_reuses_existing_frame(store):
    """If frame already exists, don't create a duplicate."""
    existing = await store.create_frame("guitar", "entity")

    extraction = ExtractionResult(
        slots=[ExtractedSlot(frame_name="guitar", frame_type="entity", key="strings", value="6")],
        associations=[],
    )
    await apply_extraction(extraction, store)

    all_guitars = [f for f in await store.list_frames() if f.name == "guitar"]
    assert len(all_guitars) == 1
    assert all_guitars[0].id == existing.id


@pytest.mark.asyncio
async def test_apply_extraction_skips_self_loops(store):
    extraction = ExtractionResult(
        slots=[ExtractedSlot(frame_name="guitar", frame_type="entity", key="strings", value="6")],
        associations=[
            ExtractedAssociation(
                from_frame="guitar", to_frame="guitar", relation_type="related_to"
            ),
        ],
    )
    summary = await apply_extraction(extraction, store)
    assert summary["associations_created"] == 0


@pytest.mark.asyncio
async def test_apply_extraction_handles_duplicate_association(store):
    """Re-applying the same association shouldn't fail."""
    extraction = ExtractionResult(
        slots=[
            ExtractedSlot(frame_name="guitar", frame_type="entity", key="strings", value="6"),
            ExtractedSlot(frame_name="music", frame_type="concept", key="genre", value="rock"),
        ],
        associations=[
            ExtractedAssociation(from_frame="guitar", to_frame="music", relation_type="related_to"),
        ],
    )
    await apply_extraction(extraction, store)
    summary = await apply_extraction(extraction, store)  # apply again
    assert summary["associations_created"] == 0


@pytest.mark.asyncio
async def test_apply_extraction_records_conflicts(store):
    """If a slot value contradicts existing, conflict is recorded."""
    e1 = ExtractionResult(
        slots=[ExtractedSlot(frame_name="guitar", frame_type="entity", key="strings", value="6")],
    )
    await apply_extraction(e1, store)

    e2 = ExtractionResult(
        slots=[ExtractedSlot(frame_name="guitar", frame_type="entity", key="strings", value="12")],
    )
    summary = await apply_extraction(e2, store)
    assert summary["conflicts_created"] == 1

    conflicts = await store.get_conflicts()
    assert len(conflicts) == 1
    assert conflicts[0].status == "auto_resolved"


@pytest.mark.asyncio
async def test_extract_and_apply_full_pipeline(store):
    mock_llm = AsyncMock()
    mock_llm.utility_model = "qwen2.5:3b"
    mock_llm.chat.return_value.content = json.dumps(
        {
            "slots": [
                {"frame_name": "pasta", "frame_type": "entity", "key": "type", "value": "spaghetti"}
            ],
            "associations": [],
        }
    )

    summary = await extract_and_apply("I love spaghetti", "That's great!", store, mock_llm)
    assert summary["slots_applied"] == 1

    frame = await store.get_frame_by_name("pasta")
    assert frame is not None


@pytest.mark.asyncio
async def test_extract_and_apply_handles_extraction_exception(store):
    mock_llm = AsyncMock()
    mock_llm.utility_model = "qwen2.5:3b"
    mock_llm.chat.side_effect = RuntimeError("LLM unavailable")

    result = await extract_and_apply("hello", "hi", store, mock_llm)
    assert result == {}


@pytest.mark.asyncio
async def test_apply_extraction_with_source_episode_id(store):
    user = await store.create_user("alice")
    episode = await store.create_episode(user.id, "s1", "user", "test", frame_ids=[])
    extraction = ExtractionResult(
        slots=[ExtractedSlot(frame_name="guitar", frame_type="entity", key="strings", value="6")],
        associations=[],
    )

    await apply_extraction(extraction, store, source_episode_id=episode.id)
    slot = await store.get_slot((await store.get_frame_by_name("guitar")).id, "strings")
    assert slot.source_episode_id == episode.id


@pytest.mark.asyncio
async def test_apply_extraction_infers_frame_type_from_association_only(store):
    extraction = ExtractionResult(
        slots=[],
        associations=[
            ExtractedAssociation(from_frame="guitar", to_frame="music", relation_type="related_to"),
        ],
    )
    summary = await apply_extraction(extraction, store)
    assert summary["associations_created"] == 1

    guitar = await store.get_frame_by_name("guitar")
    music = await store.get_frame_by_name("music")
    assert guitar.type == "entity"
    assert music.type == "entity"
