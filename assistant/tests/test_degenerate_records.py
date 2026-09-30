"""Regression: the agent must not create memory with no content.

Frame 4387 was created on 2026-09-30 from a single paste turn ("Here are all my
manual submission links..."). It has an **empty name**, zero slots, and zero
associations, and the episode that produced it is linked to it. It is the only
empty-named frame in the live brain, and it is pure pollution: it expresses
nothing, but later reads and retrievals have to reason around it.

The origin is structural, not a one-off. `apply_extraction` built the set of
frames to resolve from every slot *and association* name:

    all_frame_names = {slot.frame_name for slot in extraction.slots}
    for assoc in extraction.associations:
        all_frame_names.add(assoc.from_frame)
        all_frame_names.add(assoc.to_frame)

An extraction result carrying an empty `frame_name` reaches
`resolve_or_create_frame` with `name=""`, which `get_frame_by_name("")` misses and
which therefore creates a new frame. Model output is untrusted input, so a blank
name is not a value to be corrected later -- it is an object with no identity.

The fix is defence in depth, and both layers are pinned here:

1. Source: `_drop_degenerate` removes blank-named/blank-keyed slots and blank-named
   associations before any frame is resolved, and blank values before any slot is
   written.
2. Sink: `resolve_or_create_frame` refuses a blank name outright, so no other
   caller can create a nameless frame.
"""

from __future__ import annotations

import pytest

from assistant.backend.pipeline.extractor import (
    ExtractedAssociation,
    ExtractedSlot,
    ExtractionResult,
    _is_blank,
    apply_extraction,
    resolve_or_create_frame,
)

BLANK_STRINGS = ["", " ", "\t", "\n", "   \t\n  "]


class TestIsBlank:
    """The predicate the guards are built on."""

    @pytest.mark.parametrize("value", BLANK_STRINGS)
    def test_blank_values_are_blank(self, value):
        assert _is_blank(value) is True

    @pytest.mark.parametrize("value", ["a", "0", " ", "x"])
    def test_content_is_not_blank(self, value):
        if value.strip():
            assert _is_blank(value) is False

    def test_none_is_blank(self):
        assert _is_blank(None) is True


class TestResolveOrCreateFrameRefusesBlankName:
    """Sink layer: no blank-named frame can be created, by any caller."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("name", BLANK_STRINGS)
    async def test_blank_name_raises(self, store, name):
        with pytest.raises(ValueError, match="non-blank frame name"):
            await resolve_or_create_frame(store, name, "entity")

    @pytest.mark.asyncio
    async def test_no_frame_was_created(self, store):
        before = len(await store.list_live_frame_stubs())
        with pytest.raises(ValueError):
            await resolve_or_create_frame(store, "   ", "entity")
        after = len(await store.list_live_frame_stubs())
        assert after == before, "a blank name created a frame anyway"

    @pytest.mark.asyncio
    async def test_ordinary_name_still_works(self, store):
        """The guard must not block normal frame creation."""
        frame_id = await resolve_or_create_frame(store, "alice", "entity")
        frame = await store.get_frame(frame_id)
        assert frame is not None
        assert frame.name == "alice"


class TestApplyExtractionDropsDegenerateSlots:
    """Source layer: blank names and keys never reach the write path."""

    @pytest.mark.asyncio
    async def test_blank_frame_name_writes_nothing(self, store):
        result = await apply_extraction(
            ExtractionResult(
                slots=[
                    ExtractedSlot(frame_name="", frame_type="entity", key="x", value="y")
                ]
            ),
            store,
        )
        assert result["slots_applied"] == 0
        assert result["frame_ids"] == []
        assert result["degenerate_dropped"] == 1

    @pytest.mark.asyncio
    async def test_blank_slot_key_writes_nothing(self, store):
        result = await apply_extraction(
            ExtractionResult(
                slots=[
                    ExtractedSlot(frame_name="alice", frame_type="entity", key="", value="y")
                ]
            ),
            store,
        )
        assert result["slots_applied"] == 0
        assert result["degenerate_dropped"] == 1

    @pytest.mark.asyncio
    async def test_blank_slot_value_writes_nothing(self, store):
        result = await apply_extraction(
            ExtractionResult(
                slots=[
                    ExtractedSlot(
                        frame_name="alice", frame_type="entity", key="city", value="   "
                    )
                ]
            ),
            store,
        )
        assert result["slots_applied"] == 0
        assert result["degenerate_dropped"] == 1

    @pytest.mark.asyncio
    async def test_blank_association_creates_no_edge(self, store):
        result = await apply_extraction(
            ExtractionResult(
                associations=[
                    ExtractedAssociation(
                        from_frame="", to_frame="alice", relation_type="related_to"
                    )
                ]
            ),
            store,
        )
        assert result["associations_created"] == 0
        assert result["degenerate_dropped"] == 1
        # And crucially: no empty-named frame was created to be the endpoint.
        assert result["frame_ids"] == []

    @pytest.mark.asyncio
    async def test_no_empty_named_frame_from_full_payload(self, store):
        """The frame-4387 shape: blank names that also carry associations."""
        result = await apply_extraction(
            ExtractionResult(
                slots=[
                    ExtractedSlot(
                        frame_name="", frame_type="entity", key="links", value="http://x"
                    )
                ],
                associations=[
                    ExtractedAssociation(
                        from_frame="", to_frame="wprb", relation_type="related_to"
                    )
                ],
            ),
            store,
        )
        assert result["slots_applied"] == 0
        stubs = await store.list_live_frame_stubs()
        assert all(name.strip() for _fid, name, _t in stubs), (
            "an empty-named frame was created"
        )

    @pytest.mark.asyncio
    async def test_healthy_payload_still_writes(self, store):
        """The guard must not block normal extraction."""
        result = await apply_extraction(
            ExtractionResult(
                slots=[
                    ExtractedSlot(
                        frame_name="alice", frame_type="entity", key="city", value="Austin"
                    )
                ]
            ),
            store,
        )
        assert result["slots_applied"] == 1
        assert result["degenerate_dropped"] == 0
        assert result["frame_ids"]


class TestDegenerateDroppedIsSurfaced:
    """Dropped records are visible, not silent -- a model emitting blanks is
    misbehaving and that must show in the extraction summary rather than looking
    like a turn where nothing was learned."""

    @pytest.mark.asyncio
    async def test_count_is_reported(self, store):
        result = await apply_extraction(
            ExtractionResult(
                slots=[
                    ExtractedSlot(frame_name="", frame_type="entity", key="a", value="1"),
                    ExtractedSlot(frame_name="alice", frame_type="entity", key="", value="2"),
                    ExtractedSlot(
                        frame_name="alice", frame_type="entity", key="city", value="Austin"
                    ),
                ]
            ),
            store,
        )
        assert result["degenerate_dropped"] == 2
        assert result["slots_applied"] == 1
        assert "degenerate_dropped" in result


class TestApplyCorrectionRejectsBlank:
    """The correction path shares the predicate (it previously used truthiness,
    which let whitespace-only values through)."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("value", ["", "   ", "\t"])
    async def test_blank_new_value_is_rejected(self, store, value):
        from assistant.backend.pipeline.extractor import CorrectionResult, apply_correction

        result = await apply_correction(
            CorrectionResult(frame_name="alice", slot_key="city", new_value=value),
            store,
        )
        assert result["slots_corrected"] == 0
