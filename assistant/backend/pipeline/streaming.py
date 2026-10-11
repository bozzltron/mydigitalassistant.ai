"""SSE streaming helpers for the chat endpoint.

Provides event serialization and the streaming response generator.
"""

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from dataclasses import asdict, is_dataclass
from enum import Enum

logger = logging.getLogger(__name__)

# When the loop's content allowance is spent, an older tool result is replaced by
# this marker rather than dropped from the message list -- the tool_call it answers
# stays paired, and the model is told the result is gone and can be re-fetched.
DROPPED_TOOL_RESULT = (
    "[earlier tool result dropped to fit this turn's context budget; "
    "run the tool again if you still need it]"
)


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
    """Tool loop finalized with answer.

    Carries the loop's **peak prompt size** and the window it was measured in --
    the true context cost of the turn, which the initial `context_stats` line
    (system prompt only) cannot see. Zero when no LLM call ran.

    ``tool_result_chars`` is the peak total characters of tool results held in the
    prompt during the loop -- the part of the prompt that grows with each round.
    Reported alongside the fixed cost (system prompt + tool schemas + history,
    known to the caller) so `context_usage` can show where the window actually
    went. ``tool_results_dropped`` counts older results collapsed to make room for
    a newer one once the turn's content allowance was spent.
    """

    def __init__(
        self,
        answer: str,
        reasoning_trace: str | None = None,
        prompt_tokens: int = 0,
        context_window: int = 0,
        tool_result_chars: int = 0,
        tool_results_dropped: int = 0,
    ):
        self.type = "finalize"
        self.answer = answer
        self.reasoning_trace = reasoning_trace
        self.prompt_tokens = prompt_tokens
        self.context_window = context_window
        self.tool_result_chars = tool_result_chars
        self.tool_results_dropped = tool_results_dropped


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
        confidence: float | None = None,
        confidence_basis: str | None = None,
        citations: list[str] | None = None,
        memory_context: str | None = None,
        prompt_tokens: int = 0,
        context_window: int = 0,
        conversation_summary: str = "",
    ):
        self.type = "meta"
        self.session_id = session_id
        self.task_type = task_type
        self.extraction_summary = extraction_summary
        self.search_extraction_summary = search_extraction_summary
        self.search_info = search_info
        self.confidence = confidence
        self.confidence_basis = confidence_basis
        # The last two ChatResponse fields the stream did not carry, so a
        # consumer that needs the full response shape (the `chat()` adapter, and
        # the CLI) can rebuild it from the stream alone.
        self.citations = citations or []
        self.memory_context = memory_context or ""
        # The tool loop's peak prompt and its window -- the context meter.
        self.prompt_tokens = prompt_tokens
        self.context_window = context_window
        # This conversation's stored summary, if it has one. Transparency: the
        # user can see what the agent compressed about the thread. It rides the
        # meta, not the prompt, so it costs no context.
        self.conversation_summary = conversation_summary


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
            'reasoning_trace': event.reasoning_trace,
            'prompt_tokens': event.prompt_tokens,
            'context_window': event.context_window,
            'tool_result_chars': event.tool_result_chars,
            'tool_results_dropped': event.tool_results_dropped,
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
            'confidence': event.confidence,
            'confidence_basis': event.confidence_basis,
            'citations': event.citations,
            'memory_context': event.memory_context,
            'prompt_tokens': event.prompt_tokens,
            'context_window': event.context_window,
            'conversation_summary': event.conversation_summary,
        }
        return f"data: {json.dumps(data, default=_event_json_default)}\n\n"
    else:
        return f"data: {json.dumps({'type': 'unknown'}, default=_event_json_default)}\n\n"


_ACTION_NUDGE = (
    "You ended your turn without performing the action the user asked for. "
    "Do it now — call the tool in this message."
)

_COMPLETION_NUDGE = (
    "You replied that you had done the action, but the tools you called did not "
    "perform it. Call the tool that performs it now."
)

def _summarize_call(name: str, args: dict) -> str:
    """A short ``name(key=value, …)`` line for the completion judgement.

    Values are truncated, and a ``content`` argument is omitted entirely: it is a
    whole file body, and the judgement is about *which* tool ran, not what it
    contained.
    """
    if not isinstance(args, dict):
        return name
    parts: list[str] = []
    for key, value in list(args.items())[:3]:
        if key == "content":
            parts.append("content=<omitted>")
            continue
        parts.append(f"{key}={repr(value)[:60]}")
    return f"{name}({', '.join(parts)})"


async def _maybe_nudge(
    *,
    expects_action: bool,
    made_tool_call: bool,
    already_nudged: bool,
    request_text: str,
    tool_calls_made: list[str],
) -> str | None:
    """The nudge note for this turn, or ``None``.

    The model-first fix for a turn that announces an action and never performs it:
    a decision model judged the request needs an action (``expects_action``); if
    the turn ends without doing it, the model is handed its own message back once
    and asked to act. Fires at most once per turn.
    """
    if not expects_action or already_nudged:
        return None

    if not made_tool_call:
        logger.info("Action nudge: no tool was called for a request that needs an action")
        return _ACTION_NUDGE

    # A tool ran — but did it do the job? A turn can call a *different* tool (a
    # read) and still claim the write, measured live. Judge the calls, not the
    # assistant's claim.
    from assistant.backend.pipeline.decision import outstanding

    if await outstanding(request_text, tool_calls_made):
        logger.info("Action nudge: the tool calls did not perform the action")
        return _COMPLETION_NUDGE
    return None


async def stream_tool_loop(
    llm_client,
    messages: list[dict],
    tools: list[dict],
    think: bool = False,
    num_predict: int | None = None,
    model: str | None = None,
    user_id: str = "",
    session_id: str = "",
    max_turns: int | None = None,
    expects_action: bool = False,
) -> AsyncGenerator[str, None]:
    """Stream the full tool loop including tool calls and final answer (SSE format).

    This is Phase 4: stream tool calls too.

    ``expects_action``: a decision model judged that the request needs a tool
    action (``decision.needs_action``). If the model then ends its turn with no
    tool call, it is handed its own message back once and asked to act — the
    model-first fix for a turn that announces an action and never performs it.
    """
    from assistant.backend.config import settings
    from assistant.backend.pipeline.context_budget import content_char_limit
    from assistant.backend.pipeline.llm_client import (
        EMPTY_GENERATION_FALLBACK,
        ChatMessage,
        compact_messages,
    )
    from assistant.backend.pipeline.tool_executor import (
        ToolResult,
        execute_tool,
        format_tool_result,
    )

    chat_messages = [ChatMessage(**m) for m in messages]
    loop_model = model or llm_client.tools_model
    # The user's request, for the completion judgement — read once, before any
    # nudge adds a user-role message of its own.
    request_text = next(
        (m.content for m in reversed(chat_messages) if getattr(m, "role", "") == "user"),
        "",
    )

    # The cap is a runaway guard; the model ends the loop itself. A caller that
    # knows the task's complexity (the orchestrator, from the reasoner's plan)
    # passes a larger ceiling for deep work.
    max_turns = max_turns or settings.max_tool_rounds

    # The peak prompt across the loop's turns -- the turn's true context cost.
    # Turn 1 is the system prompt + history; the tool results that follow are what
    # make the loop the largest prompt in the system, so the peak is usually a
    # later turn. Reported against the window it was measured in.
    max_prompt_tokens = 0
    context_window = llm_client.context_window(loop_model)
    # The tool results held in the prompt, and their peak, so the window's
    # composition is visible per turn (see context_usage). The aggregate is capped
    # at the turn's content allowance: once it is spent, an older result is
    # collapsed to a marker to make room for a newer one, so a long tool chain
    # cannot overflow the window round over round.
    content_allowance = content_char_limit()
    tool_result_messages: list[ChatMessage] = []
    tool_result_chars = 0
    peak_tool_result_chars = 0
    tool_results_dropped = 0

    turn = 0
    reasoning_trace: list[str] = []
    empty_retried = False
    # The action nudge: fired at most once, and only when the action is missing.
    made_tool_call = False
    action_nudged = False
    tool_calls_made: list[str] = []

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
        max_prompt_tokens = max(max_prompt_tokens, response.prompt_eval_count)

        # An empty generation with no tool call means the model emitted nothing:
        # on this stack that is almost always context exhaustion. The prompt —
        # system prompt + every tool schema + history — filled the window and
        # left no room for the answer (llama-server logs truncated=1; Ollama
        # reports done_reason="length"), so the output was cut off to empty.
        # Retry once with history and prior tool chatter dropped, which is the
        # only part of the prompt we can give back. Without this the turn
        # finalized with a comprehension-sounding fallback that hid the cause.
        if (
            not response.tool_calls
            and not (response.content or "").strip()
            and not empty_retried
        ):
            empty_retried = True
            compacted = compact_messages(chat_messages)
            logger.warning(
                "Empty generation (done_reason=%r); retrying with %d of %d "
                "messages after dropping history",
                response.done_reason,
                len(compacted),
                len(chat_messages),
            )
            chat_messages = compacted
            response = await llm_client.chat(
                messages=chat_messages,
                tools=tools,
                tool_choice="auto",
                model=loop_model,
                temperature=0.3,
                think=think,
                num_predict=num_predict,
            )
            max_prompt_tokens = max(max_prompt_tokens, response.prompt_eval_count)

        if response.tool_calls:
            made_tool_call = True
            # Yield tool call events
            tool_calls_data = [
                {"name": tc.name, "arguments": tc.arguments}
                for tc in response.tool_calls
            ]
            yield serialize_event(ToolCallEvent(tool_calls_data))

            for call in response.tool_calls:
                tool_name = call.name
                raw_args = call.arguments or {}
                tool_calls_made.append(_summarize_call(tool_name, raw_args))

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
                        "\n\n".join(reasoning_trace) if reasoning_trace else None,
                        prompt_tokens=max_prompt_tokens,
                        context_window=context_window,
                        tool_result_chars=peak_tool_result_chars,
                        tool_results_dropped=tool_results_dropped,
                    )
                    yield serialize_event(event)
                    return

                # Record think() reasoning
                if tool_name == "think":
                    reasoning_trace.append(raw_args.get("reasoning", ""))

                # Add tool result to messages (surface the error on failure so
                # the model can recover instead of retrying the same call).
                formatted_result = format_tool_result(result)
                # The aggregate cap: the loop's rounds share one content allowance.
                # Collapse older results (oldest first) to make room for this one,
                # so a long tool chain cannot overflow the window round over round.
                if tool_result_chars + len(formatted_result) > content_allowance:
                    for old in tool_result_messages:
                        if tool_result_chars + len(formatted_result) <= content_allowance:
                            break
                        if len(old.content) > len(DROPPED_TOOL_RESULT):
                            tool_result_chars += len(DROPPED_TOOL_RESULT) - len(old.content)
                            old.content = DROPPED_TOOL_RESULT
                            tool_results_dropped += 1
                tool_result_chars += len(formatted_result)
                result_message = ChatMessage(
                    role="tool", content=formatted_result, name=tool_name
                )
                tool_result_messages.append(result_message)
                chat_messages.append(result_message)
                peak_tool_result_chars = max(peak_tool_result_chars, tool_result_chars)
        else:
            # No tool calls. If the request needed an action and the turn did not
            # perform it, hand the model its own message back once (see _maybe_nudge).
            nudge = await _maybe_nudge(
                expects_action=expects_action,
                made_tool_call=made_tool_call,
                already_nudged=action_nudged,
                request_text=request_text,
                tool_calls_made=tool_calls_made,
            )
            if nudge:
                action_nudged = True
            if nudge:
                # Surface it: a self-correction the user cannot see is just latency.
                yield serialize_event(
                    StageEvent(stage="acting", detail="making sure that was done")
                )
                chat_messages.append(
                    ChatMessage(role="assistant", content=response.content or "")
                )
                chat_messages.append(ChatMessage(role="user", content=nudge))
                continue
            # No tool calls = direct answer
            answer = (response.content or "").strip() or EMPTY_GENERATION_FALLBACK
            event = FinalizeEvent(
                answer,
                "\n\n".join(reasoning_trace) if reasoning_trace else None,
                prompt_tokens=max_prompt_tokens,
                context_window=context_window,
                tool_result_chars=peak_tool_result_chars,
                tool_results_dropped=tool_results_dropped,
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
        max_prompt_tokens = max(max_prompt_tokens, wrap_up.prompt_eval_count)
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
        "\n\n".join(reasoning_trace) if reasoning_trace else None,
        prompt_tokens=max_prompt_tokens,
        context_window=context_window,
        tool_result_chars=peak_tool_result_chars,
        tool_results_dropped=tool_results_dropped,
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