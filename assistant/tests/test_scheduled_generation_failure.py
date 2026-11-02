"""A scheduled task must not file a swallowed generation failure as its answer.

Replay of the production incident (2026-11-01):

    10:59:35.798 ERROR  Streaming generation failed: ReadTimeout('')
    10:59:36.331 INFO   Task 'ai_news_briefing' completed successfully

The loop's generation-failure terminal branch degrades to a readable sentence for
the user (correct for live chat) and then ended the turn *as if it were an
answer*. `run_scheduled_task` returned that sentence, the runner's success branch
stored it as the task's output and logged `completed successfully`, and the
runner's `except` branch — which writes `last_result_summary="failed: …"` and
raises the `task_failure` alert the presence rule requires — was unreachable for
the one failure that actually happened.

The outcome is carried out **structurally** (a flag on the value the loop returns)
and never by matching the failure message's text: a phrasing detector is forbidden
by AGENTS.md. `test_a_task_whose_answer_merely_reads_like_the_failure_sentence…`
pins that distinction from the other side.

Tests here use a `tmp_path` store and a stubbed LLM; no brain, no network.
"""

from __future__ import annotations

import json
import logging

import pytest

import assistant.backend.pipeline.streaming as streaming
from assistant.backend.config import settings
from assistant.backend.memory.retrieval import Retriever
from assistant.backend.pipeline.llm_client import (
    EMPTY_GENERATION_FALLBACK,
    ChatResponse,
    ToolCall,
)
from assistant.backend.pipeline.orchestrator import (
    _GENERATION_FAILURE_MESSAGE,
    ChatRequest,
    Orchestrator,
    OrchestratorDeps,
)
from assistant.backend.pipeline.search import WebSearchTool
from assistant.backend.scheduler.runner import execute_and_record_task

from .conftest import StubLLMClient

TASK_NAME = "ai_news_briefing"
TASK_PROMPT = "Summarise today's AI news and flag anything major"


async def _collect(agen):
    events = []
    async for line in agen:
        if line.startswith("data: "):
            events.append(json.loads(line[len("data: "):].strip()))
    return events


def _orchestrator(store, llm) -> Orchestrator:
    return Orchestrator(
        deps=OrchestratorDeps(
            store=store,
            retriever=Retriever(store=store, llm_client=llm),
            llm_client=llm,
            search_tool=WebSearchTool(enabled=False),
        )
    )


async def _generation_times_out(*args, **kwargs):
    """The incident: the generation call raises before producing any answer."""
    raise TimeoutError("ReadTimeout('')")
    yield  # pragma: no cover - makes this an async generator


def _answers_once(answer: str):
    """A tool loop that returns `answer` with no tool calls."""

    async def fake_loop(llm_client, messages, tools, **kwargs):
        yield streaming.serialize_event(streaming.FinalizeEvent(answer, None))

    return fake_loop


def _tool_errors_then_answers(answer: str):
    """A tool loop whose tool call fails but which still produces an answer."""

    async def fake_loop(llm_client, messages, tools, **kwargs):
        yield streaming.serialize_event(
            streaming.ToolResultEvent(
                tool_name="web_search",
                success=False,
                data=None,
                error="the search backend timed out",
            )
        )
        yield streaming.serialize_event(streaming.FinalizeEvent(answer, None))

    return fake_loop


async def _scheduled_task(store, user_id: int, name: str = TASK_NAME) -> int:
    return await store.upsert_scheduled_task(
        name=name,
        description="A morning briefing on AI news.",
        schedule_cron="daily",
        prompt=TASK_PROMPT,
        owner_user_id=user_id,
    )


# 1. The replay: a generation-failing scheduled task is a failed task ----------


@pytest.mark.asyncio
async def test_scheduled_task_whose_generation_failed_is_recorded_as_failed(
    store, stub_llm, monkeypatch, caplog
):
    """AC1 — the incident, replayed through the scheduler's recording path.

    Red on HEAD: the run returns success, stores the failure sentence as the
    task's output, logs `completed successfully` and raises no alert.
    """
    user = await store.create_user("alice")
    frame_id = await _scheduled_task(store, user.id)
    orchestrator = _orchestrator(store, stub_llm)
    monkeypatch.setattr(streaming, "stream_tool_loop", _generation_times_out)

    caplog.set_level(logging.INFO)
    ok, result = await execute_and_record_task(
        store, orchestrator, frame_id, TASK_NAME, TASK_PROMPT, user.id
    )

    # The run is a failure, and the failure text is not served as the answer.
    assert ok is False, "a generation-failing task was recorded as a success"
    assert result.startswith("failed:"), result

    summary = await store.get_slot(frame_id, "last_result_summary")
    assert summary is not None, "the run left no summary at all"
    assert summary.value.startswith("failed:"), (
        f"the run's output was filed as success: {summary.value!r}"
    )
    assert _GENERATION_FAILURE_MESSAGE not in summary.value, (
        "the live-chat failure sentence was stored as the task's output"
    )

    # Exactly one `task_failure` alert, for this task.
    alerts = await store.get_alerts(user.id)
    failures = [a for a in alerts if a.type == "task_failure"]
    assert len(failures) == 1, f"expected one task_failure alert, got {alerts}"
    assert TASK_NAME in failures[0].title

    # And the run was announced as a failure, not a success. Both halves matter:
    # without the first line the second could pass because nothing was logged.
    assert "Task 'ai_news_briefing' failed" in caplog.text
    assert "completed successfully" not in caplog.text, (
        "a failed run still logged `completed successfully`"
    )


# 2. Live chat is unchanged ---------------------------------------------------


@pytest.mark.asyncio
async def test_live_chat_still_receives_the_failure_sentence_and_raises_no_alert(
    store, stub_llm, monkeypatch
):
    """AC2 — the user is present, so they get the sentence and no bell entry.

    This passes before and after the fix: it is the guard that the scheduler's new
    outcome did not leak into the live path (no raise, no alert, same events).
    """
    user = await store.create_user("alice")
    orchestrator = _orchestrator(store, stub_llm)
    monkeypatch.setattr(streaming, "stream_tool_loop", _generation_times_out)

    alerts: list[dict] = []
    real_create_alert = store.create_alert

    async def recording_create_alert(**kwargs):
        alerts.append(kwargs)
        return await real_create_alert(**kwargs)

    monkeypatch.setattr(store, "create_alert", recording_create_alert)

    events = await _collect(
        orchestrator.chat_stream(
            ChatRequest(user_id=user.id, message="Hello", session_id="s-live-fail")
        )
    )

    types = [e["type"] for e in events]
    finals = [e["answer"] for e in events if e["type"] == "finalize"]
    assert finals[-1] == _GENERATION_FAILURE_MESSAGE
    assert "error" not in types, "a live turn surfaced a stream error event"
    assert types.count("meta") == 1, "the turn did not end with exactly one meta"
    assert types[-1] == "meta"
    assert alerts == [], f"a live-chat failure raised an alert: {alerts}"


# 3. The outcome is structural ------------------------------------------------


@pytest.mark.asyncio
async def test_the_generation_failure_is_a_structural_flag_on_the_meta_event(
    store, stub_llm, monkeypatch
):
    """AC3 — the loop reports *which branch it ended in*, not what the text says.

    Red on HEAD: the meta event carries no such field, so the only thing a caller
    could have matched on was the message's wording.
    """
    user = await store.create_user("alice")
    orchestrator = _orchestrator(store, stub_llm)

    monkeypatch.setattr(streaming, "stream_tool_loop", _generation_times_out)
    failed = await _collect(
        orchestrator.chat_stream(
            ChatRequest(user_id=user.id, message="Hello", session_id="s-flag-fail")
        )
    )

    monkeypatch.setattr(streaming, "stream_tool_loop", _answers_once("All good."))
    normal = await _collect(
        orchestrator.chat_stream(
            ChatRequest(user_id=user.id, message="Hello", session_id="s-flag-ok")
        )
    )

    failed_meta = [e for e in failed if e["type"] == "meta"][-1]
    normal_meta = [e for e in normal if e["type"] == "meta"][-1]
    assert failed_meta["generation_failed"] is True
    assert normal_meta["generation_failed"] is False


@pytest.mark.asyncio
async def test_the_no_tools_generation_branch_also_marks_the_turn_failed(
    store, stub_llm, monkeypatch
):
    """The loop has two generation-failure branches; both must carry the outcome.

    With `TOOLS_ENABLED=false` the turn streams the model directly instead of
    through `stream_tool_loop`. That branch is unreachable in the default
    configuration, so a half-fix that marked only the tool branch would look green
    and would silently start failing again for anyone running without tools.
    """
    user = await store.create_user("alice")
    orchestrator = _orchestrator(store, stub_llm)

    async def streaming_times_out(*args, **kwargs):
        raise TimeoutError("ReadTimeout('')")
        yield  # pragma: no cover - makes this an async generator

    monkeypatch.setattr(settings, "tools_enabled", False)
    monkeypatch.setattr(stub_llm, "chat_stream", streaming_times_out)

    events = await _collect(
        orchestrator.chat_stream(
            ChatRequest(user_id=user.id, message="Hello", session_id="s-no-tools-fail")
        )
    )

    finals = [e["answer"] for e in events if e["type"] == "finalize"]
    meta = [e for e in events if e["type"] == "meta"][-1]
    assert finals[-1] == _GENERATION_FAILURE_MESSAGE
    assert meta["generation_failed"] is True


# 4. A phrasing detector would fail this --------------------------------------

@pytest.mark.asyncio
async def test_a_task_whose_answer_merely_reads_like_the_failure_sentence_succeeds(
    store, stub_llm, monkeypatch
):
    """AC3, from the other side: an *answer* with the failure's wording is an answer.

    A detector that matched the message text would record this run as failed. The
    model really said those words and the task really produced its report, so it
    is a success. Guard: green before and after — it exists to make the forbidden
    implementation (text matching) fail.
    """
    user = await store.create_user("alice")
    frame_id = await _scheduled_task(store, user.id)
    orchestrator = _orchestrator(store, stub_llm)
    # A successful generation that happens to say exactly the failure sentence.
    monkeypatch.setattr(
        streaming, "stream_tool_loop", _answers_once(_GENERATION_FAILURE_MESSAGE)
    )

    ok, result = await execute_and_record_task(
        store, orchestrator, frame_id, TASK_NAME, TASK_PROMPT, user.id
    )

    assert ok is True, "a task that answered was recorded as failed"
    assert result == _GENERATION_FAILURE_MESSAGE
    alerts = await store.get_alerts(user.id)
    assert [a for a in alerts if a.type == "task_failure"] == []


# 5. A failed tool call is not a failed generation ----------------------------


@pytest.mark.asyncio
async def test_a_failed_tool_call_in_a_successful_run_is_not_a_task_failure(
    store, stub_llm, monkeypatch
):
    """The plan's weakest link: the flag marks the loop's terminal branch only.

    A task that produced its answer after a tool error is a success — otherwise a
    flawlessly-handled search hiccup would alert the user and overwrite the run's
    output with `failed:`. Guard: green before and after.
    """
    user = await store.create_user("alice")
    frame_id = await _scheduled_task(store, user.id)
    orchestrator = _orchestrator(store, stub_llm)
    monkeypatch.setattr(
        streaming,
        "stream_tool_loop",
        _tool_errors_then_answers("Three releases shipped; the search timed out once."),
    )

    ok, result = await execute_and_record_task(
        store, orchestrator, frame_id, TASK_NAME, TASK_PROMPT, user.id
    )

    assert ok is True, "a tool error was recorded as a task failure"
    assert "Three releases shipped" in result
    alerts = await store.get_alerts(user.id)
    assert [a for a in alerts if a.type == "task_failure"] == []


# 6. The chat-side "run my briefing now" sibling -------------------------------


class _ScheduledIntentStub(StubLLMClient):
    """conftest's stub, plus the two prompts the run-now route needs."""

    def __init__(self, intent: dict) -> None:
        super().__init__()
        self._intent = intent

    async def chat(self, messages, *args, **kwargs):
        system = messages[0].content.lower()
        if "daily task list" in system:
            return ChatResponse(
                content=json.dumps(self._intent), model=self.utility_model, done=True
            )
        if "classify" in system:
            return ChatResponse(
                content='{"task_type": "scheduled"}',
                model=self.utility_model,
                done=True,
            )
        return await super().chat(messages, *args, **kwargs)


@pytest.mark.asyncio
async def test_run_now_whose_generation_failed_tells_the_user_and_records_it_failed(
    store, stub_search, monkeypatch
):
    """AC1's sibling: the live "run it now" route runs a task, so it records one.

    The user is present, so they get the failure sentence and no alert — but the
    run must not be filed as the task's output either. Red on HEAD, where the
    sentence was stored as the summary.
    """
    llm = _ScheduledIntentStub({"intent": "run_now", "name": TASK_NAME})
    user = await store.create_user("alice")
    frame_id = await _scheduled_task(store, user.id)
    orchestrator = Orchestrator(
        deps=OrchestratorDeps(
            store=store, retriever=Retriever(store=store, llm_client=llm),
            llm_client=llm, search_tool=stub_search,
        )
    )
    monkeypatch.setattr(streaming, "stream_tool_loop", _generation_times_out)

    events = await _collect(
        orchestrator.chat_stream(
            ChatRequest(
                user_id=user.id, message="run my briefing now", session_id="s-run-now"
            )
        )
    )

    finals = [e["answer"] for e in events if e["type"] == "finalize"]
    assert finals, "the run-now turn produced no answer"
    assert finals[-1] == _GENERATION_FAILURE_MESSAGE

    summary = await store.get_slot(frame_id, "last_result_summary")
    assert summary is not None, "the run-now path recorded nothing"
    assert summary.value.startswith("failed:"), (
        f"the failure sentence was filed as the run's output: {summary.value!r}"
    )
    alerts = await store.get_alerts(user.id)
    assert [a for a in alerts if a.type == "task_failure"] == [], (
        "a failure the user was present for raised an alert"
    )


# 7. An empty answer is the same failure, one branch over ----------------------
#
# `sf1`'s verifier found `v-01`: the loop has a *second* way to end with no
# answer. When the model returns empty content, `streaming.py` substitutes
# `EMPTY_GENERATION_FALLBACK` on the **normal** finalize branch, so the
# generation-failure flag stayed False and a scheduled run filed the sentence as
# its output. Same user-visible lie, one branch over.
#
# The section runs the **real** `stream_tool_loop` (no monkeypatch): the defect
# lives in the loop's own finalize, so a monkeypatched finalize would not
# reproduce it. Only the LLM boundary is stubbed.


class _GenerationAnswers(StubLLMClient):
    """conftest's stub, but the *generation* call answers with `content`.

    Keyed on the call carrying a tool schema: `stream_tool_loop` is the only
    caller that sends one, so routing, retrieval, extraction and the decision
    model keep their deterministic stub answers while the turn's answer is
    whatever this class was constructed with — empty for the `v-01` replay.
    """

    def __init__(self, content: str) -> None:
        super().__init__()
        self._generation_content = content

    async def chat(self, messages, *args, **kwargs):
        if kwargs.get("tools"):
            return ChatResponse(
                content=self._generation_content, model=self.chat_model, done=True
            )
        return await super().chat(messages, *args, **kwargs)


def _no_content_llm() -> _GenerationAnswers:
    """The model emits nothing at all, twice: the empty-generation retry is spent."""
    return _GenerationAnswers("")


@pytest.mark.asyncio
async def test_scheduled_task_whose_model_returned_no_content_is_recorded_as_failed(
    store, monkeypatch, caplog
):
    """AC1 — the `v-01` replay: empty content is a failed run, not a briefing.

    Red on HEAD: the loop substitutes the fallback sentence on its normal
    finalize branch, so the run returns success, stores the sentence as the
    task's output, logs `completed successfully` and raises no alert.
    """
    user = await store.create_user("alice")
    frame_id = await _scheduled_task(store, user.id)
    orchestrator = _orchestrator(store, _no_content_llm())

    caplog.set_level(logging.INFO)
    ok, result = await execute_and_record_task(
        store, orchestrator, frame_id, TASK_NAME, TASK_PROMPT, user.id
    )

    assert ok is False, "a run that produced no answer was recorded as a success"
    assert result.startswith("failed:"), result

    summary = await store.get_slot(frame_id, "last_result_summary")
    assert summary is not None, "the run left no summary at all"
    assert summary.value.startswith("failed:"), (
        f"the run with no answer was filed as success: {summary.value!r}"
    )
    assert EMPTY_GENERATION_FALLBACK not in summary.value, (
        "the empty-generation sentence was stored as the task's output"
    )

    alerts = await store.get_alerts(user.id)
    failures = [a for a in alerts if a.type == "task_failure"]
    assert len(failures) == 1, f"expected one task_failure alert, got {alerts}"
    assert TASK_NAME in failures[0].title

    assert "completed successfully" not in caplog.text, (
        "a run with no answer still logged `completed successfully`"
    )


@pytest.mark.asyncio
async def test_the_finalize_carries_the_no_answer_fact_and_a_real_answer_does_not(
    store, monkeypatch
):
    """AC1/AC4 at the seam: the *finalize* says whether the model said anything.

    The turn's answer is the sentence either way (live chat is unchanged), so the
    only honest difference is structural. A fix that re-derived the outcome in
    the orchestrator by comparing the answer to the fallback sentence would pass
    the replay above and fail here.
    """
    user = await store.create_user("alice")
    silent = _orchestrator(store, _no_content_llm())
    spoken = _orchestrator(store, _GenerationAnswers("Three releases shipped."))

    empty_turn = await _collect(
        silent.chat_stream(
            ChatRequest(user_id=user.id, message="Hello", session_id="s-finalize-empty")
        )
    )
    answered_turn = await _collect(
        spoken.chat_stream(
            ChatRequest(user_id=user.id, message="Hello", session_id="s-finalize-ok")
        )
    )

    empty_final = [e for e in empty_turn if e["type"] == "finalize"][-1]
    answered_final = [e for e in answered_turn if e["type"] == "finalize"][-1]

    # The user is still given a readable answer in both cases...
    assert empty_final["answer"] == EMPTY_GENERATION_FALLBACK
    assert answered_final["answer"] == "Three releases shipped."
    # ...and only the empty one reports that nothing was said.
    assert empty_final["no_answer"] is True, (
        "the finalize did not carry the fact that the model produced no content"
    )
    assert answered_final["no_answer"] is False


@pytest.mark.asyncio
async def test_a_task_whose_answer_is_literally_the_empty_generation_sentence_succeeds(
    store, monkeypatch
):
    """AC2 — an answer with the fallback's exact wording is still an answer.

    A detector that matched the sentence text would fail this run. The model
    really produced those words, so the task really produced its report. Guard:
    green before and after — it exists to make the forbidden implementation fail,
    at whichever site it is written (the loop's finalize or the orchestrator).
    """
    user = await store.create_user("alice")
    frame_id = await _scheduled_task(store, user.id)
    orchestrator = _orchestrator(store, _GenerationAnswers(EMPTY_GENERATION_FALLBACK))

    ok, result = await execute_and_record_task(
        store, orchestrator, frame_id, TASK_NAME, TASK_PROMPT, user.id
    )

    assert ok is True, "a task whose model answered was recorded as failed"
    assert result == EMPTY_GENERATION_FALLBACK
    alerts = await store.get_alerts(user.id)
    assert [a for a in alerts if a.type == "task_failure"] == []


@pytest.mark.asyncio
async def test_live_chat_that_produced_no_answer_still_shows_the_sentence_and_no_alert(
    store, monkeypatch
):
    """AC3 — the user is present, so they get the sentence and no bell entry.

    The emptiness is recorded structurally, which is what a *recording* consumer
    needs; the live turn's behaviour is unchanged: one finalize, one meta, no
    error event, no alert.
    """
    user = await store.create_user("alice")
    orchestrator = _orchestrator(store, _no_content_llm())

    alerts: list[dict] = []
    real_create_alert = store.create_alert

    async def recording_create_alert(**kwargs):
        alerts.append(kwargs)
        return await real_create_alert(**kwargs)

    monkeypatch.setattr(store, "create_alert", recording_create_alert)

    events = await _collect(
        orchestrator.chat_stream(
            ChatRequest(user_id=user.id, message="Hello", session_id="s-live-empty")
        )
    )

    types = [e["type"] for e in events]
    finals = [e["answer"] for e in events if e["type"] == "finalize"]
    assert finals[-1] == EMPTY_GENERATION_FALLBACK
    assert "error" not in types, "a live turn surfaced a stream error event"
    assert types.count("meta") == 1, "the turn did not end with exactly one meta"
    assert types[-1] == "meta"
    assert alerts == [], f"a live empty answer raised an alert: {alerts}"


@pytest.mark.asyncio
async def test_the_no_tools_branch_that_produced_no_content_fails_the_task(
    store, stub_llm, monkeypatch
):
    """AC4 — the second construction site: the no-tools branch's empty answer.

    With `TOOLS_ENABLED=false` the loop does not finalize through
    `stream_tool_loop` at all: it collects the stream's deltas and leaves
    `final_answer` empty, and `_append_response_footers` substitutes the sentence
    for the user. That substitution is the same lie, so the emptiness has to be
    recorded before it. Red on HEAD, where the run is filed as a success.
    """
    user = await store.create_user("alice")
    frame_id = await _scheduled_task(store, user.id)
    orchestrator = _orchestrator(store, stub_llm)

    async def empty_stream(*args, **kwargs):
        yield ChatResponse(content="", model=stub_llm.chat_model, done=True)

    monkeypatch.setattr(settings, "tools_enabled", False)
    monkeypatch.setattr(stub_llm, "chat_stream", empty_stream)

    ok, result = await execute_and_record_task(
        store, orchestrator, frame_id, TASK_NAME, TASK_PROMPT, user.id
    )

    assert ok is False, "a no-tools run that produced no answer was recorded as success"
    assert result.startswith("failed:"), result
    summary = await store.get_slot(frame_id, "last_result_summary")
    assert summary is not None
    assert summary.value.startswith("failed:"), (
        f"the fallback sentence was filed as the run's output: {summary.value!r}"
    )
    alerts = await store.get_alerts(user.id)
    assert len([a for a in alerts if a.type == "task_failure"]) == 1


# 8. The finalize tool's own answer is a third way to end empty -----------------
#
# `finalize` is handled in the loop (`streaming.py:834`) with
# `raw_args.get("answer", "Done.")`, so the model's raw argument reaches the
# turn's answer unchanged. `FinalizeArgs.answer` is typed `str`, but the dict is
# never validated on this path: a present-but-null key returns `None`, and an
# empty string returns `""`. Neither is an answer.


class _FinalizesWith(StubLLMClient):
    """conftest's stub, but the generation call asks to finalize with `answer`."""

    def __init__(self, answer: object) -> None:
        super().__init__()
        self._answer = answer

    async def chat(self, messages, *args, **kwargs):
        if kwargs.get("tools"):
            return ChatResponse(
                content="",
                model=self.chat_model,
                done=True,
                tool_calls=[
                    ToolCall(name="finalize", arguments={"answer": self._answer})
                ],
            )
        return await super().chat(messages, *args, **kwargs)


@pytest.mark.asyncio
async def test_a_finalize_with_a_null_answer_fails_the_task_with_the_normal_message(
    store,
):
    """r-01 — a null `answer` is tolerated, not a `NoneType.strip()` crash.

    Out of schema (`FinalizeArgs.answer` is `str`) but reachable: `arguments` is
    the model's raw dict and `raw_args.get("answer", "Done.")` returns `None` for
    a present key with a null value. The emptiness guard used to call `.strip()`
    on it, so the turn ended as an SSE `error` and the task with a Python
    traceback as its failure message. The normal no-answer outcome is the
    fallback sentence for the user and `failed:` for the recording.
    """
    user = await store.create_user("alice")
    frame_id = await _scheduled_task(store, user.id)
    orchestrator = _orchestrator(store, _FinalizesWith(None))

    # The live turn finalizes with the sentence, not an error event.
    events = await _collect(
        orchestrator.chat_stream(
            ChatRequest(user_id=user.id, message="Hello", session_id="s-finalize-null")
        )
    )
    types = [e["type"] for e in events]
    finals = [e["answer"] for e in events if e["type"] == "finalize"]
    assert "error" not in types, "a null finalize answer surfaced a stream error event"
    assert finals[-1] == EMPTY_GENERATION_FALLBACK

    # And the run is recorded failed for the ordinary reason. `failed:` alone is
    # not enough: the runner's broad `except` turned the crash into one too, with
    # the traceback as the message. This pins *which* failure it was.
    ok, result = await execute_and_record_task(
        store, orchestrator, frame_id, TASK_NAME, TASK_PROMPT, user.id
    )
    assert ok is False, "a finalize with a null answer was recorded as a success"
    assert result.startswith("failed:"), result
    assert "generation failed" in result, result
    assert "NoneType" not in result and "AttributeError" not in result, result

    summary = await store.get_slot(frame_id, "last_result_summary")
    assert summary is not None, "the run left no summary at all"
    assert summary.value.startswith("failed:"), summary.value
    assert "NoneType" not in summary.value, (
        f"the failure was a Python traceback: {summary.value!r}"
    )

    alerts = await store.get_alerts(user.id)
    assert len([a for a in alerts if a.type == "task_failure"]) == 1


@pytest.mark.asyncio
async def test_a_finalize_with_an_empty_answer_fails_the_task(store):
    """r-03 — the finalize tool's empty-answer door is the same no-answer outcome.

    `finalize` with `answer=""` leaves the turn's answer empty. The loop's own
    `no_answer` flag is set only on the no-tool-calls branch, so this door is the
    orchestrator's emptiness check, not the seam `sf1`/`emp1` tested. The door was
    claimed covered ("caught by the same emptiness check") but nothing drove it.
    """
    user = await store.create_user("alice")
    frame_id = await _scheduled_task(store, user.id)
    orchestrator = _orchestrator(store, _FinalizesWith(""))

    ok, result = await execute_and_record_task(
        store, orchestrator, frame_id, TASK_NAME, TASK_PROMPT, user.id
    )

    assert ok is False, "a finalize with an empty answer was recorded as a success"
    assert result.startswith("failed:"), result

    summary = await store.get_slot(frame_id, "last_result_summary")
    assert summary is not None, "the run left no summary at all"
    assert summary.value.startswith("failed:"), (
        f"the empty finalize answer was filed as the run's output: {summary.value!r}"
    )
    assert EMPTY_GENERATION_FALLBACK not in summary.value

    alerts = await store.get_alerts(user.id)
    assert len([a for a in alerts if a.type == "task_failure"]) == 1
