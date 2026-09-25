"""SSE streaming helpers for the chat endpoint.

Provides event serialization and the streaming response generator.
"""

import asyncio
import json
from collections.abc import AsyncGenerator
from dataclasses import asdict, is_dataclass
from enum import Enum


class ToolLoopEvent:
    """Event types for the tool loop streaming."""
    pass


class TextDeltaEvent(ToolLoopEvent):
    """Text content delta from the LLM."""

    def __init__(self, delta: str):
        self.type = "text_delta"
        self.delta = delta


class ToolCallEvent(ToolLoopEvent):
    """Tool call requested by the model."""

    def __init__(self, tool_calls: list[dict]):
        self.type = "tool_call"
        self.tool_calls = tool_calls


class ToolResultEvent(ToolLoopEvent):
    """Tool execution result."""

    def __init__(self, tool_name: str, success: bool, data: dict, error: str = ""):
        self.type = "tool_result"
        self.tool_name = tool_name
        self.success = success
        self.data = data
        self.error = error


class FinalizeEvent(ToolLoopEvent):
    """Tool loop finalized with answer."""

    def __init__(self, answer: str, reasoning_trace: str | None = None):
        self.type = "finalize"
        self.answer = answer
        self.reasoning_trace = reasoning_trace


class ErrorEvent(ToolLoopEvent):
    """Error during tool loop."""

    def __init__(self, error: str):
        self.type = "error"
        self.error = error


class StageEvent(ToolLoopEvent):
    """Live pipeline stage update (mirrors /chat/status for the SSE stream)."""

    def __init__(self, stage: str, detail: str):
        self.type = "stage"
        self.stage = stage
        self.detail = detail


class MetaEvent(ToolLoopEvent):
    """Final stream metadata: session id, task type, extraction/search summaries, search info.

    Carries the same transparency fields the non-streaming ChatResponse exposes
    (search backend + query, "what I learned", task type) onto the SSE stream so
    the UI can render them without a separate request.
    """

    def __init__(
        self,
        session_id: str | None,
        task_type: str,
        extraction_summary: dict | None = None,
        search_extraction_summary: dict | None = None,
        search_info: object | None = None,
    ):
        self.type = "meta"
        self.session_id = session_id
        self.task_type = task_type
        self.extraction_summary = extraction_summary
        self.search_extraction_summary = search_extraction_summary
        self.search_info = search_info


def _event_json_default(obj: object) -> object:
    """JSON fallback for SSE payloads: recurse dataclasses, unwrap enums."""
    if isinstance(obj, Enum):
        return obj.value
    if is_dataclass(obj) and not isinstance(obj, type):
        return asdict(obj)
    return str(obj)


def serialize_event(event: ToolLoopEvent) -> str:
    """Serialize a tool loop event as an SSE message."""
    if isinstance(event, TextDeltaEvent):
        data = {'type': 'text_delta', 'delta': event.delta}
        return f"data: {json.dumps(data, default=_event_json_default)}\n\n"
    elif isinstance(event, ToolCallEvent):
        data = {'type': 'tool_call', 'tool_calls': event.tool_calls}
        return f"data: {json.dumps(data, default=_event_json_default)}\n\n"
    elif isinstance(event, ToolResultEvent):
        data = {
            'type': 'tool_result',
            'tool_name': event.tool_name,
            'success': event.success,
            'data': event.data,
            'error': event.error
        }
        return f"data: {json.dumps(data, default=_event_json_default)}\n\n"
    elif isinstance(event, FinalizeEvent):
        data = {
            'type': 'finalize',
            'answer': event.answer,
            'reasoning_trace': event.reasoning_trace
        }
        return f"data: {json.dumps(data, default=_event_json_default)}\n\n"
    elif isinstance(event, ErrorEvent):
        data = {'type': 'error', 'error': event.error}
        return f"data: {json.dumps(data, default=_event_json_default)}\n\n"
    elif isinstance(event, StageEvent):
        data = {'type': 'stage', 'stage': event.stage, 'detail': event.detail}
        return f"data: {json.dumps(data, default=_event_json_default)}\n\n"
    elif isinstance(event, MetaEvent):
        data = {
            'type': 'meta',
            'session_id': event.session_id,
            'task_type': event.task_type,
            'extraction_summary': event.extraction_summary,
            'search_extraction_summary': event.search_extraction_summary,
            'search_info': event.search_info,
        }
        return f"data: {json.dumps(data, default=_event_json_default)}\n\n"
    else:
        return f"data: {json.dumps({'type': 'unknown'}, default=_event_json_default)}\n\n"


async def stream_tool_loop(
    llm_client,
    messages: list[dict],
    tools: list[dict],
    think: bool = False,
    num_predict: int | None = None,
    model: str | None = None,
    user_id: str = "",
    session_id: str = "",
) -> AsyncGenerator[str, None]:
    """Stream the full tool loop including tool calls and final answer (SSE format).

    This is Phase 4: stream tool calls too.
    """
    import logging

    from assistant.backend.pipeline.async_tools import format_tool_result
    from assistant.backend.pipeline.llm_client import ChatMessage
    from assistant.backend.pipeline.tool_executor import ToolResult, execute_tool
    from assistant.backend.pipeline.tools import MAX_TOOL_ROUNDS

    logger = logging.getLogger(__name__)

    chat_messages = [ChatMessage(**m) for m in messages]
    loop_model = model or llm_client.tools_model

    max_turns = MAX_TOOL_ROUNDS

    turn = 0
    reasoning_trace: list[str] = []

    while turn < max_turns:
        turn += 1

        logger.info(
            "DEBUG stream_tool_loop: Turn %d, calling LLM (%s)",
            turn,
            loop_model,
        )

        response = await llm_client.chat(
            messages=chat_messages,
            tools=tools,
            tool_choice="auto",
            model=loop_model,
            temperature=0.3,
            think=think,
            num_predict=num_predict,
        )

        if response.tool_calls:
            # Yield tool call events
            tool_calls_data = [
                {"name": tc.name, "arguments": tc.arguments}
                for tc in response.tool_calls
            ]
            yield serialize_event(ToolCallEvent(tool_calls_data))

            for call in response.tool_calls:
                tool_name = call.name
                raw_args = call.arguments or {}

                # Execute tool
                try:
                    result = await execute_tool(
                        tool_name,
                        raw_args,
                        user_id=user_id,
                        session_id=session_id,
                    )
                except Exception as e:
                    logger.error(f"Tool execution error: {e}")
                    result = ToolResult(success=False, data={}, error=str(e))

                # Yield tool result event
                yield serialize_event(
                    ToolResultEvent(
                        tool_name=tool_name,
                        success=getattr(result, "success", True),
                        data=getattr(result, "data", {}),
                        error=getattr(result, "error", ""),
                    )
                )

                # Check for finalize
                if tool_name == "finalize":
                    answer = raw_args.get("answer", "Done.")
                    event = FinalizeEvent(
                        answer,
                        "\n\n".join(reasoning_trace) if reasoning_trace else None
                    )
                    yield serialize_event(event)
                    return

                # Record think() reasoning
                if tool_name == "think":
                    reasoning_trace.append(raw_args.get("reasoning", ""))

                # Add tool result to messages (surface the error on failure so
                # the model can recover instead of retrying the same call)
                chat_messages.append(
                    ChatMessage(
                        role="tool",
                        content=format_tool_result(result),
                        name=tool_name
                    )
                )
        else:
            # No tool calls = direct answer
            answer = response.content or "I'm not sure how to respond."
            event = FinalizeEvent(
                answer,
                "\n\n".join(reasoning_trace) if reasoning_trace else None
            )
            yield serialize_event(event)
            return

    # Max turns reached - synthesize a graceful wrap-up from the model instead
    # of dumping raw tool metadata at the user. This extra LLM call only fires
    # on the already-failing path (per the "lean on the model" principle; a
    # neutral message is a last resort if even the wrap-up call fails).
    answer = None
    try:
        wrap_up = await llm_client.chat(
            messages=[
                *chat_messages,
                ChatMessage(
                    role="user",
                    content=(
                        "You have used all your available tool-call turns "
                        "without producing a final answer. Wrap up for the "
                        "user now in plain language: state what you were able "
                        "to do, what blocked you (including any tool errors), "
                        "and what you need from them next. Do not describe "
                        "the tool loop internals or repeat raw tool data."
                    ),
                ),
            ],
            tools=tools,
            tool_choice="none",
            model=loop_model,
            temperature=0.3,
            think=think,
            num_predict=num_predict,
        )
        answer = (wrap_up.content or "").strip() or None
    except Exception:
        logger.warning(
            "Tool wrap-up LLM call failed; using graceful fallback",
            exc_info=True,
        )
    if answer is None:
        answer = (
            "I wasn't able to complete that within my allowed steps. "
            "Let me know how you'd like me to adjust."
        )
    event = FinalizeEvent(
        answer,
        "\n\n".join(reasoning_trace) if reasoning_trace else None
    )
    yield serialize_event(event)


async def merge_sse(
    primary: AsyncGenerator[str, None],
    event_queue: asyncio.Queue,
    poll: float = 0.05,
) -> AsyncGenerator[str, None]:
    """Interleave primary SSE events with side-channel events from a queue.

    The primary generator (e.g. the orchestrator's stream) is drained by a
    background task, so we never cancel a long-awaited pipeline step while
    polling for stage updates. Both channels are streamed out with at most
    ``poll`` seconds of latency. An unexpected exception in ``primary`` is
    surfaced as an SSE ``error`` event instead of silently ending the stream.
    """
    sentinel = object()
    out: asyncio.Queue = asyncio.Queue()

    async def _drain_primary():
        try:
            async for item in primary:
                out.put_nowait(item)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # surface stream failures as SSE errors
            out.put_nowait(exc)
        finally:
            out.put_nowait(sentinel)

    task = asyncio.create_task(_drain_primary())
    try:
        while True:
            # Side-channel events first (stage updates).
            while not event_queue.empty():
                yield event_queue.get_nowait()
            # Then anything the primary produced since the last poll.
            while not out.empty():
                item = out.get_nowait()
                if item is sentinel:
                    while not event_queue.empty():
                        yield event_queue.get_nowait()
                    return
                if isinstance(item, Exception):
                    yield serialize_event(ErrorEvent(str(item)))
                    while not event_queue.empty():
                        yield event_queue.get_nowait()
                    return
                yield item
            await asyncio.sleep(poll)
    finally:
        task.cancel()