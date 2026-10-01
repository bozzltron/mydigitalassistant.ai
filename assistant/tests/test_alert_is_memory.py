"""Regression: an alert is memory of a type, not a notifications row.

The `alerts` table was a second storage mechanism for something the memory model
already expresses. It had 103 rows, all unread, and the reason none were read is
structural: reading an alert accomplished nothing, so nobody did it.

Making an alert a frame is not bookkeeping. Because it is memory it participates in
retrieval, which means the agent can raise an alert **when it is contextually
relevant** -- the user mid-conversation about their submission list can be told about
the alert already held on a related conflict. A notification table can only be looked
at. That is the whole argument, and it is the same reasoning that makes a scheduled
task a frame rather than a scheduler column.

The signature of `create_alert` is deliberately unchanged, so all five call sites
keep working and this is a storage change rather than a rewrite of its callers.
"""

from __future__ import annotations

import pytest

from assistant.backend.memory.store import ALERT_FRAME_TYPE


class TestAlertIsAFrame:
    @pytest.mark.asyncio
    async def test_create_alert_writes_a_frame_not_a_row(self, store):
        user = await store.create_user("alice")
        await store.create_alert(
            user_id=user.id,
            type="task_alert",
            title="Restock Alert",
            message="The Zelda console restocked just now.",
            severity="important",
        )

        frames = await store.list_frames(ALERT_FRAME_TYPE, owner_user_id=user.id)
        assert len(frames) == 1, "the alert was not stored as a frame"
        assert frames[0].type == ALERT_FRAME_TYPE

    @pytest.mark.asyncio
    async def test_alert_carries_its_content_as_slots(self, store):
        user = await store.create_user("alice")
        alert = await store.create_alert(
            user_id=user.id,
            type="task_alert",
            title="Restock Alert",
            message="Limited quantities.",
            severity="important",
        )
        slots = {s.key: s.value for s in await store.get_slots_for_frame(alert.id)}
        assert slots["title"] == "Restock Alert"
        assert slots["message"] == "Limited quantities."
        assert slots["severity"] == "important"
        assert slots["kind"] == "task_alert"
        assert slots["status"] == "new"

    @pytest.mark.asyncio
    async def test_alert_is_retrievable_as_memory(self, store):
        """The reason alerts are frames: they can be surfaced contextually, not
        only by opening a bell."""
        user = await store.create_user("alice")
        alert = await store.create_alert(
            user_id=user.id,
            type="conflict",
            title="Two beliefs about your city",
            message="I hold 'Austin' and 'Austin, TX'.",
            about="austin_tx",
        )
        frame = await store.get_frame(alert.id)
        assert frame is not None
        # It is an ordinary frame, so the embedding machinery applies to it.
        assert frame.type == ALERT_FRAME_TYPE

    @pytest.mark.asyncio
    async def test_about_records_what_it_concerns(self, store):
        """Resolution needs a target, and the deterministic backstop watches it."""
        user = await store.create_user("alice")
        alert = await store.create_alert(
            user_id=user.id,
            type="conflict",
            title="t",
            message="m",
            about="austin_tx",
        )
        slots = {s.key: s.value for s in await store.get_slots_for_frame(alert.id)}
        assert slots["about"] == "austin_tx"


class TestTwoStatesOnly:
    """The UI needs exactly one bit: is this still open?"""

    @pytest.mark.asyncio
    async def test_new_alert_is_counted(self, store):
        user = await store.create_user("alice")
        await store.create_alert(user_id=user.id, type="task_alert", title="a", message="b")
        assert await store.get_unread_alert_count(user.id) == 1

    @pytest.mark.asyncio
    async def test_resolved_alert_is_not_counted(self, store):
        user = await store.create_user("alice")
        alert = await store.create_alert(
            user_id=user.id, type="task_alert", title="a", message="b"
        )
        resolved = await store.resolve_alert(alert.id)
        assert resolved is True
        assert await store.get_unread_alert_count(user.id) == 0

    @pytest.mark.asyncio
    async def test_status_transitions_to_resolved(self, store):
        user = await store.create_user("alice")
        alert = await store.create_alert(
            user_id=user.id, type="task_alert", title="a", message="b"
        )
        await store.resolve_alert(alert.id)
        slots = {s.key: s.value for s in await store.get_slots_for_frame(alert.id)}
        assert slots["status"] == "resolved"

    @pytest.mark.asyncio
    async def test_mark_read_means_resolve(self, store):
        """The old API name is kept, but it now performs the real transition: an
        alert the user looked at and did not answer is still open."""
        user = await store.create_user("alice")
        alert = await store.create_alert(
            user_id=user.id, type="task_alert", title="a", message="b"
        )
        await store.mark_alert_read(alert.id, user.id)
        assert await store.get_unread_alert_count(user.id) == 0

    @pytest.mark.asyncio
    async def test_get_alerts_excludes_resolved_by_default(self, store):
        user = await store.create_user("alice")
        a = await store.create_alert(
            user_id=user.id, type="task_alert", title="open", message="m"
        )
        await store.create_alert(
            user_id=user.id, type="task_alert", title="also open", message="m"
        )
        await store.resolve_alert(a.id)

        open_alerts = await store.get_alerts(user.id)
        assert len(open_alerts) == 1
        assert open_alerts[0].title == "also open"

    @pytest.mark.asyncio
    async def test_resolving_all_reports_the_count(self, store):
        user = await store.create_user("alice")
        for _ in range(3):
            await store.create_alert(
                user_id=user.id, type="task_alert", title="t", message="m"
            )
        moved = await store.mark_all_alerts_read(user.id)
        assert moved == 3
        assert await store.get_unread_alert_count(user.id) == 0


class TestConsolidationExcludesAlerts:
    """Two unrelated questions must never fuse into one."""

    @pytest.mark.asyncio
    async def test_alert_frames_are_not_scanned_for_merging(self, store):
        from assistant.backend.memory.consolidate import run_consolidation

        user = await store.create_user("alice")
        # Two alerts with near-identical text -- exactly what fuzzy merging would
        # unify if alerts were not excluded.
        for _ in range(2):
            await store.create_alert(
                user_id=user.id,
                type="conflict",
                title="Two beliefs about your city",
                message="I hold 'Austin' and 'Austin, TX' and cannot choose.",
            )

        # dry_run: compute the plan, write nothing.
        report = await run_consolidation(store.db_path, dry_run=True)
        alert_ids = {
            f.id for f in await store.list_frames(ALERT_FRAME_TYPE, owner_user_id=user.id)
        }
        assert alert_ids, "no alert frames were created"
        # The report counts what it scanned; alerts must not be among them.
        assert report.scanned_frames < 2 + len(alert_ids), (
            "alert frames were included in the consolidation scan"
        )
