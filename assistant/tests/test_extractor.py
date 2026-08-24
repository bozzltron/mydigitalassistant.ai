import json
from unittest.mock import AsyncMock, MagicMock

from assistant.backend.pipeline.extractor import (
    CorrectionResult,
    ExtractedAssociation,
    ExtractedSlot,
    ExtractionResult,
    apply_correction,
    apply_extraction,
    apply_search_extraction,
    extract_and_apply,
    extract_correction,
    extract_facts,
    extract_facts_from_search,
    filter_duplicate_slots,
    validate_correction,
)


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


async def test_extract_facts_handles_empty_extraction():
    mock_llm = AsyncMock()
    mock_llm.utility_model = "qwen2.5:3b"
    mock_llm.chat.return_value.content = '{"slots": [], "associations": []}'

    result = await extract_facts("Hello", "Hi there!", mock_llm)
    assert result.slots == []
    assert result.associations == []


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


async def test_apply_extraction_handles_duplicate_association(store):
    """Re-applying the same association bumps confidence instead of duplicating."""
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
    assert summary["associations_created"] == 1

    guitar = await store.get_frame_by_name("guitar")
    edges = await store.get_all_associations_for_frame(guitar.id)
    assert len(edges) == 1
    assert edges[0].confidence > 0.5


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


async def test_extract_and_apply_handles_extraction_exception(store):
    mock_llm = AsyncMock()
    mock_llm.utility_model = "qwen2.5:3b"
    mock_llm.chat.side_effect = RuntimeError("LLM unavailable")

    result = await extract_and_apply("hello", "hi", store, mock_llm)
    assert result == {}


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


async def test_extract_facts_from_search_parses_results():
    mock_llm = AsyncMock()
    mock_llm.utility_model = "qwen2.5:3b"
    mock_llm.chat.return_value.content = json.dumps(
        {
            "slots": [
                {
                    "frame_name": "capybara",
                    "frame_type": "entity",
                    "key": "scientific_name",
                    "value": "Hydrochoerus hydrochaeris",
                },
                {
                    "frame_name": "capybara",
                    "frame_type": "entity",
                    "key": "size",
                    "value": "large rodent",
                },
            ],
            "associations": [],
        }
    )

    search_results = [
        MagicMock(
            url="https://en.wikipedia.org/wiki/Capybara",
            snippet="The capybara is a large rodent.",
            title="Capybara - Wikipedia",
        ),
        MagicMock(
            url="https://nationalgeographic.com/capybara",
            snippet="Capybaras are from South America.",
            title="Capybara - Nat Geo",
        ),
    ]

    result = await extract_facts_from_search("What is a capybara?", search_results, mock_llm)

    assert len(result.slots) == 2
    assert result.slots[0].frame_name == "capybara"
    assert result.slots[0].value == "Hydrochoerus hydrochaeris"


async def test_extract_facts_from_search_empty_results():
    mock_llm = AsyncMock()
    result = await extract_facts_from_search("query", [], mock_llm)
    assert result.slots == []
    assert result.associations == []


async def test_apply_search_extraction_deduplicates_by_content(store):
    search_results = [
        MagicMock(
            url="https://example.com/a",
            snippet="Capybara is a large rodent.",
            title="A",
        ),
        MagicMock(
            url="https://example.com/b",
            snippet="Capybara is a large rodent.",
            title="B",
        ),
    ]

    mock_llm = AsyncMock()
    mock_llm.utility_model = "qwen2.5:3b"
    mock_llm.chat.return_value.content = json.dumps(
        {
            "slots": [
                {
                    "frame_name": "capybara",
                    "frame_type": "entity",
                    "key": "size",
                    "value": "large rodent",
                },
            ],
            "associations": [],
        }
    )

    extraction = await extract_facts_from_search("What is a capybara?", search_results, mock_llm)
    await apply_search_extraction(extraction, search_results, store)

    frame = await store.get_frame_by_name("capybara")
    assert frame is not None
    slots = await store.get_slots_for_frame(frame.id)
    assert len(slots) == 1


async def test_apply_search_extraction_sets_source_type_and_url(store):
    search_results = [
        MagicMock(
            url="https://en.wikipedia.org/wiki/Capybara",
            snippet="The capybara is the largest living rodent.",
            title="Capybara",
        ),
    ]

    mock_llm = AsyncMock()
    mock_llm.utility_model = "qwen2.5:3b"
    mock_llm.chat.return_value.content = json.dumps(
        {
            "slots": [
                {
                    "frame_name": "capybara",
                    "frame_type": "entity",
                    "key": "description",
                    "value": "largest living rodent",
                },
            ],
            "associations": [],
        }
    )

    extraction = await extract_facts_from_search("What is a capybara?", search_results, mock_llm)
    await apply_search_extraction(extraction, search_results, store)

    frame = await store.get_frame_by_name("capybara")
    assert frame is not None
    slots = await store.get_slots_for_frame(frame.id)
    assert any(s.key == "description" and s.value == "largest living rodent" for s in slots)
    assert slots[0].source_url == "https://en.wikipedia.org/wiki/Capybara"
    assert slots[0].source_type == "search"


async def test_validate_correction_corroborated_by_search(store):
    """When search results contain the new value, validation marks it corroborated."""
    from unittest.mock import AsyncMock

    frame = await store.create_frame("guitar", "entity")
    await store.upsert_slot(frame.id, "strings", "6")

    mock_search = AsyncMock()
    mock_search.search.return_value = [
        MagicMock(url="https://example.com/strings", snippet="12-string guitars are common"),
    ]

    correction = CorrectionResult(
        frame_name="guitar", slot_key="strings", new_value="12"
    )
    validation = await validate_correction(
        correction=correction,
        current_value="6",
        store=store,
        search_tool=mock_search,
        llm_client=AsyncMock(),
    )

    assert validation.corroborated is True
    assert validation.contradicted is False
    assert "12" in validation.summary


async def test_validate_correction_contradicted_by_search(store):
    """When search results support the old value, validation rejects the correction."""
    from unittest.mock import AsyncMock

    frame = await store.create_frame("guitar", "entity")
    await store.upsert_slot(frame.id, "strings", "6")

    mock_search = AsyncMock()
    mock_search.search.return_value = [
        MagicMock(url="https://example.com/strings", snippet="Most guitars have 6 strings"),
    ]

    correction = CorrectionResult(
        frame_name="guitar", slot_key="strings", new_value="12"
    )
    validation = await validate_correction(
        correction=correction,
        current_value="6",
        store=store,
        search_tool=mock_search,
        llm_client=AsyncMock(),
    )

    assert validation.corroborated is False
    assert validation.contradicted is True
    assert "6" in validation.summary
    assert "12" in validation.summary


async def test_validate_correction_inconclusive_when_search_empty(store):
    """When no search results are available, correction is accepted without corroboration."""
    from unittest.mock import AsyncMock

    frame = await store.create_frame("guitar", "entity")
    await store.upsert_slot(frame.id, "strings", "6")

    mock_search = AsyncMock()
    mock_search.search.return_value = []

    correction = CorrectionResult(
        frame_name="guitar", slot_key="strings", new_value="12"
    )
    validation = await validate_correction(
        correction=correction,
        current_value="6",
        store=store,
        search_tool=mock_search,
        llm_client=AsyncMock(),
    )

    assert validation.corroborated is False
    assert validation.contradicted is False
    assert "no third-party sources" in validation.summary.lower()


async def test_validate_correction_invalid_correction(store):
    """Validation handles incomplete correction results gracefully."""
    from unittest.mock import AsyncMock

    mock_search = AsyncMock()
    validation = await validate_correction(
        correction=CorrectionResult(),
        current_value=None,
        store=store,
        search_tool=mock_search,
        llm_client=AsyncMock(),
    )

    assert validation.corroborated is False
    assert validation.contradicted is False
    assert "invalid" in validation.summary.lower()


async def test_extract_correction_parses_valid_input():

    mock_llm = AsyncMock()
    mock_llm.utility_model = "qwen2.5:3b"
    mock_llm.chat.return_value.content = json.dumps({
        "frame_name": "guitar",
        "slot_key": "strings",
        "new_value": "12",
    })

    result = await extract_correction(
        "Actually the guitar has 12 strings, not 6",
        mock_llm,
    )

    assert result is not None
    assert result.frame_name == "guitar"
    assert result.slot_key == "strings"
    assert result.new_value == "12"


async def test_extract_correction_returns_none_for_nulls():

    mock_llm = AsyncMock()
    mock_llm.utility_model = "qwen2.5:3b"
    mock_llm.chat.return_value.content = json.dumps({
        "frame_name": None,
        "slot_key": None,
        "new_value": None,
    })

    result = await extract_correction("That's wrong", mock_llm)
    assert result is None


async def test_extract_correction_retries_then_returns_none():
    from unittest.mock import MagicMock

    mock_llm = AsyncMock()
    mock_llm.utility_model = "qwen2.5:3b"
    mock_llm.chat.side_effect = [
        MagicMock(content="not json"),
        MagicMock(content="still not json"),
    ]

    result = await extract_correction("That's wrong", mock_llm)
    assert result is None
    assert mock_llm.chat.call_count == 2


async def test_apply_correction_updates_existing_slot(store):
    frame = await store.create_frame("guitar", "entity")
    await store.upsert_slot(frame.id, "strings", "6")

    from assistant.backend.pipeline.extractor import CorrectionResult
    summary = await apply_correction(
        CorrectionResult(frame_name="guitar", slot_key="strings", new_value="12"),
        store,
    )

    assert summary["slots_corrected"] == 1
    updated = await store.get_slot(frame.id, "strings")
    assert updated.value == "12"


async def test_apply_correction_creates_frame_if_missing(store):
    from assistant.backend.pipeline.extractor import CorrectionResult
    summary = await apply_correction(
        CorrectionResult(frame_name="novel", slot_key="genre", new_value="sci-fi"),
        store,
    )

    assert summary["slots_corrected"] == 1
    frame = await store.get_frame_by_name("novel")
    assert frame is not None
    slot = await store.get_slot(frame.id, "genre")
    assert slot.value == "sci-fi"


async def test_apply_correction_noops_on_null_fields(store):
    from assistant.backend.pipeline.extractor import CorrectionResult
    summary = await apply_correction(CorrectionResult(), store)
    assert summary["slots_corrected"] == 0


async def test_apply_correction_sets_high_reliability(store):
    frame = await store.create_frame("guitar", "entity")
    await store.upsert_slot(frame.id, "strings", "6")

    from assistant.backend.pipeline.extractor import CorrectionResult
    await apply_correction(
        CorrectionResult(frame_name="guitar", slot_key="strings", new_value="12"),
        store,
    )

    slot = await store.get_slot(frame.id, "strings")
    assert slot.source_type == "user_correction"


async def test_apply_extraction_returns_frame_ids(store):
    """apply_extraction should return the frame_ids it created/looked up."""
    from assistant.backend.pipeline.extractor import ExtractedSlot, ExtractionResult
    extraction = ExtractionResult(
        slots=[
            ExtractedSlot(frame_name="guitar", frame_type="entity", key="strings", value="6"),
            ExtractedSlot(frame_name="music", frame_type="concept", key="genre", value="rock"),
        ],
        associations=[
            {"from_frame": "guitar", "to_frame": "music", "relation_type": "related_to"},
        ],
    )
    summary = await apply_extraction(extraction, store)
    assert "frame_ids" in summary
    assert len(summary["frame_ids"]) == 2
    assert all(isinstance(fid, int) for fid in summary["frame_ids"])


async def test_apply_search_extraction_returns_frame_ids(store):
    """apply_search_extraction should return the frame_ids it created."""
    from assistant.backend.pipeline.extractor import ExtractedSlot, ExtractionResult
    from assistant.backend.pipeline.search import SearchResult
    extraction = ExtractionResult(
        slots=[
            ExtractedSlot(
                frame_name="nikola_tesla",
                frame_type="concept",
                key="born_on",
                value="10 July 1856",
            ),
        ],
        associations=[],
    )
    search_results = [
        SearchResult(
            title="Nikola Tesla",
            url="https://example.com",
            snippet="Born July 10 1856.",
            engine="test",
        ),
    ]
    summary = await apply_search_extraction(extraction, search_results, store)
    assert "frame_ids" in summary
    assert len(summary["frame_ids"]) == 1


async def test_extract_and_apply_embeds_frames(store, stub_llm):
    """extract_and_apply should generate embeddings for newly created frames."""
    from assistant.backend.pipeline.extractor import extract_and_apply
    stub_llm.set_extraction_result(
        slots=[{"frame_name": "piano", "frame_type": "entity", "key": "keys", "value": "88"}],
        associations=[],
    )
    result = await extract_and_apply(
        "I have a piano with 88 keys",
        "Nice!",
        store,
        stub_llm,
    )
    assert "frame_ids" in result
    assert len(result["frame_ids"]) == 1
    frame_id = result["frame_ids"][0]
    emb = await store.get_frame_embedding(frame_id)
    assert emb is not None
    assert len(emb) == 768


async def test_extract_and_apply_returns_frame_ids_even_when_empty(store, stub_llm):
    """extract_and_apply returns frame_ids=[] when nothing is extracted."""
    from assistant.backend.pipeline.extractor import extract_and_apply
    stub_llm.set_extraction_result(slots=[], associations=[])
    result = await extract_and_apply(
        "Hello",
        "Hi!",
        store,
        stub_llm,
    )
    assert result["frame_ids"] == []


async def test_store_embed_frames_generates_and_stores(store, stub_llm):
    """store.embed_frames should generate and persist embeddings for given frame IDs."""
    f1 = await store.create_frame("guitar", "entity")
    f2 = await store.create_frame("music", "concept")
    await store.upsert_slot(f1.id, "strings", "6")
    await store.upsert_slot(f2.id, "genre", "rock")

    async def get_embedding(text: str) -> list[float]:
        resp = await stub_llm.embed(text)
        return resp.embedding

    await store.embed_frames([f1.id, f2.id], get_embedding)

    emb1 = await store.get_frame_embedding(f1.id)
    emb2 = await store.get_frame_embedding(f2.id)
    assert emb1 is not None
    assert emb2 is not None
    assert len(emb1) == 768
    assert len(emb2) == 768


async def test_store_update_episode_frame_ids(store):
    """store.update_episode_frame_ids should update the frame_ids JSON field."""
    user = await store.create_user("alice")
    episode = await store.create_episode(user.id, "sess1", "user", "Hello", frame_ids=[])
    assert episode.frame_ids == []

    await store.update_episode_frame_ids(episode.id, [1, 2, 3])

    updated = await store.get_episodes_for_session("sess1")
    assert len(updated) == 1
    assert updated[0].frame_ids == [1, 2, 3]



def test_filter_duplicate_slots_drops_cross_key_duplicates():
    """Same fact from two channels under different keys must dedup on value."""
    candidate = ExtractionResult(slots=[
        ExtractedSlot(frame_name="fender_stratocaster", key="number_of_strings", value="6"),
        ExtractedSlot(frame_name="fender_stratocaster", key="production_year", value="1985"),
        ExtractedSlot(frame_name="fender_stratocaster", key="finish", value="sunburst"),
    ])
    stored = [
        {"frame_name": "fender_stratocaster", "key": "strings", "value": "6"},
        {"frame_name": "fender_stratocaster", "key": "year_made", "value": "1985"},
    ]

    result = filter_duplicate_slots(candidate, stored)

    assert [s.key for s in result.slots] == ["finish"]
    assert len(result.associations) == len(candidate.associations)


def test_filter_duplicate_slots_case_and_whitespace_insensitive():
    candidate = ExtractionResult(slots=[
        ExtractedSlot(frame_name="Fender_Stratocaster", key="color", value=" Sunburst "),
    ])
    stored = [{"frame_name": "fender_stratocaster", "key": "finish", "value": "sunburst"}]

    result = filter_duplicate_slots(candidate, stored)

    assert result.slots == []


def test_filter_duplicate_slots_empty_stored_returns_candidate():
    candidate = ExtractionResult(slots=[
        ExtractedSlot(frame_name="guitar", key="strings", value="6"),
    ])

    result = filter_duplicate_slots(candidate, [])

    assert result is candidate


def test_filter_duplicate_slots_keeps_none_values():
    candidate = ExtractionResult(slots=[
        ExtractedSlot(frame_name="guitar", key="strings", value=None),
        ExtractedSlot(frame_name="guitar", key="strings", value="6"),
    ])
    stored = [{"frame_name": "guitar", "key": "strings", "value": "6"}]

    result = filter_duplicate_slots(candidate, stored)

    assert [s.value for s in result.slots] == [None]
