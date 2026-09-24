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