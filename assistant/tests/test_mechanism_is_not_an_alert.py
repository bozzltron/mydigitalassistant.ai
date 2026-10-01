"""Regression: mechanism notices are not alerts.

Phase C.4. The presence rule, from plans/2026-09-30-alerts-as-memory.md:

    An alert is warranted when the agent learned something and the user was not
    there to hear it.

Measured on the live brain before this change: of 111 alert rows, **103 were this
class of noise** — 54 "Task completed: job_postings_monitor", 24 search notices fired
mid-conversation, 25 auto-resolved conflicts announced to the user who was watching
them resolve. The 8 that were real were buried in it.

Two writers were removed, and one was kept with its reasoning recorded:

- **Task completion** — not an alert. Mechanism, and the output is already an episode
  the user can ask about.
- **Facts learned mid-conversation** (search results, auto-resolved conflicts) — not
  an alert. `Message.tsx` already renders both as "What I learned" and "Found from
  search", itemised per slot with a conflict flag, so the bell entry was a third copy
  of something already on screen.
- **Task failure** — *kept*, and this is the presence rule applied properly rather
  than an exception to it. The user asked for a recurring task and it is silently
  broken; that is something they were not there to see, it needs their attention, and
  nobody else will tell them.
"""

from __future__ import annotations

import pytest

from assistant.backend.memory.store import ALERT_FRAME_TYPE
from assistant.backend.pipeline.orchestrator import Orchestrator


async def _open_alert_types(store, user_id: int) -> list[str]:
    alerts = await store.get_alerts(user_id, unread_only=True, limit=100)
    return [a.type for a in alerts]


class TestLearnedFactsAreNotAlerts:
    """The mid-conversation writer, which fired while the user was watching."""

    @pytest.mark.asyncio
    async def test_search_results_do_not_raise_an_alert(self, store):
        orch = Orchestrator.__new__(Orchestrator)
        orch.store = store
        user = await store.create_user("alice")

        await orch._create_learning_alerts(
            user_id=user.id,
            extraction_summary={},
            search_extraction_summary={"slots_applied": 5, "conflicts_created": 0},
        )

        assert await _open_alert_types(store, user.id) == [], (
            "'New facts learned from search' is news to nobody: the user just asked "
            "for the search and the facts are already rendered in the response"
        )

    @pytest.mark.asyncio
    async def test_auto_resolved_conflicts_do_not_raise_an_alert(self, store):
        orch = Orchestrator.__new__(Orchestrator)
        orch.store = store
        user = await store.create_user("alice")

        await orch._create_learning_alerts(
            user_id=user.id,
            extraction_summary={"conflicts_created": 3},
            search_extraction_summary={"slots_applied": 2, "conflicts_created": 1},
        )

        assert await _open_alert_types(store, user.id) == [], (
            "conflicts resolved during the turn were announced to the user who was "
            "watching them resolve, and the response already flags them per slot"
        )

    @pytest.mark.asyncio
    async def test_the_method_is_still_callable_on_both_paths(self, store):
        """chat() and chat_stream() both call it; it must not raise."""
        orch = Orchestrator.__new__(Orchestrator)
        orch.store = store
        user = await store.create_user("alice")

        # No return value and no side effect — the contract is now "do nothing".
        assert (
            await orch._create_learning_alerts(user.id, {}, {})  # type: ignore[func-returns-value]
            is None
        )


class TestTheInformationSurvives:
    """Removing the alert must not remove the information.

    The response carries both summaries and the UI renders them; that is why the
    alert was a duplicate. This pins the payload the UI depends on so a future
    change cannot quietly drop it along with the alert.
    """

    @pytest.mark.asyncio
    async def test_response_type_carries_both_summaries(self, store):
        from assistant.backend.pipeline.orchestrator import ChatResponse

        fields = ChatResponse.model_fields
        assert "extraction_summary" in fields
        assert "search_extraction_summary" in fields


class TestTaskFailureStillAlerts:
    """The one mechanism notice that earns its place, and why."""

    @pytest.mark.asyncio
    async def test_a_failed_task_creates_an_alert_the_user_can_act_on(self, store):
        """Not an exception to the presence rule: the task broke while the user was
        away, and only the agent knows."""
        user = await store.create_user("alice")
        await store.create_alert(
            user_id=user.id,
            type="task_failure",
            title="Task failed: daily_briefing",
            message="Your scheduled task 'daily_briefing' failed to run: connection refused",
            severity="warning",
        )

        alerts = await store.get_alerts(user.id, unread_only=True)
        assert len(alerts) == 1
        assert alerts[0].type == "task_failure"
        assert alerts[0].severity == "warning"
        # Actionable: it says what broke and that it will retry.
        assert "failed to run" in alerts[0].message

    @pytest.mark.asyncio
    async def test_task_completion_is_distinguishable_by_type(self, store):
        """`task_failure` exists so a completion and a failure are not the same
        type — the distinction is deliberate and should stay queryable."""
        user = await store.create_user("alice")
        frames = await store.list_frames(ALERT_FRAME_TYPE, owner_user_id=user.id)
        assert frames == [], "no alert should exist before one is created"

        await store.create_alert(
            user_id=user.id,
            type="task_failure",
            title="Task failed: x",
            message="failed",
            severity="warning",
        )
        slots = {
            s.key: s.value
            for f in await store.list_frames(ALERT_FRAME_TYPE, owner_user_id=user.id)
            for s in await store.get_slots_for_frame(f.id)
        }
        assert slots["kind"] == "task_failure"
        assert slots["kind"] != "task_result"
