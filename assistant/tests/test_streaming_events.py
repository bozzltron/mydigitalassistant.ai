"""Regression tests for the SSE streaming helpers.

Covers the two pieces added to fix "stream shows a status but no response":
1. ``serialize_event`` for the new ``stage`` event type.
2. ``merge_sse`` — the generator that interleaves live pipeline stage events
   with the primary SSE stream without ever cancelling a long-awaited step.
"""

import asyncio
import json

from assistant.backend.pipeline.streaming import (
    ErrorEvent,
    MetaEvent,
    StageEvent,
    merge_sse,
    serialize_event,
)


def test_serialize_stage_event():
    """Stage events serialize to the wire format the frontend parses."""
    msg = serialize_event(StageEvent("recall", "checking my memory"))
    assert msg == (
        'data: {"type": "stage", "stage": "recall", '
        '"detail": "checking my memory"}\n\n'
    )


def test_serialize_error_event():
    """Error events match the frontend's expected wire format."""
    msg = serialize_event(ErrorEvent("boom"))
    assert msg == 'data: {"type": "error", "error": "boom"}\n\n'


def test_serialize_meta_event():
    """Meta events serialize dataclass/enum payloads (the SearchInfo shape)."""
    from dataclasses import dataclass
    from enum import Enum

    class Level(Enum):
        SAFE = "safe"

    @dataclass
    class Snippet:
        title: str
        url: str

    @dataclass
    class SearchInfoLike:
        backend: str
        query: str
        level: Level
        results: list

    msg = serialize_event(
        MetaEvent(
            session_id="s-1",
            task_type="search",
            extraction_summary={"slots_applied": 1},
            search_info=SearchInfoLike(
                backend="brave",
                query="hi there",
                level=Level.SAFE,
                results=[Snippet(title="t", url="u")],
            ),
        )
    )
    parsed = json.loads(msg[len("data: "):].strip("\n"))
    assert parsed["type"] == "meta"
    assert parsed["session_id"] == "s-1"
    assert parsed["task_type"] == "search"
    assert parsed["extraction_summary"] == {"slots_applied": 1}
    # Nested dataclasses are expanded; enums become their string values.
    assert parsed["search_info"]["backend"] == "brave"
    assert parsed["search_info"]["level"] == "safe"
    assert parsed["search_info"]["results"] == [{"title": "t", "url": "u"}]


async def _collect(agen):
    return [ev async for ev in agen]


def _delta(text: str) -> str:
    return f"data: {json.dumps({'type': 'text_delta', 'delta': text})}\n\n"


async def test_merge_sse_interleaves_stage_events():
    """Stage events queued while the pipeline runs reach the client in order."""
    async def primary():
        for i in range(3):
            await asyncio.sleep(0.02)
            yield _delta(str(i))

    queue: asyncio.Queue = asyncio.Queue()
    queue.put_nowait(serialize_event(StageEvent("routing", "reading your message")))
    queue.put_nowait(serialize_event(StageEvent("recall", "checking my memory")))

    events = await _collect(merge_sse(primary(), queue, poll=0.005))

    stage = [ev for ev in events if '"type": "stage"' in ev]
    assert len(stage) == 2

    deltas = [ev for ev in events if '"type": "text_delta"' in ev]
    assert len(deltas) == 3

    # Events queued before the merge started are emitted first.
    assert events[0] == serialize_event(StageEvent("routing", "reading your message"))
    assert events[1] == serialize_event(StageEvent("recall", "checking my memory"))

    # Primary events keep their relative order.
    assert deltas == [_delta("0"), _delta("1"), _delta("2")]


async def test_merge_sse_surfaces_primary_exception():
    """Failures inside the stream become an SSE error event, not a dead stream.

    This matters for the streaming UX: before this, an exception mid-pipeline
    left the tab stuck on "checking my memory" forever.
    """
    async def broken():
        yield serialize_event(StageEvent("routing", "reading your message"))
        raise RuntimeError("boom")

    queue: asyncio.Queue = asyncio.Queue()
    events = await _collect(merge_sse(broken(), queue, poll=0.005))

    assert any(ev == 'data: {"type": "error", "error": "boom"}\n\n' for ev in events)


async def test_merge_sse_flushes_queue_after_primary_completes():
    """Stage events emitted right at completion are not dropped."""
    async def primary():
        yield f"data: {json.dumps({'type': 'finalize', 'answer': 'hi'})}\n\n"

    queue: asyncio.Queue = asyncio.Queue()
    queue.put_nowait(serialize_event(StageEvent("responding", "writing a reply")))

    events = await _collect(merge_sse(primary(), queue, poll=0.005))

    assert any('"type": "stage"' in ev for ev in events)
    assert any('"type": "finalize"' in ev for ev in events)


async def test_merge_sse_emits_stage_events_during_long_pipeline_step():
    """Stage events stream out while the primary is mid-step (not only at
    primary event boundaries). This is the behavior that makes the status bar
    feel live during a multi-second retrieval + LLM turn.
    """
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_primary():
        yield _delta("first")
        started.set()
        await release.wait()  # long awaited pipeline step
        yield _delta("second")

    queue: asyncio.Queue = asyncio.Queue()

    async def observer():
        collected = []
        async for ev in merge_sse(slow_primary(), queue, poll=0.005):
            collected.append(ev)
            if ev == _delta("first"):
                # Pipeline is now blocked on the long step; push a stage update.
                queue.put_nowait(
                    serialize_event(StageEvent("recall", "checking my memory"))
                )
            if '"type": "stage"' in ev:
                break
        release.set()
        return collected

    collected = await observer()
    assert _delta("first") in collected
    assert any('"type": "stage"' in ev for ev in collected)
    # The stage event arrives BEFORE the primary finishes its long step.
    assert collected[-1].startswith("data: ")


async def test_stream_tool_loop_max_turns_wraps_up_not_metadata(tmp_path):
    """Regression: exhausting tool turns streams a model wrap-up, not metadata.

    Before the fix, stream_tool_loop finalized with a hardcoded dump of the
    tool-call records ("I've considered this for N turns. Here's what I found:
    [...]"), leaking internal tool state into the user's chat.
    """
    from assistant.backend.pipeline.llm_client import ChatResponse, ToolCall
    from assistant.backend.pipeline.streaming import stream_tool_loop
    from assistant.backend.pipeline.tool_executor import init_store
    init_store(str(tmp_path / "stream.db"))

    class StubLLM:
        def __init__(self, responses):
            self.responses = list(responses)
            self.tools_model = "test-model"
            self.calls = []

        async def chat(self, messages, **kwargs):
            self.calls.append({"messages": messages, **kwargs})
            return self.responses.pop(0)

        def context_window(self, model=None):
            return 16384

    failing = ChatResponse(
        content="",
        model="m",
        done=True,
        tool_calls=[ToolCall(name="read_file", arguments={"path": "nope.csv"})],
    )
    wrap_up = ChatResponse(
        content="I couldn't find a file named nope.csv in your sandbox.",
        model="m",
        done=True,
    )
    llm = StubLLM([failing] * 3 + [wrap_up])

    events = []
    async for ev in stream_tool_loop(
        llm,
        messages=[{"role": "user", "content": "read nope.csv from my files"}],
        tools=[],
        model="test-model",
        user_id="1",
        session_id="s-1",
        max_turns=3,
    ):
        events.append(ev)

    stream = "".join(events)
    assert "Here's what I found" not in stream
    assert "read_file" not in "".join(
        ev for ev in events if '"type": "finalize"' in ev
    )

    # The finalize event carries the model's plain-language wrap-up.
    finalize = [ev for ev in events if '"type": "finalize"' in ev]
    assert finalize
    assert "I couldn't find a file named nope.csv" in finalize[-1]

    # The wrap-up LLM call was text-only (tool_choice="none").
    assert llm.calls[-1]["tool_choice"] == "none"

    # Failed reads surfaced on the wire as tool_result errors.
    assert any(
        '"type": "tool_result"' in ev and "File not found" in ev for ev in events
    )


async def test_stream_tool_loop_retries_empty_generation_with_compacted_history(
    tmp_path,
):
    """Regression: an empty generation is retried without history, not surfaced
    as the old comprehension fallback.

    Root cause (live incident 2026-10-01): the tool-loop prompt (system prompt +
    16 tool schemas + 6 turns of history) reached 8169 tokens against an
    8192-token context. The model emitted 23 tokens, was cut off, and returned
    neither content nor a tool call — so the loop finalized with
    "I'm not sure how to respond." Dropping history gives the model room.
    """
    from assistant.backend.pipeline.llm_client import ChatResponse
    from assistant.backend.pipeline.streaming import stream_tool_loop
    from assistant.backend.pipeline.tool_executor import init_store

    init_store(str(tmp_path / "stream.db"))

    class StubLLM:
        def __init__(self, responses):
            self.responses = list(responses)
            self.tools_model = "test-model"
            self.calls = []

        async def chat(self, messages, **kwargs):
            self.calls.append({"messages": messages, **kwargs})
            return self.responses.pop(0)

        def context_window(self, model=None):
            return 16384

    empty = ChatResponse(content="", model="m", done=True, done_reason="length")
    answer = ChatResponse(content="Here is the strategy.", model="m", done=True)
    llm = StubLLM([empty, answer])

    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "earlier question"},
        {"role": "assistant", "content": "earlier answer"},
        {"role": "user", "content": "the complex question"},
    ]
    events = [
        ev
        async for ev in stream_tool_loop(
            llm,
            messages=messages,
            tools=[],
            model="test-model",
            user_id="1",
            session_id="s-1",
        )
    ]

    # Exactly one retry, and the user sees the model's answer.
    assert len(llm.calls) == 2
    stream = "".join(events)
    assert "I'm not sure how to respond" not in stream
    finalize = [ev for ev in events if '"type": "finalize"' in ev]
    assert finalize and "Here is the strategy." in finalize[-1]

    # The retry dropped history: system + most recent user turn only.
    retry = llm.calls[1]["messages"]
    assert [m.role for m in retry] == ["system", "user"]
    assert retry[-1].content == "the complex question"


async def test_stream_tool_loop_empty_generation_fallback_is_honest(tmp_path):
    """If even the compacted retry is empty, say the context filled — don't
    blame the question."""
    from assistant.backend.pipeline.llm_client import ChatResponse
    from assistant.backend.pipeline.streaming import stream_tool_loop
    from assistant.backend.pipeline.tool_executor import init_store

    init_store(str(tmp_path / "stream.db"))

    class StubLLM:
        def __init__(self):
            self.tools_model = "test-model"
            self.calls = 0

        async def chat(self, messages, **kwargs):
            self.calls += 1
            return ChatResponse(
                content="", model="m", done=True, done_reason="length"
            )

        def context_window(self, model=None):
            return 16384

    llm = StubLLM()
    events = [
        ev
        async for ev in stream_tool_loop(
            llm,
            messages=[{"role": "user", "content": "q"}],
            tools=[],
            model="test-model",
            user_id="1",
            session_id="s-1",
        )
    ]

    assert llm.calls == 2  # one original + one compacted retry
    stream = "".join(events)
    assert "I'm not sure how to respond" not in stream
    assert "my working context filled up" in stream


async def test_stream_tool_loop_reports_the_peak_prompt(tmp_path):
    """The finalize carries the loop's *maximum* prompt, not turn 1's.

    The tool result appended after the first call is what makes the loop the
    largest prompt in the system, so the peak is the number the context meter
    needs -- turn 1 is only the system prompt + history.
    """
    import json

    from assistant.backend.pipeline.llm_client import ChatResponse, ToolCall
    from assistant.backend.pipeline.streaming import stream_tool_loop
    from assistant.backend.pipeline.tool_executor import init_store

    init_store(str(tmp_path / "stream.db"))

    class StubLLM:
        def __init__(self, responses):
            self.responses = list(responses)
            self.tools_model = "test-model"

        async def chat(self, messages, **kwargs):
            return self.responses.pop(0)

        def context_window(self, model=None):
            return 16384

    tool_turn = ChatResponse(
        content="",
        model="m",
        done=True,
        prompt_eval_count=9000,
        tool_calls=[ToolCall(name="read_file", arguments={"path": "nope.csv"})],
    )
    answer_turn = ChatResponse(
        content="done", model="m", done=True, prompt_eval_count=15000
    )
    llm = StubLLM([tool_turn, answer_turn])

    events = [
        ev
        async for ev in stream_tool_loop(
            llm,
            messages=[{"role": "user", "content": "q"}],
            tools=[],
            model="test-model",
            user_id="1",
            session_id="s-1",
        )
    ]
    finalize = [
        json.loads(ev.replace("data: ", "").strip())
        for ev in events
        if '"type": "finalize"' in ev
    ][-1]
    assert finalize["prompt_tokens"] == 15000  # the peak, not turn 1's 9000
    assert finalize["context_window"] == 16384


def test_serialize_finalize_includes_tool_result_chars():
    """The finalize wire format carries the tool-result total and drop count.

    The orchestrator reads them back to log where the window went; if they are not
    serialized the values silently read as 0 and the composition is a lie.
    """
    from assistant.backend.pipeline.streaming import FinalizeEvent

    msg = serialize_event(
        FinalizeEvent(
            "hi",
            prompt_tokens=10,
            context_window=100,
            tool_result_chars=42,
            tool_results_dropped=3,
        )
    )
    parsed = json.loads(msg[len("data: "):].strip("\n"))
    assert parsed["tool_result_chars"] == 42
    assert parsed["tool_results_dropped"] == 3


async def test_stream_tool_loop_reports_tool_result_chars(tmp_path):
    """The finalize carries the total characters of tool results appended.

    That is the part of the prompt that grows round over round. `context_usage`
    reports it with the fixed cost so the window's composition is visible per
    turn rather than inferred.
    """
    from assistant.backend.pipeline.llm_client import ChatResponse, ToolCall
    from assistant.backend.pipeline.streaming import stream_tool_loop
    from assistant.backend.pipeline.tool_executor import init_store

    init_store(str(tmp_path / "stream.db"))

    class StubLLM:
        def __init__(self, responses):
            self.responses = list(responses)
            self.tools_model = "test-model"

        async def chat(self, messages, **kwargs):
            return self.responses.pop(0)

        def context_window(self, model=None):
            return 16384

    tool_turn = ChatResponse(
        content="",
        model="m",
        done=True,
        tool_calls=[ToolCall(name="read_file", arguments={"path": "nope.csv"})],
    )
    answer_turn = ChatResponse(content="done", model="m", done=True)
    llm = StubLLM([tool_turn, answer_turn])

    events = [
        ev
        async for ev in stream_tool_loop(
            llm,
            messages=[{"role": "user", "content": "q"}],
            tools=[],
            model="test-model",
            user_id="1",
            session_id="s-1",
        )
    ]
    finalize = [
        json.loads(ev.replace("data: ", "").strip())
        for ev in events
        if '"type": "finalize"' in ev
    ][-1]
    # The failed read appended an "ERROR: ..." tool result, so the total is > 0.
    assert finalize["tool_result_chars"] > 0


async def test_stream_tool_loop_does_not_resend_thinking(tmp_path):
    """§6.3 hygiene: the model's thinking is returned for audit, never re-sent.

    Ported from the removed `run_tool_loop` tests, since that loop was dead in
    production and this one is the only path.
    """
    from assistant.backend.pipeline.llm_client import ChatResponse, ToolCall
    from assistant.backend.pipeline.streaming import stream_tool_loop
    from assistant.backend.pipeline.tool_executor import init_store

    init_store(str(tmp_path / "stream.db"))

    class StubLLM:
        def __init__(self, responses):
            self.responses = list(responses)
            self.tools_model = "test-model"
            self.calls = []

        async def chat(self, messages, **kwargs):
            self.calls.append(messages)
            return self.responses.pop(0)

        def context_window(self, model=None):
            return 16384

    first = ChatResponse(
        content="",
        model="m",
        done=True,
        thinking="pondering",
        tool_calls=[ToolCall(name="read_file", arguments={"path": "nope.csv"})],
    )
    answer = ChatResponse(content="done", model="m", done=True)
    llm = StubLLM([first, answer])

    async for _ in stream_tool_loop(
        llm,
        messages=[{"role": "user", "content": "q"}],
        tools=[],
        model="test-model",
        user_id="1",
        session_id="s-1",
    ):
        pass

    assert len(llm.calls) == 2
    assert all(
        "pondering" not in (getattr(m, "content", "") or "") for m in llm.calls[1]
    )


async def test_stream_tool_loop_collapses_older_results_when_the_budget_is_spent(
    tmp_path,
):
    """The aggregate cap: rounds share one allowance, older results give way.

    T2 caps each result; without an aggregate cap the loop could still add one per
    round and overflow. Here the allowance fits one large read, so the second read
    collapses the first to a marker -- which the model sees, and which keeps the
    tool_call/tool_result pairing intact.
    """
    from assistant.backend.pipeline import filesystem
    from assistant.backend.pipeline.context_budget import (
        TurnBudget,
        reset_turn_budget,
        set_turn_budget,
    )
    from assistant.backend.pipeline.llm_client import ChatResponse, ToolCall
    from assistant.backend.pipeline.streaming import DROPPED_TOOL_RESULT, stream_tool_loop
    from assistant.backend.pipeline.tool_executor import init_store

    init_store(str(tmp_path / "stream.db"))
    filesystem.SANDBOX_ROOT.mkdir(parents=True, exist_ok=True)
    big = filesystem.SANDBOX_ROOT / "collapse_big.txt"
    big.write_text("\n".join(f"line {i}" for i in range(5000)), encoding="utf-8")
    # A second, *different* file: two reads of the same path are now answered from
    # the repeat ledger and never reach the aggregate cap, which is what this test
    # is about.
    big2 = filesystem.SANDBOX_ROOT / "collapse_big2.txt"
    big2.write_text("\n".join(f"other {i}" for i in range(5000)), encoding="utf-8")

    class StubLLM:
        def __init__(self, responses):
            self.responses = list(responses)
            self.tools_model = "test-model"
            self.calls = []

        async def chat(self, messages, **kwargs):
            self.calls.append(messages)
            return self.responses.pop(0)

        def context_window(self, model=None):
            return 16384

    def read_call(path):
        return ChatResponse(
            content="",
            model="m",
            done=True,
            tool_calls=[ToolCall(name="read_file", arguments={"path": path})],
        )

    llm = StubLLM([
        read_call("collapse_big.txt"),
        read_call("collapse_big2.txt"),
        ChatResponse(content="done", model="m", done=True),
    ])

    # Allowance 4,000 chars: one large read fits, two do not.
    token = set_turn_budget(
        TurnBudget(window_tokens=16384, fixed_cost_chars=0, reserved_output_tokens=16384 - 1000)
    )
    try:
        events = [
            ev
            async for ev in stream_tool_loop(
                llm,
                messages=[{"role": "user", "content": "q"}],
                tools=[],
                model="test-model",
                user_id="1",
                session_id="s-1",
            )
        ]
    finally:
        reset_turn_budget(token)
        big.unlink(missing_ok=True)
        big2.unlink(missing_ok=True)

    finalize = [
        json.loads(ev.replace("data: ", "").strip())
        for ev in events
        if '"type": "finalize"' in ev
    ][-1]
    assert finalize["tool_results_dropped"] >= 1
    # The dropped marker reached the model's messages.
    assert any(
        DROPPED_TOOL_RESULT in (m.content or "")
        for call in llm.calls
        for m in call
    )


async def test_stream_tool_loop_nudges_once_when_an_action_was_expected(tmp_path):
    """Regression: a request that needs an action but gets a reply is nudged once.

    The live failure: asked for a file, the agent announced it ("I'll now create
    X") and called no tool, so nothing was written. A decision model judges that
    the request needs an action (``decision.needs_action``); the loop hands the
    model its own message back once and lets it act.
    """
    from assistant.backend.pipeline.llm_client import ChatResponse, ToolCall
    from assistant.backend.pipeline.streaming import stream_tool_loop
    from assistant.backend.pipeline.tool_executor import init_store

    init_store(str(tmp_path / "nudge.db"))

    class StubLLM:
        def __init__(self):
            self.tools_model = "test-model"
            self.calls: list = []

        async def chat(self, messages, **kwargs):
            self.calls.append(messages)
            if len(self.calls) == 1:
                # The failure: it announces the action and calls no tool.
                return ChatResponse(
                    content="I'll now create the file. Let me write that now!",
                    model="m",
                    done=True,
                )
            if len(self.calls) == 2:
                # After the nudge it acts (read_file on a missing path is enough
                # to prove a tool call happened; no file is written by the test).
                return ChatResponse(
                    content="",
                    model="m",
                    done=True,
                    tool_calls=[
                        ToolCall(name="read_file", arguments={"path": "nudge_probe.txt"})
                    ],
                )
            return ChatResponse(content="Done.", model="m", done=True)

        def context_window(self, model=None):
            return 16384

    llm = StubLLM()
    events = [
        ev
        async for ev in stream_tool_loop(
            llm,
            [{"role": "user", "content": "Create a file called nudge_probe.txt"}],
            tools=[],
            expects_action=True,
        )
    ]

    assert len(llm.calls) >= 2, "the loop must give the model a second chance"
    second = llm.calls[1]
    assert any(
        getattr(m, "role", "") == "user"
        and "without performing the action" in (getattr(m, "content", "") or "")
        for m in second
    ), "the nudge note must reach the model"
    assert any(
        getattr(m, "role", "") == "assistant"
        and "Let me write that now" in (getattr(m, "content", "") or "")
        for m in second
    ), "the model's own message is handed back"
    assert events and '"type": "finalize"' in events[-1]


def test_action_performed_is_structural():
    """A read is not a write, and a lookup is not a change.

    Both halves are regressions from `experiments/ship_tracker_efficacy/`:

    - run 1: 7 of 10 trials read the CSV, never called a write tool, and the previous
      *model* judgement (`outstanding`, `nimble`) called that done;
    - run 2: the first structural rule checked both tool sets and accepted either, so a
      `fetch_url` satisfied a request to write a file — 5 of 10 still failed, one tool
      further along.
    """
    from assistant.backend.pipeline.decision import action_performed

    # A *change* is not performed by a read, nor by a lookup.
    assert not action_performed([], "change")
    assert not action_performed(["read_file", "search_file", "glob"], "change")
    assert not action_performed(["read_file", "fetch_url"], "change")  # run 2's bug
    assert action_performed(["write_file"], "change")
    assert action_performed(["read_file", "edit_file"], "change")
    assert action_performed(["upsert_slot"], "change")

    # A *lookup* is performed by a lookup, and not by a write.
    assert not action_performed(["read_file"], "lookup")
    assert action_performed(["web_search"], "lookup")
    assert action_performed(["fetch_url"], "lookup")
    assert not action_performed(["search_episodes"], "lookup")  # internal, not external

    # An unknown kind performs nothing: the check must not fire on a guess.
    assert not action_performed(["write_file"], None)


async def test_stream_tool_loop_still_judges_completion_after_the_first_nudge(
    tmp_path, monkeypatch
):
    """Narrate -> nudged -> *read* -> finalise: the completion judgement must still run.

    Regression, from a live scheduled task. `_maybe_nudge` checked `already_nudged`
    before both of its branches, so the single allowance was spent by the
    missing-call nudge and the completion judgement — the one written for "a tool ran
    but it did not do the job" — could never fire afterwards. That is the *common*
    sequence, so the branch was dead for the case it exists for: the model narrated, was
    nudged, called `search_file` (a read), and finalised claiming the write.
    """
    from assistant.backend.pipeline.llm_client import ChatResponse, ToolCall
    from assistant.backend.pipeline.streaming import stream_tool_loop
    from assistant.backend.pipeline.tool_executor import init_store

    init_store(str(tmp_path / "sequence.db"))

    class StubLLM:
        def __init__(self):
            self.tools_model = "test-model"
            self.calls: list = []

        async def chat(self, messages, **kwargs):
            self.calls.append(messages)
            if len(self.calls) == 1:
                # Narrates, calls nothing — the missing-call nudge.
                return ChatResponse(content="I'll update the file now.", model="m", done=True)
            if len(self.calls) == 2:
                # Answers the nudge with a *read*, not the write.
                return ChatResponse(
                    content="",
                    model="m",
                    done=True,
                    tool_calls=[
                        ToolCall(name="search_file", arguments={"path": "x.csv", "query": "y"})
                    ],
                )
            if len(self.calls) == 3:
                return ChatResponse(content="Done — the file is updated.", model="m", done=True)
            return ChatResponse(content="Wrote it for real now.", model="m", done=True)

        def context_window(self, model=None):
            return 16384

    llm = StubLLM()
    events = [
        ev
        async for ev in stream_tool_loop(
            llm,
            [{"role": "user", "content": "Update x.csv and write x.geojson"}],
            [],
            expects_action=True,
        )
    ]

    # The *completion* note specifically. Both notes mention "perform", so asserting on
    # that word alone passes even when this branch never runs — which is how the first
    # version of this test passed against the bug it was written for.
    contents = [getattr(m, "content", "") or "" for call in llm.calls for m in call]
    assert any("the tools you called did not perform it" in c for c in contents), contents
    assert events and '"type": "finalize"' in events[-1]


async def test_a_failed_write_is_not_a_performed_action(tmp_path):
    """A refused write must trigger the retry nudge.

    `write_file` on an existing path fails with "File exists: … (use overwrite=True)".
    The completion check judged by tool *name*, so the attempt counted as the action,
    the model was never nudged, and the turn ended reporting a failure it could have
    fixed by calling the same tool with `overwrite=True` — the overwrite failure that
    has been costing scheduled runs.
    """
    from assistant.backend.pipeline.filesystem import get_sandbox_root
    from assistant.backend.pipeline.llm_client import ChatResponse, ToolCall
    from assistant.backend.pipeline.streaming import stream_tool_loop
    from assistant.backend.pipeline.tool_executor import init_store

    init_store(str(tmp_path / "failed-write.db"))
    target = get_sandbox_root() / "failed_write_probe.txt"
    target.write_text("old", encoding="utf-8")

    class StubLLM:
        tools_model = "test-model"

        def __init__(self):
            self.calls = []

        async def chat(self, messages, **kwargs):
            self.calls.append(list(messages))
            if len(self.calls) == 1:
                # No overwrite flag: this call fails.
                return ChatResponse(
                    content="",
                    model="m",
                    done=True,
                    tool_calls=[
                        ToolCall(
                            name="write_file",
                            arguments={"path": "failed_write_probe.txt", "content": "new"},
                        )
                    ],
                )
            return ChatResponse(content="Could not update it.", model="m", done=True)

        def context_window(self, model=None):
            return 16384

    llm = StubLLM()
    events = [
        ev
        async for ev in stream_tool_loop(
            llm,
            [{"role": "user", "content": "Overwrite failed_write_probe.txt with new content"}],
            [],
            expects_action=True,
            action_kind="change",
        )
    ]

    contents = [getattr(m, "content", "") or "" for call in llm.calls for m in call]
    assert any("the tools you called did not perform it" in c for c in contents), contents
    assert events and '"type": "finalize"' in events[-1]
    target.unlink(missing_ok=True)


async def test_stream_tool_loop_nudges_when_the_calls_did_not_do_it(tmp_path, monkeypatch):
    """The live failure: a tool ran (read_file) but the action was not performed.

    "No tool call" is too coarse — the model called a *different* tool and then
    claimed the write. The completion check is structural now: a request that needs a
    change is not performed by a read, and no model has to be asked.
    """
    from assistant.backend.pipeline.llm_client import ChatResponse, ToolCall
    from assistant.backend.pipeline.streaming import stream_tool_loop
    from assistant.backend.pipeline.tool_executor import init_store

    init_store(str(tmp_path / "completion.db"))

    class StubLLM:
        def __init__(self):
            self.tools_model = "test-model"
            self.calls: list = []

        async def chat(self, messages, **kwargs):
            self.calls.append(messages)
            if len(self.calls) == 1:
                return ChatResponse(
                    content="",
                    model="m",
                    done=True,
                    tool_calls=[ToolCall(name="read_file", arguments={"path": "x.md"})],
                )
            if len(self.calls) == 2:
                return ChatResponse(content="I've created the file.", model="m", done=True)
            return ChatResponse(content="Created it for real now.", model="m", done=True)

        def context_window(self, model=None):
            return 16384

    llm = StubLLM()
    _ = [
        ev
        async for ev in stream_tool_loop(
            llm,
            [{"role": "user", "content": "Create x.md"}],
            tools=[],
            expects_action=True,
        )
    ]

    assert len(llm.calls) >= 3, "the completion nudge must give the model another round"
    third = llm.calls[2]
    assert any(
        getattr(m, "role", "") == "user"
        and "did not perform it" in (getattr(m, "content", "") or "")
        for m in third
    ), "the completion nudge must reach the model"


async def test_stream_tool_loop_does_not_nudge_when_no_action_expected(tmp_path):
    """The other half: a plain reply finalizes with no extra round."""
    from assistant.backend.pipeline.llm_client import ChatResponse
    from assistant.backend.pipeline.streaming import stream_tool_loop
    from assistant.backend.pipeline.tool_executor import init_store

    init_store(str(tmp_path / "nonudge.db"))

    class StubLLM:
        def __init__(self):
            self.tools_model = "test-model"
            self.calls: list = []

        async def chat(self, messages, **kwargs):
            self.calls.append(messages)
            return ChatResponse(content="Paris.", model="m", done=True)

        def context_window(self, model=None):
            return 16384

    llm = StubLLM()
    _ = [
        ev
        async for ev in stream_tool_loop(
            llm,
            [{"role": "user", "content": "What's the capital of France?"}],
            tools=[],
            expects_action=False,
        )
    ]
    assert len(llm.calls) == 1, "no nudge round when no action is expected"


async def test_stream_tool_loop_keeps_the_search_it_ran_and_announces_it(monkeypatch):
    """A `web_search` the model runs itself must reach the UI.

    Regression: `execute_web_search` returns `{"results": …, "search_info": …}`, and
    the loop kept only the formatted text and dropped the record. So a search the
    model performed left no trace anywhere — no "Searched via Brave", no sources, no
    trace entry — even though Brave had answered. The user had no way to tell a real
    search from the model's prose claiming one, which is exactly how it was reported:
    "it's saying that it's searching the web, but it's not."
    """
    from assistant.backend.pipeline import tool_executor
    from assistant.backend.pipeline.llm_client import ChatResponse, ToolCall
    from assistant.backend.pipeline.search import SearchInfo, SearchResult
    from assistant.backend.pipeline.streaming import stream_tool_loop
    from assistant.backend.pipeline.tool_executor import ToolResult

    info = SearchInfo(
        backend="brave",
        query="latest news",
        results=[
            SearchResult(
                title="A real headline", url="https://example.com/a", snippet="s", engine="brave"
            )
        ],
    )

    async def fake_execute(name, args, **kwargs):
        return ToolResult(success=True, data={"results": info.results, "search_info": info})

    monkeypatch.setattr(tool_executor, "execute_tool", fake_execute)

    class StubLLM:
        def __init__(self, responses):
            self.responses = list(responses)
            self.tools_model = "test-model"

        async def chat(self, messages, **kwargs):
            return self.responses.pop(0)

        def context_window(self, model=None):
            return 16384

    llm = StubLLM(
        [
            ChatResponse(
                content="",
                model="m",
                done=True,
                tool_calls=[ToolCall(name="web_search", arguments={"query": "latest news"})],
            ),
            ChatResponse(content="Here you go.", model="m", done=True),
        ]
    )

    sink: dict = {}
    events = [
        ev
        async for ev in stream_tool_loop(
            llm,
            messages=[{"role": "user", "content": "search the news"}],
            tools=[],
            model="test-model",
            user_id="1",
            session_id="s-1",
            search_sink=sink,
        )
    ]

    parsed = [json.loads(ev.replace("data: ", "").strip()) for ev in events]
    assert any(
        e.get("type") == "stage" and e.get("stage") == "searching" for e in parsed
    ), "the search was never announced while it ran"
    assert sink.get("info") is info, "the search record the model used was discarded"



async def test_stream_tool_loop_grants_a_round_at_the_ceiling_to_act(tmp_path):
    """Exhaustion must spend the completion nudge, not fall through to a report.

    Regression, from `experiments/ship_tracker_efficacy/` run 4. The ceiling
    (`while turn < max_turns`) fell through to a wrap-up call that sets
    `tool_choice="none"` and asks the model to "state what blocked you". A turn that ran
    out of rounds could therefore produce a *reason* but never act — and the completion
    nudge, which produced all seven of the run's writes, was never reached, because only
    a round ending with no tool call passes through `_maybe_nudge`.

    Measured: 2 of 3 failures exited that way. The model complied with the question it was
    asked — "I don't have direct write access", "should I write ... now?" — while its tool
    list contained `write_file`.
    """
    from assistant.backend.pipeline.llm_client import ChatResponse, ToolCall
    from assistant.backend.pipeline.streaming import stream_tool_loop
    from assistant.backend.pipeline.tool_executor import init_store

    init_store(str(tmp_path / "ceiling.db"))

    class StubLLM:
        """Reads until the ceiling, exactly like run 4's trials 1 and 7."""

        def __init__(self):
            self.tools_model = "test-model"
            self.calls: list = []

        async def chat(self, messages, **kwargs):
            self.calls.append(list(messages))
            if len(self.calls) <= 2:
                return ChatResponse(
                    content="",
                    model="m",
                    done=True,
                    tool_calls=[
                        ToolCall(
                            name="search_file",
                            arguments={"path": "vessels.csv", "query": "ever"},
                        )
                    ],
                )
            return ChatResponse(content="Done - the file is updated.", model="m", done=True)

        def context_window(self, model=None):
            return 16384

    llm = StubLLM()
    events = [
        ev
        async for ev in stream_tool_loop(
            llm,
            [{"role": "user", "content": "Update ship-tracker.geojson"}],
            [],
            max_turns=2,
            expects_action=True,
            action_kind="change",
        )
    ]

    contents = [m.content or "" for call in llm.calls for m in call]
    assert any("the tools you called did not perform it" in c for c in contents), contents
    # The report-only exit must not run while the action is still unperformed: its prompt
    # asks the model what blocked it, which is how run 4 got a fabricated blocker.
    assert not any("You have used all your available tool-call turns" in c for c in contents), (
        contents
    )
    assert events and '"type": "finalize"' in events[-1]


async def test_stream_tool_loop_grants_a_round_when_the_nudge_lands_on_the_last_turn(
    tmp_path,
):
    """The completion nudge firing on the final round must still get a round to be acted on.

    The other half of the same fix. `_maybe_nudge` hands the model its own message back and
    the loop `continue`s — but if that happens on the last round, the ceiling check
    immediately breaks out to the wrap-up, and the nudge is spent for nothing.
    """
    from assistant.backend.pipeline.llm_client import ChatResponse, ToolCall
    from assistant.backend.pipeline.streaming import stream_tool_loop
    from assistant.backend.pipeline.tool_executor import init_store

    init_store(str(tmp_path / "lastround.db"))

    class StubLLM:
        def __init__(self):
            self.tools_model = "test-model"
            self.calls: list = []

        async def chat(self, messages, **kwargs):
            self.calls.append(list(messages))
            if len(self.calls) == 1:
                return ChatResponse(
                    content="",
                    model="m",
                    done=True,
                    tool_calls=[
                        ToolCall(
                            name="search_file",
                            arguments={"path": "vessels.csv", "query": "ever"},
                        )
                    ],
                )
            if len(self.calls) == 2:
                # Answers the *first* round's read with a claim and no tool call — on the
                # last round, so the completion nudge fires here.
                return ChatResponse(
                    content="The file is updated.", model="m", done=True
                )
            return ChatResponse(content="Done for real now.", model="m", done=True)

        def context_window(self, model=None):
            return 16384

    llm = StubLLM()
    events = [
        ev
        async for ev in stream_tool_loop(
            llm,
            [{"role": "user", "content": "Update ship-tracker.geojson"}],
            [],
            max_turns=2,
            expects_action=True,
            action_kind="change",
        )
    ]

    contents = [m.content or "" for call in llm.calls for m in call]
    assert any("the tools you called did not perform it" in c for c in contents), contents
    assert not any("You have used all your available tool-call turns" in c for c in contents), (
        contents
    )
    # The nudge was answered by the model, not replaced by a wrap-up.
    assert "Done for real now." in events[-1]


class TestToolLoopExitReasonIsLogged:
    """The loop states why it stopped, at INFO, one greppable line per turn.

    Run 4 had to establish that two trials exited through the ceiling *by construction* —
    from the absence of a nudge in the record — because the wrap-up's answer is
    indistinguishable from an ordinary one at the file level. It is the same reasoning the
    turn-timing lines exist for, and it is pinned the same way.
    """

    def _stub(self, *, always_tools: bool):
        from assistant.backend.pipeline.llm_client import ChatResponse, ToolCall

        class StubLLM:
            def __init__(self):
                self.tools_model = "test-model"
                self.calls = 0

            async def chat(self, messages, **kwargs):
                self.calls += 1
                if always_tools:
                    return ChatResponse(
                        content="",
                        model="m",
                        done=True,
                        tool_calls=[
                            ToolCall(
                                name="search_file",
                                arguments={"path": "vessels.csv", "query": "ever"},
                            )
                        ],
                    )
                return ChatResponse(content="An answer.", model="m", done=True)

            def context_window(self, model=None):
                return 16384

        return StubLLM()

    async def test_logs_answered(self, tmp_path, caplog):
        import logging

        from assistant.backend.pipeline.streaming import stream_tool_loop
        from assistant.backend.pipeline.tool_executor import init_store

        init_store(str(tmp_path / "answered.db"))
        caplog.set_level(logging.INFO, logger="assistant.backend.pipeline.streaming")

        async for _ in stream_tool_loop(
            self._stub(always_tools=False),
            [{"role": "user", "content": "hello"}],
            [],
            max_turns=3,
        ):
            pass

        assert "tool_loop_exit: reason=answered" in caplog.text, caplog.text

    async def test_logs_exhausted(self, tmp_path, caplog):
        import logging

        from assistant.backend.pipeline.streaming import stream_tool_loop
        from assistant.backend.pipeline.tool_executor import init_store

        init_store(str(tmp_path / "exhausted.db"))
        caplog.set_level(logging.INFO, logger="assistant.backend.pipeline.streaming")

        # `expects_action` is False, so there is nothing to act on and the ceiling is a
        # genuine stop — no grant, and the wrap-up path is taken.
        async for _ in stream_tool_loop(
            self._stub(always_tools=True),
            [{"role": "user", "content": "hello"}],
            [],
            max_turns=1,
        ):
            pass

        assert "tool_loop_exit: reason=exhausted" in caplog.text, caplog.text


async def test_stream_tool_loop_terminates_when_the_model_never_stops_acting(tmp_path):
    """`while True` is bounded, and the bound is `grace_rounds_left`.

    The ceiling stopped being the loop condition when it became a grant, so the
    termination argument is now a variable: the budget starts positive, only decreases,
    and every grant spends it. This pins that, because a runaway here would not fail a
    single turn — a scheduled task's firing path has no cap, so the damage would be
    unbounded work rather than a bad answer.
    """
    from assistant.backend.pipeline.llm_client import ChatResponse, ToolCall
    from assistant.backend.pipeline.streaming import stream_tool_loop
    from assistant.backend.pipeline.tool_executor import init_store

    init_store(str(tmp_path / "runaway.db"))

    class StubLLM:
        """Reads forever and never performs the change."""

        def __init__(self):
            self.tools_model = "test-model"
            self.calls = 0

        async def chat(self, messages, **kwargs):
            self.calls += 1
            return ChatResponse(
                content="",
                model="m",
                done=True,
                tool_calls=[
                    ToolCall(
                        name="search_file",
                        arguments={"path": "vessels.csv", "query": "ever"},
                    )
                ],
            )

        def context_window(self, model=None):
            return 16384

    llm = StubLLM()
    events = [
        ev
        async for ev in stream_tool_loop(
            llm,
            [{"role": "user", "content": "Update ship-tracker.geojson"}],
            [],
            max_turns=1,
            expects_action=True,
            action_kind="change",
        )
    ]

    # One round, one granted grace round, then the wrap-up call. Bounded, not a spin.
    assert llm.calls == 4, f"loop did not terminate as bounded: {llm.calls} LLM calls"
    assert events and '"type": "finalize"' in events[-1]
