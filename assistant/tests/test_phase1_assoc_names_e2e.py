"""Phase 1 end-to-end: real associations through the store render as names.

The unit tests in `test_retrieval.py` build `RetrievedFrame`s by hand, which
proves the formatter. This one writes real frames and a real association through
`MemoryStore`, resolves names the way `Retriever.retrieve` does, and asserts no
database id reaches the prompt.
"""

from __future__ import annotations

import re

import pytest

from assistant.backend.memory.retrieval import (
    MemoryContext,
    RetrievedFrame,
    format_memory_context,
)


@pytest.mark.asyncio
async def test_real_association_renders_as_a_name(store):
    user = await store.create_user("alice")
    guitar = await store.create_frame("guitar", "entity", owner_user_id=user.id)
    amp = await store.create_frame("amplifier", "entity", owner_user_id=user.id)
    await store.create_association(
        from_frame_id=guitar.id,
        to_frame_id=amp.id,
        relation_type="played_through",
    )

    assocs = await store.get_all_associations_for_frame(guitar.id)
    assert assocs, "the association was not stored"

    target_ids = {x for a in assocs for x in (a.from_frame_id, a.to_frame_id)} - {guitar.id}
    names = await store.get_frames_by_ids(list(target_ids))

    rf = RetrievedFrame(
        frame=guitar,
        slots=await store.get_slots_for_frame(guitar.id),
        associations=assocs,
        relevance=0.9,
        source="direct_match",
    )
    ctx = MemoryContext(
        query="guitars",
        retrieved_frames=[rf],
        recent_episodes=[],
        formatted="",
        frame_names={i: n.name for i, n in names.items()},
    )
    out = format_memory_context(ctx)

    assert "played_through\u2192amplifier" in out
    # The regression: no `frame:<id>` and no bare numeric id anywhere.
    assert not re.search(r"frame:\d+", out)
    assert str(amp.id) not in out
