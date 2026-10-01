"""Regression: a conflict records the inputs its decision ran on.

The `conflict_ladder_value` experiment could not answer whether the confidence ladder
was doing anything, and the reason was instrumentation, not behaviour:

    slot_history holds a matching old/new pair for 556 of 556 conflicts,
    but has no provenance column. Reconstruction is impossible for 100% of the sample.

`resolve_conflict` ranks by `source_reliability -> confidence -> priority -> recency`.
To audit a past decision you need what each side carried *at that moment*, and nothing
retained it. A brain that cannot account for its own belief changes after the fact is
a brain you cannot tune.

These tests pin the fix: the inputs are written with the conflict and readable back.
The columns are nullable and existing rows keep NULL — their provenance is genuinely
gone, and inventing it would be worse than admitting the gap.

Why this matters beyond the experiment: without it, "the agent nearly got renamed to
grok" is a story about luck rather than a decision that can be inspected. With it, the
next occurrence is a query.
"""

from __future__ import annotations

import pytest


class TestProvenanceIsRecorded:
    @pytest.mark.asyncio
    async def test_an_auto_resolved_conflict_carries_both_sides(self, store):
        """A user-stated fact (0.95) overwriting a search fact (0.5)."""
        user = await store.create_user("alice")
        frame = await store.create_frame("austin_tx", "entity", owner_user_id=user.id)

        # Existing, from search: reliability 0.5.
        await store.upsert_slot(
            frame_id=frame.id, key="city", value="Austin", source_type="search"
        )
        # New, from the user: reliability 0.95, so the ladder should defend nothing
        # and the new value wins on rung 1.
        _, conflict = await store.upsert_slot(
            frame_id=frame.id,
            key="city",
            value="Austin, TX",
            source_type="user_correction",
            source_reliability=0.95,
        )

        assert conflict is not None, "no conflict was recorded"
        # The inputs the decision ran on, as received.
        assert conflict.existing_source_reliability == 0.5
        assert conflict.new_source_reliability == 0.95
        assert conflict.existing_confidence is not None
        assert conflict.new_confidence is not None
        assert conflict.existing_priority is not None
        assert conflict.new_priority is not None

    @pytest.mark.asyncio
    async def test_the_reliability_is_recorded_as_received_not_defaulted(self, store):
        """`None` must stay `None`.

        The experiment's premise was that extraction supplies no reliability, so the
        ladder falls through to recency. Recording a defaulted 0.5 instead would
        destroy exactly the evidence needed to confirm or refute that.
        """
        user = await store.create_user("alice")
        frame = await store.create_frame("thing", "entity", owner_user_id=user.id)
        await store.upsert_slot(frame_id=frame.id, key="k", value="old")

        _, conflict = await store.upsert_slot(
            frame_id=frame.id, key="k", value="new"  # no reliability, no source_type
        )

        assert conflict is not None
        assert conflict.new_source_reliability is None, (
            "the new side's reliability was defaulted; the decision can no longer be "
            "audited for whether it ran on a real value or a fallback"
        )

    @pytest.mark.asyncio
    async def test_an_existing_wins_decision_is_recorded_as_decided(self, store):
        """The `grok` shape: a lower-reliability source tries to overwrite a
        higher-reliability fact.

        The ladder decides (the user's value stands) and the row now says so. It used
        to say 'pending', which is why a decision the agent had made read as an open
        question — and why "the agent nearly got renamed to grok" looked like luck
        rather than the ladder working.
        """
        user = await store.create_user("alice")
        frame = await store.create_frame("identity_name", "entity", owner_user_id=user.id)
        await store.upsert_slot(
            frame_id=frame.id,
            key="full_name",
            value="Echo",
            source_type="user_correction",
            source_reliability=0.99,
        )
        slot, conflict = await store.upsert_slot(
            frame_id=frame.id,
            key="full_name",
            value="grok",
            source_type="search",
            source_reliability=0.5,
        )

        assert conflict is not None
        # Decided, not deferred.
        assert conflict.status == "auto_resolved"
        assert conflict.resolved_value == "Echo"
        # The hostile value did not land.
        assert slot.value == "Echo"
        # And the inputs that produced the decision are on the row, so this is
        # auditable rather than reconstructed.
        assert conflict.existing_source_reliability == 0.99
        assert conflict.new_source_reliability == 0.5

    @pytest.mark.asyncio
    async def test_provenance_is_readable_back_through_get_conflicts(self, store):
        """Writing it is only half the point; it must come back out."""
        user = await store.create_user("alice")
        frame = await store.create_frame("thing", "entity", owner_user_id=user.id)
        await store.upsert_slot(
            frame_id=frame.id, key="k", value="old", source_reliability=0.9
        )
        await store.upsert_slot(
            frame_id=frame.id, key="k", value="new", source_reliability=0.4
        )

        rows = await store.get_conflicts_for_frame(frame.id)
        assert rows, "no conflicts returned"
        row = rows[-1]
        assert row.existing_source_reliability == 0.9
        assert row.new_source_reliability == 0.4

        # And via the unfiltered read too, which uses a different SELECT.
        all_rows = await store.get_conflicts()
        assert any(
            r.existing_source_reliability == 0.9
            and r.new_source_reliability == 0.4
            for r in all_rows
        ), "the provenance did not survive the get_conflicts SELECT"

    @pytest.mark.asyncio
    async def test_an_equal_reliability_decision_is_distinguishable(self, store):
        """The case the experiment could not see: both sides at 0.5, so the decision
        fell through to recency. Recorded as two 0.5s, it is now identifiable."""
        user = await store.create_user("alice")
        frame = await store.create_frame("thing", "entity", owner_user_id=user.id)
        await store.upsert_slot(
            frame_id=frame.id, key="k", value="old", source_reliability=0.5
        )
        _, conflict = await store.upsert_slot(
            frame_id=frame.id, key="k", value="new", source_reliability=0.5
        )

        assert conflict is not None
        assert conflict.existing_source_reliability == conflict.new_source_reliability
        assert conflict.resolved_value == "new", "recency decides ties"
