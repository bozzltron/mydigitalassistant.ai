"""SSE streaming helpers for the chat endpoint.

Provides event serialization and the streaming response generator.
"""

import json
from collections.abc import AsyncGenerator


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


def serialize_event(event: ToolLoopEvent) -> str:
    """Serialize a tool loop event as an SSE message."""
    if isinstance(event, TextDeltaEvent):
        data = {'type': 'text_delta', 'delta': event.delta}
        return f"data: {json.dumps(data)}\n\n"
    elif isinstance(event, ToolCallEvent):
        data = {'type': 'tool_call', 'tool_calls': event.tool_calls}
        return f"data: {json.dumps(data)}\n\n"
    elif isinstance(event, ToolResultEvent):
        data = {
            'type': 'tool_result',
            'tool_name': event.tool_name,
            'success': event.success,
            'data': event.data,
            'error': event.error
        }
        return f"data: {json.dumps(data)}\n\n"
    elif isinstance(event, FinalizeEvent):
        data = {
            'type': 'finalize',
            'answer': event.answer,
            'reasoning_trace': event.reasoning_trace
        }
        return f"data: {json.dumps(data)}\n\n"
    elif isinstance(event, ErrorEvent):
        data = {'type': 'error', 'error': event.error}
        return f"data: {json.dumps(data)}\n\n"
    else:
        return f"data: {json.dumps({'type': 'unknown'})}\n\n"


async def stream_final_answer(
    llm_client,
    messages: list[dict],
    tools: list[dict] | None = None,
    think: bool = False,
    num_predict: int | None = None,
    model: str | None = None,
    user_id: str = "",
    session_id: str = "",
) -> AsyncGenerator[str, None]:
    """Stream the final answer from the chat model (SSE format).

    This is Phase 1: stream only the final answer, tool calls remain synchronous.
    """
    from assistant.backend.pipeline.llm_client import ChatMessage

    # Convert messages to ChatMessage format
    chat_messages = [ChatMessage(**m) for m in messages]

    # Use the tools model for tool calling if tools are provided
    loop_model = model or llm_client.tools_model

    # Run tool loop synchronously first
    from assistant.backend.pipeline.tools import run_tool_loop

    try:
        result = await run_tool_loop(
            llm_client,
            chat_messages,
            tools or [],
            user_id=user_id,
            session_id=session_id,
            think=think,
            num_predict=num_predict,
            model=loop_model,
        )
    except Exception as e:
        yield serialize_event(ErrorEvent(str(e)))
        return

    answer = result.get("answer", "")
    reasoning_trace = result.get("reasoning_trace")

    # Now stream the final answer using the chat model
    final_messages = chat_messages + [
        ChatMessage(role="assistant", content=answer)
    ]

    # Stream the answer from the chat model
    async for chunk in llm_client.chat_stream(
        final_messages,
        model=llm_client.chat_model,
        think=think,
        num_predict=num_predict,
    ):
        if chunk.content:
            yield serialize_event(TextDeltaEvent(chunk.content))
        if chunk.done:
            yield serialize_event(FinalizeEvent(answer, reasoning_trace))
            break


async def stream_tool_loop(
    llm_client,
    messages: list[dict],
    tools: list[dict],
    think: bool = False,
    num_predict: int | None = None,
    model: str | None = None,
) -> AsyncGenerator[str, None]:
    """Stream the full tool loop including tool calls and final answer (SSE format).

    This is Phase 4: stream tool calls too.
    """
    import logging

    from assistant.backend.pipeline.llm_client import ChatMessage
    from assistant.backend.pipeline.tool_executor import execute_tool

    logger = logging.getLogger(__name__)

    chat_messages = [ChatMessage(**m) for m in messages]
    loop_model = model or llm_client.tools_model

    # Determine max turns
    max_turns = 3  # Default from tools.py

    turn = 0
    tool_results: list[dict] = []
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
                        user_id="",
                        session_id="",
                    )
                except Exception as e:
                    logger.error(f"Tool execution error: {e}")
                    result = type(
                        "ToolResult",
                        (),
                        {
                            "success": False,
                            "error": str(e),
                            "data": {},
                            "metadata": {},
                        },
                    )()

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

                # Add tool result to messages
                chat_messages.append(
                    ChatMessage(
                        role="tool",
                        content=str(getattr(result, "data", {})),
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

    # Max turns reached
    answer = (
        f"I've considered this for {max_turns} turns. "
        f"Here's what I found: " + str(tool_results)
    )
    event = FinalizeEvent(
        answer,
        "\n\n".join(reasoning_trace) if reasoning_trace else None
    )
    yield serialize_event(event)