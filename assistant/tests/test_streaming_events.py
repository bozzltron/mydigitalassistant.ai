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
