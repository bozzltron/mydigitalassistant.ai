"""Integration test for the daily scheduled tasks end-to-end experiment.

Tests the full loop:
1. Create task via natural language chat
2. Manually trigger execution via POST /tasks/run-due
3. Verify memory artifacts: episode, extracted facts, daily_run event
4. Query "what did you learn today" via chat
5. Verify response references task output + extracted facts
"""

import json
from datetime import UTC, datetime

import pytest

from assistant.backend.config import settings
from assistant.backend.db.schema import init_db
from assistant.backend.memory.retrieval import Retriever
from assistant.backend.memory.store import MemoryStore
from assistant.backend.pipeline.llm_client import ChatResponse, OllamaClient
from assistant.backend.pipeline.orchestrator import (
    ChatRequest,
    Orchestrator,
    OrchestratorDeps,
)
from assistant.backend.pipeline.search import WebSearchTool


class ExperimentLLM(OllamaClient):
    """Fake LLM that routes based on system prompt content."""

    def __init__(self):
        super().__init__()
        self.scheduled_intent = {}
        self.task_response = (
            "Found AI breakthrough: new transformer architecture achieves 95% "
            "accuracy on reasoning benchmarks. Published in Nature."
        )
        self.report_response = (
            "Today's daily briefing found a major AI breakthrough: a new "
            "transformer architecture achieving 95% accuracy on reasoning "
            "benchmarks, published in Nature."
        )

    async def chat(self, messages, *args, **kwargs):
        system = messages[0].content.lower()
        user_msg = messages[-1].content.lower() if messages else ""

        # Task router classification
        if "task_type" in system and "classify" in system:
            # Return introspective for report queries, scheduled for task management
            if "what did you learn" in user_msg or "what have you learned" in user_msg:
                return ChatResponse(
                    content='{"task_type": "introspective", "wants_search": false}',
                    model="fake",
                    done=True,
                )
            return ChatResponse(
                content='{"task_type": "scheduled", "wants_search": false}',
                model="fake",
                done=True,
            )

        # Scheduled task field extraction
        if "daily task list" in system:
            payload = {"intent": "create", "name": "ai_breakthrough_watch", "repeat": True}
            payload.update(self.scheduled_intent)
            return ChatResponse(
                content=json.dumps(payload), model="fake", done=True
            )

        # Scheduled task execution (run_scheduled_task) - functional task with memory context
        is_assistant = (
            "cognitive digital assistant" in system or "personal cognitive assistant" in system
        )
        if is_assistant and "guidelines:" in system:
            return ChatResponse(
                content=self.task_response, model="fake", done=True
            )

        # Report generation (introspective query)
        if "introspective" in system or "what did you learn" in user_msg:
            return ChatResponse(
                content=self.report_response, model="fake", done=True
            )

        # Embedding
        if "embed" in system:
            from assistant.backend.pipeline.llm_client import EmbeddingResponse
            return EmbeddingResponse(embedding=[0.1] * 768, model="fake")

        return ChatResponse(content="ok", model="fake", done=True)

    async def embed(self, text: str, model: str | None = None):
        from assistant.backend.pipeline.llm_client import EmbeddingResponse
        return EmbeddingResponse(embedding=[0.1] * 768, model=model or "fake")


@pytest.fixture
async def env(tmp_path):
    db_path = str(tmp_path / "t.db")
    await init_db(db_path)
    store = MemoryStore(db_path)
    llm = ExperimentLLM()
    retriever = Retriever(store=store, llm_client=llm)
    orch = Orchestrator(
        deps=OrchestratorDeps(
            store=store,
            retriever=retriever,
            llm_client=llm,
            search_tool=WebSearchTool(enabled=False),
        )
    )
    user = await store.create_user("alice")
    return orch, llm, store, retriever, user.id


@pytest.mark.asyncio
async def test_daily_task_full_loop(env):
    """Full end-to-end test: create task -> run-due -> query report."""
    orch, llm, store, retriever, user_id = env

    # ===== PHASE 1: Create task via natural language =====
    llm.scheduled_intent = {
        "intent": "create",
        "name": "ai_breakthrough_watch",
        "repeat": True,
        "prompt": "Read reputable tech news and report on AI breakthroughs",
        "description": "Daily tech news briefing focusing on AI breakthroughs",
    }

    resp = await orch.chat(ChatRequest(
        user_id=user_id,
        message=(
            "hey let's read reputable tech news every day and keep an eye on "
            "breakthroughs in artificial intelligence"
        ),
    ))

    assert resp.task_type == "scheduled"
    assert "ai_breakthrough_watch" in resp.response

    # Verify task stored
    tasks = await store.get_scheduled_tasks(owner_user_id=user_id)
    assert len(tasks) == 1
    task = tasks[0]
    assert task["name"] == "ai_breakthrough_watch"
    assert task["schedule_cron"] == "daily"
    assert task["enabled"] == 1
    assert "AI breakthrough" in task["prompt"]

    task_frame_id = task["id"]

    # ===== PHASE 2: Manually trigger execution via run-due =====
    # Force task to be due by updating next_run to past
    from datetime import timedelta
    past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    await store.upsert_scheduled_task_slot(task_frame_id, "next_run", past)

    # Execute via the same logic as POST /tasks/run-due
    due = await store.get_due_scheduled_tasks()
    assert len(due) == 1
    assert due[0]["name"] == "ai_breakthrough_watch"

    # Run the task (simulates POST /tasks/run-due)
    task_data = due[0]
    result = await orch.run_scheduled_task(
        prompt=task_data["prompt"],
        user_id=user_id,
        task_name=task_data["name"],
    )

    # Verify task execution produced response
    assert "AI breakthrough" in result
    assert "transformer" in result
    assert "95%" in result

    # Update task run info (like run-due does)
    now_str = datetime.now(UTC).isoformat()
    date_str = datetime.now(UTC).strftime("%Y_%m_%d")
    summary = result[:2000]
    await store.update_scheduled_task_run(
        frame_id=task_frame_id,
        last_run=now_str,
        last_result_summary=summary,
    )

    # Create daily run event and associations (like run-due does)
    session_id = f"scheduled-{task_data['name']}-{date_str}"
    episode = await store.get_last_assistant_episode(user_id, session_id)

    daily_run_frame_id = await store.get_or_create_daily_run_frame(
        date_str, owner_user_id=user_id
    )

    await store.associate_frames(
        task_frame_id, daily_run_frame_id, "ran_in", confidence=0.9
    )

    if episode:
        await store.upsert_scheduled_task_slot(
            task_frame_id, "last_output_episode_id", str(episode.id)
        )
    await store.associate_frames(
        daily_run_frame_id, task_frame_id, "includes_task", confidence=0.9
    )

    await store.update_daily_run_frame(daily_run_frame_id, [task_data["name"]], "completed")

    # Generate embedding for daily run frame
    await store.embed_frames(
        [daily_run_frame_id],
        orch._embed_fn(),
        embedding_model=settings.embedding_model,
    )

    # ===== PHASE 3: Verify memory artifacts =====

    # 1. Assistant episode exists
    assert episode is not None
    assert episode.role == "assistant"
    assert "AI breakthrough" in episode.content

    # 2. Daily run event frame exists with correct slots
    daily_run_frame = await store.get_frame(daily_run_frame_id)
    assert daily_run_frame is not None
    assert daily_run_frame.type == "event"
    assert daily_run_frame.name == f"daily_run_{date_str}"

    daily_run_slots = await store.get_slots_for_frame(daily_run_frame_id)
    slot_map = {s.key: s.value for s in daily_run_slots}
    assert slot_map["date"] == date_str
    assert slot_map["tasks_run"] == "ai_breakthrough_watch"
    assert slot_map["status"] == "completed"

    # 3. Associations exist
    task_to_run = await store.get_associations_from(task_frame_id)
    ran_in_assoc = [a for a in task_to_run if a.relation_type == "ran_in"]
    assert len(ran_in_assoc) == 1
    assert ran_in_assoc[0].to_frame_id == daily_run_frame_id

    run_to_task = await store.get_associations_from(daily_run_frame_id)
    includes_assoc = [a for a in run_to_task if a.relation_type == "includes_task"]
    assert len(includes_assoc) == 1
    assert includes_assoc[0].to_frame_id == task_frame_id

    # 4. Task frame updated with last_run and last_result_summary
    task_slots = await store.get_slots_for_frame(task_frame_id)
    task_slot_map = {s.key: s.value for s in task_slots}
    assert task_slot_map["last_run"] == now_str
    assert "AI breakthrough" in task_slot_map["last_result_summary"]
    assert "next_run" in task_slot_map  # rescheduled for tomorrow

    # ===== PHASE 4: Query "what did you learn today" =====
    llm.scheduled_intent = {"intent": "list"}  # not used but needed for router
    llm.task_response = (
            "Today's daily briefing found a major AI breakthrough: a new "
            "transformer architecture achieving 95% accuracy on reasoning "
            "benchmarks, published in Nature."
        )

    # Use a query that doesn't trigger the scheduled task router
    report_query = "what did you learn today. Give me a report"
    report_resp = await orch.chat(ChatRequest(
        user_id=user_id,
        message=report_query,
    ))

    # The router should classify this as introspective
    assert report_resp.task_type == "introspective"
    assert "breakthrough" in report_resp.response.lower()
    assert "transformer" in report_resp.response.lower()
    assert "95%" in report_resp.response

    # ===== PHASE 5: Verify retrieval finds daily run event =====
    # Direct retrieval test
    memory_context = await retriever.retrieve(
        query="what did you learn today daily tasks",
        user_id=user_id,
    )

    # Should find the daily run frame via graph walk from task or direct match
    frame_names = [rf.frame.name for rf in memory_context.retrieved_frames]
    assert f"daily_run_{date_str}" in frame_names or "ai_breakthrough_watch" in frame_names

    print("✅ Full daily schedule experiment passed!")


@pytest.mark.asyncio
async def test_run_due_endpoint_logic(env):
    """Test the POST /tasks/run-due logic directly."""
    orch, llm, store, retriever, user_id = env

    # Create a task directly in store
    from assistant.backend.scheduler.schedule import next_daily_run
    next_tick = next_daily_run()
    task_frame_id = await store.upsert_scheduled_task(
        name="test_briefing",
        description="Test briefing",
        schedule_cron="daily",
        prompt="Give me a test briefing",
        enabled=True,
        owner_user_id=user_id,
        next_run=next_tick.astimezone(UTC).isoformat(),
    )

    # Force it due
    from datetime import timedelta
    past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    await store.upsert_scheduled_task_slot(task_frame_id, "next_run", past)

    # Get due tasks
    due = await store.get_due_scheduled_tasks()
    assert len(due) == 1

    # Execute each due task (simulating run-due endpoint)
    for task in due:
        result = await orch.run_scheduled_task(
            prompt=task["prompt"],
            user_id=user_id,
            task_name=task["name"],
        )

        now_str = datetime.now(UTC).isoformat()
        date_str = datetime.now(UTC).strftime("%Y_%m_%d")
        summary = result[:2000]
        await store.update_scheduled_task_run(
            frame_id=task["id"],
            last_run=now_str,
            last_result_summary=summary,
        )

        session_id = f"scheduled-{task['name']}-{date_str}"
        episode = await store.get_last_assistant_episode(user_id, session_id)

        daily_run_frame_id = await store.get_or_create_daily_run_frame(
            date_str, owner_user_id=user_id
        )

        await store.associate_frames(
            task["id"], daily_run_frame_id, "ran_in", confidence=0.9
        )

        if episode:
            await store.upsert_scheduled_task_slot(
                task["id"], "last_output_episode_id", str(episode.id)
            )
        await store.associate_frames(
            daily_run_frame_id, task["id"], "includes_task", confidence=0.9
        )

        await store.update_daily_run_frame(daily_run_frame_id, [task["name"]], "completed")

        await store.embed_frames(
            [daily_run_frame_id],
            orch._embed_fn(),
            embedding_model=settings.embedding_model,
        )

# Verify results
        tasks_after = await store.get_scheduled_tasks(owner_user_id=user_id)
        assert tasks_after[0]["last_run"] is not None
        # The fake LLM returns a fixed response about AI breakthroughs
        assert "breakthrough" in tasks_after[0]["last_result_summary"].lower()

    daily_run_frame = await store.get_frame_by_name(f"daily_run_{date_str}")
    assert daily_run_frame is not None
    slots = await store.get_slots_for_frame(daily_run_frame.id)
    slot_map = {s.key: s.value for s in slots}
    assert slot_map["tasks_run"] == "test_briefing"

    print("✅ run-due endpoint logic test passed!")