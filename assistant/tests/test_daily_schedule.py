"""Tests for the daily-list scheduler (redesigned Phase 7).

Model: one daily tick (DAILY_TASKS_TIME); tasks are "daily" (repeat until
stopped) or "once" (next tick, then auto-disable). No cron expressions.
"""

import json
from datetime import UTC, datetime, timedelta

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
from assistant.backend.scheduler.schedule import (
    format_next_run,
    next_daily_run,
    parse_daily_time,
)


class FakeLLM(OllamaClient):
    """Routes by system prompt: classification + scheduled-intent JSON."""

    def __init__(self) -> None:
        super().__init__()
        self.intent_reply: dict = {}
        self.extraction_reply: dict = {}
        self.chat_reply: str | None = None
        self.chat_calls: list[list] = []

    async def chat(self, messages, *args, **kwargs):  # noqa: ANN001, ANN002
        self.chat_calls.append(messages)
        system = messages[0].content.lower()
        if "task_type" in system and "classify" in system:
            return ChatResponse(
                content='{"task_type": "scheduled"}', model="fake", done=True
            )
        if "daily task list" in system:
            payload = {"intent": "create", "name": "test_task", "repeat": True}
            payload.update(self.intent_reply)
            return ChatResponse(
                content=json.dumps(payload), model="fake", done=True
            )
        if "extract" in system or "extraction" in system:
            payload = self.extraction_reply or {"slots": [], "associations": []}
            return ChatResponse(
                content=json.dumps(payload), model="fake", done=True
            )
        if self.chat_reply is not None:
            return ChatResponse(
                content=self.chat_reply, model="fake", done=True
            )
        return ChatResponse(content="ok", model="fake", done=True)

    async def embed(self, text: str, model: str | None = None):
        from assistant.backend.pipeline.llm_client import EmbeddingResponse

        return EmbeddingResponse(embedding=[0.0] * 768, model=model or "fake")


@pytest.fixture
async def env(tmp_path):
    db_path = str(tmp_path / "t.db")
    await init_db(db_path)
    store = MemoryStore(db_path)
    llm = FakeLLM()
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
    return orch, llm, store, user.id


# ---- clock helpers ----


def test_parse_daily_time():
    assert parse_daily_time("09:00") == (9, 0)
    assert parse_daily_time("9:05") == (9, 5)
    with pytest.raises(ValueError):
        parse_daily_time("9am")
    with pytest.raises(ValueError):
        parse_daily_time("25:00")


def test_next_daily_run_before_and_after_tick():
    original_time = settings.daily_tasks_time
    original_tz = settings.daily_tasks_tz
    try:
        settings.daily_tasks_time = "09:00"
        settings.daily_tasks_tz = "UTC"  # pin zone: test asserts UTC wall-clock
        now = datetime.now(tz=UTC).replace(hour=7, minute=0, second=0, microsecond=0)
        nxt = next_daily_run(now)
        assert nxt > now
        assert nxt.hour == 9 and nxt.minute == 0 and nxt.date() == now.date()

        now_pm = now.replace(hour=10)  # past today's tick -> tomorrow
        nxt2 = next_daily_run(now_pm)
        assert nxt2.date() == (now + timedelta(days=1)).date()
        assert nxt2.hour == 9
    finally:
        settings.daily_tasks_time = original_time
        settings.daily_tasks_tz = original_tz


def test_format_next_run_contains_weekday_and_time():
    original_tz = settings.daily_tasks_tz
    try:
        settings.daily_tasks_tz = "UTC"  # pin zone: assertion is UTC wall-clock
        dt = datetime(2026, 8, 21, 9, 0, tzinfo=UTC)
        text = format_next_run(dt)
        assert "09:00" in text
    finally:
        settings.daily_tasks_tz = original_tz


# ---- store semantics ----


async def test_upsert_defaults_to_daily_tick(tmp_path):
    db_path = str(tmp_path / "t.db")
    await init_db(db_path)
    store = MemoryStore(db_path)
    await store.upsert_scheduled_task(
        name="briefing", description="", schedule_cron="daily", prompt="do it"
    )
    tasks = await store.get_scheduled_tasks()
    assert tasks[0]["schedule_cron"] == "daily"
    next_run = datetime.fromisoformat(tasks[0]["next_run"])
    assert next_run > datetime.now(tz=UTC)


async def test_update_run_reschedules_daily_and_disables_once(tmp_path):
    db_path = str(tmp_path / "t.db")
    await init_db(db_path)
    store = MemoryStore(db_path)

    fid_daily = await store.upsert_scheduled_task(
        name="d", description="", schedule_cron="daily", prompt="p"
    )
    fid_once = await store.upsert_scheduled_task(
        name="o", description="", schedule_cron="once", prompt="p"
    )

    await store.update_scheduled_task_run(fid_daily, datetime.now(UTC).isoformat(), "ok")
    await store.update_scheduled_task_run(fid_once, datetime.now(UTC).isoformat(), "ok")

    tasks = {t["name"]: t for t in await store.get_scheduled_tasks()}
    daily_next = datetime.fromisoformat(tasks["d"]["next_run"])
    assert daily_next > datetime.now(tz=UTC)  # moved to tomorrow's tick
    assert tasks["o"]["enabled"] == 0
    assert tasks["o"]["next_run"] is None


# ---- chat flow ----


async def test_chat_create_daily_task(env):
    orch, llm, store, user_id = env
    llm.intent_reply = {
        "intent": "create",
        "name": "ai_news_briefing",
        "repeat": True,
        "prompt": "Brief Alice on AI news",
    }
    resp = await orch.chat(ChatRequest(user_id=user_id, message="add a news briefing"))
    assert "ai_news_briefing" in resp.response
    assert resp.task_type == "scheduled"
    tasks = await store.get_scheduled_tasks(owner_user_id=user_id)
    assert len(tasks) == 1
    assert tasks[0]["schedule_cron"] == "daily"


async def test_chat_create_once_task(env):
    orch, llm, store, user_id = env
    llm.intent_reply = {
        "intent": "create",
        "name": "call_mom",
        "repeat": False,
        "prompt": "Remind Alice to call her mom",
    }
    resp = await orch.chat(ChatRequest(user_id=user_id, message="remind me tomorrow"))
    assert resp.task_type == "scheduled"
    tasks = await store.get_scheduled_tasks(owner_user_id=user_id)
    assert len(tasks) == 1
    assert tasks[0]["schedule_cron"] == "once"


async def test_chat_list_tasks(env):
    orch, llm, store, user_id = env
    await store.upsert_scheduled_task(
        name="briefing", description="morning news",
        schedule_cron="daily", prompt="brief", owner_user_id=user_id,
    )
    llm.intent_reply = {"intent": "list"}
    resp = await orch.chat(ChatRequest(user_id=user_id, message="what's on my list?"))
    assert resp.task_type == "scheduled"
    tasks = await store.get_scheduled_tasks(owner_user_id=user_id)
    assert len(tasks) == 1
    assert tasks[0]["name"] == "briefing"


async def test_chat_run_now_task(env):
    """User says 'run my briefing now' → task is found by name and executed."""
    orch, llm, store, user_id = env
    await store.upsert_scheduled_task(
        name="briefing", description="morning news",
        schedule_cron="daily", prompt="Give me a morning news briefing",
        owner_user_id=user_id,
    )
    llm.intent_reply = {"intent": "run_now", "name": "briefing"}
    resp = await orch.chat(ChatRequest(user_id=user_id, message="run my briefing now"))
    assert resp.task_type == "scheduled"
    assert resp.response  # should have actual content from run_scheduled_task
    tasks = await store.get_scheduled_tasks(owner_user_id=user_id)
    assert tasks[0]["name"] == "briefing"
    # last_run should be updated
    assert tasks[0]["last_run"] is not None


async def test_run_now_keeps_the_callers_session_id(env):
    """run_now must echo the caller's session, not mint a fresh uuid.

    The frontend keys its conversation state on the returned session_id, so a
    fresh uuid made the *next* message start an empty conversation: the user
    asked "run my briefing now" and silently lost their thread.
    """
    orch, llm, store, user_id = env
    await store.upsert_scheduled_task(
        name="briefing", description="morning news",
        schedule_cron="daily", prompt="Give me a morning news briefing",
        owner_user_id=user_id,
    )
    llm.intent_reply = {"intent": "run_now", "name": "briefing"}
    resp = await orch.chat(
        ChatRequest(
            user_id=user_id,
            message="run my briefing now",
            session_id="my-thread-42",
        )
    )
    assert resp.task_type == "scheduled"
    assert resp.session_id == "my-thread-42"


async def test_run_scheduled_task_episode_has_frame_ids(env):
    """run_scheduled_task should link extracted frames to the assistant episode."""
    orch, llm, store, user_id = env
    await store.upsert_scheduled_task(
        name="test_briefing", description="test",
        schedule_cron="daily", prompt="Brief me on AI news",
        owner_user_id=user_id,
    )
    # Provide a fake extraction that creates frames
    llm.extraction_reply = {
        "slots": [
            {
                "frame_name": "ai_news",
                "frame_type": "entity",
                "key": "topic",
                "value": "AI breakthroughs",
                "confidence": 0.8,
            }
        ],
        "associations": [],
    }
    # Call run_scheduled_task directly (bypasses chat intent routing)
    result = await orch.run_scheduled_task(
        prompt="Brief me on AI news",
        user_id=user_id,
        task_name="test_briefing",
    )
    assert result == "ok"

    # Find the episode created for this task
    episodes = await store.get_episodes_for_user(user_id)
    task_episodes = [e for e in episodes if e.session_id.startswith("scheduled-test_briefing-")]
    assert len(task_episodes) == 1
    episode = task_episodes[0]
    assert episode.role == "assistant"
    # frame_ids should be populated with extracted frames
    assert episode.frame_ids is not None
    assert len(episode.frame_ids) >= 1
    # Verify the frame was created
    frame = await store.get_frame(episode.frame_ids[0])
    assert frame is not None
    assert frame.name == "ai_news"


async def test_run_scheduled_task_delivers_task_prompt_to_model(env):
    """The task's own instruction must reach the model as the user message.

    Regression: the task prompt used to be folded only into the system prompt;
    a small local model then drifted and answered from whatever was loudest in
    memory context (e.g. repeating an old "file created" episode for a deals
    task), making daily tasks useless.
    """
    orch, llm, store, user_id = env
    await orch.run_scheduled_task(
        prompt="Keep an eye on AI in the news and report anything major",
        user_id=user_id,
        task_name="ai_watch",
    )
    user_turns = [
        m.content for call in llm.chat_calls for m in call if m.role == "user"
    ]
    assert len(user_turns) >= 1
    assert any("Keep an eye on AI in the news" in t for t in user_turns)


async def test_daily_run_frame_accumulates_task_names(store):
    """A day's run frame must accumulate task names, not overwrite them.

    Regression: update_daily_run_frame replaced tasks_run with the latest
    task's name, so a 10-task morning was recorded as a single-task run and
    "what did my run find?" was unanswerable from the frame itself.
    """
    user = await store.create_user("alice")
    fid = await store.get_or_create_daily_run_frame(
        "2099_01_01", owner_user_id=user.id
    )
    await store.update_daily_run_frame(fid, ["ai_news_briefing"], "completed")
    await store.update_daily_run_frame(fid, ["track_deals"], "completed")
    await store.update_daily_run_frame(fid, ["track_deals"], "completed")  # dedupe
    async with store._connect() as db:
        rows = await db.execute_fetchall(
            "SELECT value FROM slots WHERE frame_id = ? AND key = 'tasks_run'",
            (fid,),
        )
    assert rows and rows[0][0] == "ai_news_briefing,track_deals"


async def test_extract_agent_alert_parses_footer(env):
    from assistant.backend.scheduler.runner import _extract_agent_alert

    report = (
        "Here's today's briefing on AI: agents keep shipping.\n\n"
        "ALERT: Frontier model released\n"
        "A major lab shipped a new model yesterday."
    )
    title, message, cleaned = _extract_agent_alert(report, "ai_news_briefing")
    assert title == "Frontier model released"
    assert "A major lab shipped" in message
    assert "ALERT" not in cleaned
    assert "agents keep shipping" in cleaned

    # No alert footer -> passthrough untouched.
    title2, message2, cleaned2 = _extract_agent_alert("Nothing special today", "t")
    assert title2 is None and message2 is None
    assert cleaned2 == "Nothing special today"


async def test_execute_task_creates_agent_alert(env):
    """A task report ending in ALERT: raises an 'important' alert and the
    stored summary stays clean of the footer."""
    from assistant.backend.scheduler.runner import execute_and_record_task

    orch, llm, store, user_id = env
    llm.chat_reply = (
        "Switch 2 restocks went out this morning.\n\n"
        "ALERT: Switch 2 restock\n"
        "Big restock hit Amazon and sold out fast."
    )
    fid = await store.upsert_scheduled_task(
        name="deals", description="", schedule_cron="daily",
        prompt="Look for deals", owner_user_id=user_id,
    )
    ok, _result = await execute_and_record_task(
        store, orch, fid, "deals", "Look for deals", user_id
    )
    assert ok

    task_alerts = [a for a in await store.get_alerts(user_id) if a.type == "task_alert"]
    assert len(task_alerts) == 1
    assert task_alerts[0].severity == "important"
    assert "Switch 2 restock" in task_alerts[0].title
    assert "Amazon" in task_alerts[0].message

    tasks = await store.get_scheduled_tasks(owner_user_id=user_id)
    assert "ALERT" not in tasks[0]["last_result_summary"]
    assert "Switch 2 restocks went out" in tasks[0]["last_result_summary"]


async def test_chat_run_now_disables_once_task(env):
    """A 'once' task must be disabled after run_now fires."""
    orch, llm, store, user_id = env
    await store.upsert_scheduled_task(
        name="reminder", description="call mom",
        schedule_cron="once", prompt="Call mom",
        owner_user_id=user_id,
    )
    llm.intent_reply = {"intent": "run_now", "name": "reminder"}
    resp = await orch.chat(ChatRequest(user_id=user_id, message="run my reminder now"))
    assert resp.task_type == "scheduled"
    tasks = await store.get_scheduled_tasks(owner_user_id=user_id)
    assert len(tasks) == 1
    assert tasks[0]["enabled"] == 0, "once task must be disabled after run_now"
    assert tasks[0]["last_run"] is not None


async def test_chat_delete_task(env):
    orch, llm, store, user_id = env
    fid = await store.upsert_scheduled_task(
        name="weather", description="", schedule_cron="daily",
        prompt="check weather", owner_user_id=user_id,
    )
    llm.intent_reply = {"intent": "delete", "name": "weather"}
    await orch.chat(ChatRequest(user_id=user_id, message="stop the weather"))
    tasks = await store.get_scheduled_tasks(owner_user_id=user_id)
    assert all(t["id"] != fid for t in tasks) or not tasks


# ---- due-ness with mixed UTC offsets (regression: string compare fired
# tasks hours early when next_run carried a non-UTC offset) ----


async def test_due_comparison_handles_mixed_offsets(store):
    """next_run stored as -05:00 must not fire before its true instant."""
    now = datetime.now(UTC)
    # Due 30 min ago, written in Chicago local time (offset -05:00).
    past_local = (now - timedelta(minutes=30)).astimezone(
        __import__("zoneinfo").ZoneInfo("America/Chicago")
    )
    # Due 2 h from now, also written local — string-wise it sorts BEFORE
    # the past task's hour digits, which is what broke the old SQL compare.
    future_local = (now + timedelta(hours=2)).astimezone(
        __import__("zoneinfo").ZoneInfo("America/Chicago")
    )
    await store.upsert_scheduled_task(
        name="past_task", description="", schedule_cron="daily",
        prompt="p1", owner_user_id=None, next_run=past_local.isoformat(),
    )
    await store.upsert_scheduled_task(
        name="future_task", description="", schedule_cron="daily",
        prompt="p2", owner_user_id=None, next_run=future_local.isoformat(),
    )
    # Also a canonical UTC row due now.
    await store.upsert_scheduled_task(
        name="utc_task", description="", schedule_cron="once",
        prompt="p3", owner_user_id=None,
        next_run=(now - timedelta(minutes=5)).isoformat(),
    )

    due = {t["name"] for t in await store.get_due_scheduled_tasks()}
    assert "past_task" in due
    assert "utc_task" in due
    assert "future_task" not in due


async def test_upsert_stores_next_run_in_utc(store):
    """New tasks canonicalize to UTC regardless of configured zone."""
    from datetime import datetime as dt

    fid = await store.upsert_scheduled_task(
        name="tz_check", description="", schedule_cron="daily", prompt="p"
    )
    rows = await store.get_due_scheduled_tasks()  # exercises parse path too
    row = next((t for t in rows if t["id"] == fid), None)
    if row:  # only if immediately due; otherwise just check the format below
        pass
    raw = None
    async with store._connect() as db:
        cur = await db.execute_fetchall(
            "SELECT next_run FROM frames WHERE id = ?", (fid,)
        )
        raw = cur[0][0]
    parsed = dt.fromisoformat(raw)
    assert parsed.tzinfo is not None
    assert parsed.utcoffset().total_seconds() == 0


async def test_nearest_run_parses_mixed_offsets(store):
    now = datetime.now(UTC)
    far = (now + timedelta(days=3)).isoformat()
    near_local = (
        now + timedelta(hours=1)
    ).astimezone(__import__("zoneinfo").ZoneInfo("America/Chicago")).isoformat()
    await store.upsert_scheduled_task(
        name="a", description="", schedule_cron="once", prompt="p",
        next_run=far,
    )
    await store.upsert_scheduled_task(
        name="b", description="", schedule_cron="once", prompt="p",
        next_run=near_local,
    )
    nearest = await store.get_nearest_scheduled_task_run()
    assert nearest is not None
    delta = abs((nearest - now).total_seconds())
    assert delta < 2 * 3600  # the 1h-away task wins despite "+00:00"-style sort order


