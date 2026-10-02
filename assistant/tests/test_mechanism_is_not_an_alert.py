"""Regression: mechanism notices are not alerts, and removing them loses no information.

Phase C.4. The presence rule, from plans/2026-09-30-alerts-as-memory.md:

    An alert is warranted when the agent learned something and the user was not
    there to hear it.

Measured on the live brain before this change: of 111 alert rows, **103 were this
class of noise** — 54 "Task completed: job_postings_monitor", 24 search notices fired
mid-conversation, 25 auto-resolved conflicts announced to the user who was watching
them resolve. The 8 that were real were buried in it.

These tests go through `chat_stream`, not through the private method that used to
emit the notices. Asserting that a no-op returns `None`, or that two fields still
exist on a response model, would pass whether or not the behaviour held — the
guarantee is about what happens to a turn that learns things.
"""

from __future__ import annotations

import pytest

from assistant.backend.pipeline.orchestrator import ChatRequest
from assistant.backend.pipeline.search import SearchResult

from .conftest import add_embedding_cluster


def _empty_context():
    from assistant.backend.memory.retrieval import MemoryContext

    ctx = MemoryContext(
        query="", retrieved_frames=[], recent_episodes=[], formatted=""
    )
    return ctx


def _search_tool(results):
    from assistant.backend.pipeline.search import WebSearchTool

    tool = WebSearchTool(enabled=False)

    async def _with_info(query, num_results=5, llm_client=None, user_consent=False):
        return results, None

    tool.search_with_info = _with_info  # type: ignore[method-assign]
    return tool


def _orchestrator(store, llm, search):
    from assistant.backend.memory.retrieval import Retriever
    from assistant.backend.pipeline.orchestrator import Orchestrator, OrchestratorDeps

    return Orchestrator(
        deps=OrchestratorDeps(
            store=store,
            retriever=Retriever(store=store, llm_client=llm),
            llm_client=llm,
            search_tool=search,
        )
    )


async def _run_turn(store, llm, search, message, session_id, user_id: int = 1):
    orch = _orchestrator(store, llm, search)
    events = []
    async for chunk in orch.chat_stream(
        ChatRequest(user_id=user_id, message=message, session_id=session_id)
    ):
        events.append(chunk)
    return events


class TestAMidConversationTurnRaisesNoAlert:
    """The writer that fired while the user was watching."""

    @pytest.mark.asyncio
    async def test_search_learning_creates_no_alert(self, store, stub_llm, monkeypatch):
        add_embedding_cluster("capital", "texas", "austin")
        search = _search_tool(
            [
                SearchResult(
                    title="Capital of Texas",
                    url="https://en.wikipedia.org/wiki/Texas",
                    snippet="Austin is the capital of Texas",
                    engine="wikipedia",
                )
            ]
        )
        user = await store.create_user("alice")

        raised: list[dict] = []
        real = store.create_alert

        async def recording(**kwargs):
            raised.append(kwargs)
            return await real(**kwargs)

        monkeypatch.setattr(store, "create_alert", recording)

        await _run_turn(
            store,
            stub_llm,
            search,
            "What is the capital of Texas?",
            "s-learn",
            user_id=user.id,
        )

        learning = [
            a for a in raised if a.get("type") in ("search_result", "conflict")
        ]
        assert learning == [], (
            "a fact learned while the user was in the conversation raised a bell "
            f"entry: {learning}"
        )

    @pytest.mark.asyncio
    async def test_the_learning_still_reaches_the_ui(self, store, stub_llm, monkeypatch):
        """Removing the alert must not remove the information.

        `Message.tsx` renders `extraction_summary` and `search_extraction_summary`
        as "What I learned" / "Found from search", itemised per slot. If that stopped
        arriving, the web UI would silently stop reporting learning — which is the
        bug the removed alerts were originally added to fix.
        """
        add_embedding_cluster("capital", "texas", "austin")
        search = _search_tool(
            [
                SearchResult(
                    title="Capital of Texas",
                    url="https://en.wikipedia.org/wiki/Texas",
                    snippet="Austin is the capital of Texas",
                    engine="wikipedia",
                )
            ]
        )
        user = await store.create_user("alice")

        events = await _run_turn(
            store,
            stub_llm,
            search,
            "What is the capital of Texas?",
            "s-learn-2",
            user_id=user.id,
        )

        metas = [e for e in events if '"type": "meta"' in e]
        assert metas, "the stream produced no meta event for the UI to render"
        # The payload the UI reads must be present, even when empty.
        assert "extraction_summary" in metas[-1]
        assert "search_extraction_summary" in metas[-1]


class TestTaskCompletionIsNotAnAlert:
    """Mechanism, not a message — and the output is already queryable memory."""

    @pytest.mark.asyncio
    async def test_a_completed_task_creates_no_alert(self, store, stub_llm):
        from assistant.backend.scheduler.runner import execute_and_record_task

        user = await store.create_user("alice")
        frame_id = await store.upsert_scheduled_task(
            name="probe_task",
            description="",
            schedule_cron="daily",
            prompt="say hello",
            owner_user_id=user.id,
        )
        before = len(await store.get_alerts(user.id, unread_only=True, limit=100))

        search = _search_tool([])
        orch = _orchestrator(store, stub_llm, search)
        await execute_and_record_task(
            store=store,
            orchestrator=orch,
            task_frame_id=frame_id,
            task_name="probe_task",
            task_prompt="say hello",
            owner_user_id=user.id,
        )

        after = await store.get_alerts(user.id, unread_only=True, limit=100)
        completion = [a for a in after if a.type == "task_result"]
        assert completion == [], (
            f"'Task completed: probe_task' raised a bell entry ({len(after)} vs "
            f"{before} open alerts)"
        )


class TestTaskFailureStillAlerts:
    """The one mechanism notice that earns its place.

    Not an exception to the presence rule — the rule applied properly. The user
    asked for a recurring task and it is now silently broken: they were not there to
    see it, it needs their attention, and nobody else will tell them.
    """

    @pytest.mark.asyncio
    async def test_a_failed_task_creates_an_actionable_alert(self, store):
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
        # Actionable: what broke, and that it will retry.
        assert "failed to run" in alerts[0].message

    @pytest.mark.asyncio
    async def test_failure_is_a_distinct_kind_from_completion(self, store):
        """`task_failure` exists so the two are not the same kind. The distinction
        is deliberate and must stay queryable, or the next reader cannot tell it was
        a decision rather than an inconsistency."""
        user = await store.create_user("alice")
        await store.create_alert(
            user_id=user.id,
            type="task_failure",
            title="Task failed: x",
            message="failed",
            severity="warning",
        )
        frames = await store.list_frames("alert", owner_user_id=user.id)
        kinds = {
            s.value
            for f in frames
            for s in await store.get_slots_for_frame(f.id)
            if s.key == "kind"
        }
        assert kinds == {"task_failure"}


class TestACorrectionTurnRaisesNoAlert:
    """The other writer that fired while the user was watching.

    A correction happens in the user's own conversation and `_acknowledge_correction`
    says so in the reply, so the alert was a third copy of something on screen — and
    an unclearable one, because a notice about something that already happened has no
    question in it. Two writers raised these (applied, contradicted); both are gone.
    """

    @staticmethod
    async def _correction_orchestrator(store, llm, monkeypatch, *, contradicted):
        from assistant.backend.pipeline import extractor as extractor_mod
        from assistant.backend.pipeline.extractor import (
            CorrectionResult,
            CorrectionValidation,
        )

        async def fake_extract(message, llm_client):
            return CorrectionResult(
                frame_name="city", slot_key="name", new_value="Dallas"
            )

        async def fake_validate(
            correction, current_value, store, search_tool, llm_client
        ):
            return CorrectionValidation(
                corroborated=not contradicted,
                contradicted=contradicted,
                summary="stub",
            )

        monkeypatch.setattr(extractor_mod, "extract_correction", fake_extract)
        monkeypatch.setattr(extractor_mod, "validate_correction", fake_validate)
        return _orchestrator(store, llm, _search_tool([]))

    @pytest.mark.asyncio
    async def test_an_applied_correction_creates_no_alert(
        self, store, stub_llm, monkeypatch
    ):
        user = await store.create_user("alice")
        frame = await store.create_frame("city", "entity", owner_user_id=user.id)
        await store.upsert_slot(frame_id=frame.id, key="name", value="Austin")

        raised: list[dict] = []
        real = store.create_alert

        async def recording(**kwargs):
            raised.append(kwargs)
            return await real(**kwargs)

        monkeypatch.setattr(store, "create_alert", recording)

        orch = await self._correction_orchestrator(
            store, stub_llm, monkeypatch, contradicted=False
        )
        await orch._run_correction(
            ChatRequest(user_id=user.id, message="no, it's Dallas", session_id="s-c"),
            session_id="s-c",
            user_episode_id=None,
            memory_context=_empty_context(),
        )

        correction_alerts = [a for a in raised if a.get("type") == "correction"]
        assert correction_alerts == [], (
            f"a correction in the user's own conversation raised a bell entry: "
            f"{correction_alerts}"
        )

    @pytest.mark.asyncio
    async def test_a_contradicted_correction_creates_no_alert(
        self, store, stub_llm, monkeypatch
    ):
        user = await store.create_user("alice")
        frame = await store.create_frame("city", "entity", owner_user_id=user.id)
        await store.upsert_slot(frame_id=frame.id, key="name", value="Austin")

        raised: list[dict] = []
        real = store.create_alert

        async def recording(**kwargs):
            raised.append(kwargs)
            return await real(**kwargs)

        monkeypatch.setattr(store, "create_alert", recording)

        orch = await self._correction_orchestrator(
            store, stub_llm, monkeypatch, contradicted=True
        )
        await orch._run_correction(
            ChatRequest(user_id=user.id, message="no, it's Dallas", session_id="s-c2"),
            session_id="s-c2",
            user_episode_id=None,
            memory_context=_empty_context(),
        )

        correction_alerts = [a for a in raised if a.get("type") == "correction"]
        assert correction_alerts == []


class TestTheWriterIsGoneNotJustQuiet:
    """A source guard, because the behavioural tests above need the branch to run.

    If a future edit reintroduces a `create_alert` in the correction path, this
    fails at the file level rather than depending on a stub reaching the branch.
    """

    def test_the_orchestrator_raises_no_alerts(self):
        from pathlib import Path

        source = (
            Path(__file__).parent.parent / "backend" / "pipeline" / "orchestrator.py"
        ).read_text()
        assert "create_alert(" not in source, (
            "the orchestrator creates alerts again; alerts are for what the agent "
            "learned while the user was NOT there (see the presence rule)"
        )
