"""Regression: a counter rewritten every run is not a belief.

`_upsert_summary_frame` wrote `turn_count`, `date_start`, and `date_end` as slots
and re-upserted all of them on **every** summarization run. Because they change by
design, each run "conflicted" with the previous value and landed in the conflicts
ledger. Measured on the live brain before the fix:

    conversation_summary conflict rows:  3,307 of 3,857 total (86%)
      bookkeeping (turn_count/date_*/session_id): 1,172
      content re-upserted (summary/key_entities/open_questions): 2,135

That is 3,307 rows recording a summarizer arguing with itself, on zero actual
disagreements. Frame 1406 alone accounted for 258 pending rows.

The fix splits the two kinds of slot:

- **Content** (summary prose, key_entities, open_questions) are beliefs, so they
  keep going through `upsert_slot` and keep participating in conflict detection.
- **Bookkeeping** (session_id, turn_count, date_start, date_end) is written
  directly via `set_bookkeeping_slot`, which bypasses belief revision entirely.

Also pinned: empty `key_entities` / `open_questions` now write **no slot** rather
than an empty string (the source of the blank slot values in the live brain).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from assistant.backend.scheduler.summarizer import Summarizer


def _summarizer(store):
    llm = MagicMock()
    llm.embed = AsyncMock(return_value=MagicMock(embedding=[0.0] * 1024))
    return Summarizer(store=store, llm_client=llm)


async def _summary_frame(store, session_id):
    return await store.get_frame_by_name(f"conversation_summary_{session_id}")


class TestCountersDoNotConflict:
    """The core regression: summarizing twice creates no conflict rows."""

    @pytest.mark.asyncio
    async def test_second_run_creates_no_conflicts(self, store):
        user = await store.create_user("alice")
        s = _summarizer(store)

        await s._upsert_summary_frame(
            session_id="conv_a",
            user_id=user.id,
            summary="First summary.",
            key_entities=["mozworth"],
            open_questions=["when?"],
            turn_count=10,
            date_range=("2026-01-01", "2026-01-02"),
        )

        # Same session, one more turn summarized: counters move, prose re-derived.
        await s._upsert_summary_frame(
            session_id="conv_a",
            user_id=user.id,
            summary="Second summary.",
            key_entities=["mozworth"],
            open_questions=["when?"],
            turn_count=11,
            date_range=("2026-01-01", "2026-01-03"),
        )

        # The counters changing must not register as disagreements. The prose
        # changing is a genuine content revision and may legitimately conflict,
        # so the assertion is scoped to bookkeeping keys.
        conflicts = await store.get_conflicts_for_frame(
            (await _summary_frame(store, "conv_a")).id
        )
        bookkeeping_keys = {"session_id", "turn_count", "date_start", "date_end"}
        offending = [c for c in conflicts if c.slot_key in bookkeeping_keys]
        assert offending == [], (
            f"bookkeeping slots created conflicts: {[c.slot_key for c in offending]}"
        )

    @pytest.mark.asyncio
    async def test_counters_still_stored_and_updated(self, store):
        """Bypassing conflict detection must not mean losing the data."""
        user = await store.create_user("alice")
        s = _summarizer(store)

        await s._upsert_summary_frame(
            session_id="conv_b",
            user_id=user.id,
            summary="A summary.",
            key_entities=[],
            open_questions=[],
            turn_count=10,
            date_range=("2026-01-01", "2026-01-02"),
        )
        await s._upsert_summary_frame(
            session_id="conv_b",
            user_id=user.id,
            summary="A summary.",
            key_entities=[],
            open_questions=[],
            turn_count=42,
            date_range=("2026-01-01", "2026-01-09"),
        )

        frame = await _summary_frame(store, "conv_b")
        slots = {sl.key: sl.value for sl in await store.get_slots_for_frame(frame.id)}
        assert slots["turn_count"] == "42"
        assert slots["date_end"] == "2026-01-09"
        assert slots["session_id"] == "conv_b"


class TestEmptyListsWriteNoSlot:
    """`", ".join([])` is `""`, which was being written as a slot value."""

    @pytest.mark.asyncio
    async def test_no_open_questions_writes_no_slot(self, store):
        user = await store.create_user("alice")
        s = _summarizer(store)

        await s._upsert_summary_frame(
            session_id="conv_c",
            user_id=user.id,
            summary="Nothing open.",
            key_entities=[],
            open_questions=[],
            turn_count=5,
            date_range=("2026-01-01", "2026-01-02"),
        )

        frame = await _summary_frame(store, "conv_c")
        slots = {sl.key: sl.value for sl in await store.get_slots_for_frame(frame.id)}
        assert "open_questions" not in slots
        assert "key_entities" not in slots
        # And nothing in the frame is a blank value.
        assert all(v.strip() for v in slots.values()), slots


class TestContentStillBehavesLikeABelief:
    """The split must not weaken genuine content revisions."""

    @pytest.mark.asyncio
    async def test_content_slots_go_through_upsert(self, store):
        user = await store.create_user("alice")
        s = _summarizer(store)

        await s._upsert_summary_frame(
            session_id="conv_d",
            user_id=user.id,
            summary="Original.",
            key_entities=["a"],
            open_questions=[],
            turn_count=3,
            date_range=("2026-01-01", "2026-01-02"),
        )
        frame = await _summary_frame(store, "conv_d")
        before = {
            sl.key: sl.confidence
            for sl in await store.get_slots_for_frame(frame.id)
        }

        await s._upsert_summary_frame(
            session_id="conv_d",
            user_id=user.id,
            summary="Original.",
            key_entities=["a"],
            open_questions=[],
            turn_count=3,
            date_range=("2026-01-01", "2026-01-02"),
        )
        after = {
            sl.key: sl.confidence
            for sl in await store.get_slots_for_frame(frame.id)
        }

        # Re-summarizing identical content reinforces it, as for any belief.
        assert after["summary"] > before["summary"]
