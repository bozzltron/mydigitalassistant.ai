# Main function-calling memory loop
# Orchestrates multi-turn deliberation where LLM invokes tools

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from assistant.backend.config import settings
from assistant.backend.pipeline.context_builder import (
    build_initial_context,
    build_loop_prompt,
    format_system_prompt,
)
from assistant.backend.pipeline.tool_executor import execute_tool
from assistant.backend.memory.store import get_session_id  # helper
from assistant.backend.pipeline.tools import builtin_tools

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Loop configuration
# ---------------------------------------------------------------------------

MAX_REASONING_TURNS: dict[str, int] = {
    "none": 0,
    "low": 1,
    "medium": getattr(settings, "MAX_REASONING_TURNS", 3),
    "high": getattr(settings, "MAX_REASONING_TURNS_HIGH", 5),
}

REASONING_TEMPERATURE: dict[str, float] = {
    "none": 0.7,
    "low": 0.5,
    "medium": 0.3,
    "high": 0.2,
}


# ---------------------------------------------------------------------------
# Main chat loop
# ---------------------------------------------------------------------------


async def chat_loop(
    user_id: str,
    session_id: str,
    user_message: str,
    reasoning_effort: str = "medium",
    stream: bool = False,
) -> dict[str, object]:
    """
    Main deliberation loop.

    Runs until LLM calls finalize() or max_turns reached.
    Returns {"answer": str, "loop_terminated": str, "memory_updated": bool}.
    """
    # 1. Validate reasoning_effort
    if reasoning_effort not in MAX_REASONING_TURNS:
        reasoning_effort = "medium"

    max_turns = MAX_REASONING_TURNS[reasoning_effort]
    temperature = REASONING_TEMPERATURE[reasoning_effort]

    # 2. Build initial context
    bundle = await build_initial_context(user_id, session_id, user_message)

    # 3. Initialize state
    turn = 0
    tool_results: list[dict] = []
    reasoning_trace: list[str] = []

    # 4. Stream setup (if enabled)
    stream_queue: asyncio.Queue[dict] | None = (
        asyncio.Queue() if stream else None
    )

    # 5. Get tool definitions
    tools = builtin_tools()

    # 5. Main loop
    while turn < max_turns:
        turn += 1

        # 5.1 Construct prompt
        messages = build_loop_prompt(
            bundle,
            tool_results,
            reasoning_trace,
            user_message,
        )

        # 5.2 Call LLM with tool definitions
        from assistant.backend.pipeline.llm_client import OllamaClient

        llm_client = OllamaClient(
            base_url=getattr(settings, "OLLAMA_URL", "http://127.0.0.1:11434"),
            chat_model=getattr(settings, "CHAT_MODEL", "qwen2.5:7b"),
        )

        response = await llm_client.chat(
            messages=messages,
            tools=tools,
            tool_choice="auto",
            model=settings.chat_model,
            temperature=temperature,
            think=True,
        )

        # 5.3 Parse tool calls from response
        if response.tool_calls:
            # 5.4 Execute each tool call
            for call in response.tool_calls:
                tool_name = call.name
                raw_args = call.arguments or {}

                # Execute tool with timeout
                try:
                    result = await execute_tool(
                        tool_name,
                        raw_args,
                        user_id=user_id,
                        session_id=session_id,
                    )
                except Exception as e:
                    logger.error(f"Tool execution error: {e}")
                    result = type("ToolResult", (), {
                        "success": False,
                        "error": str(e),
                        "data": {},
                        "metadata": {}
                    })()

                # 5.4.1 finalize ends the loop immediately
                if tool_name == "finalize":
                    answer = raw_args.get("answer", "Thank you for the information.")
                    return {
                        "answer": answer,
                        "loop_terminated": "finalize",
                        "memory_updated": True,
                        "turns": turn,
                        "reasoning_effort": reasoning_effort,
                    }

                # 5.4.2 Record tool result for next iteration
                tool_results.append(
                    {
                        "call": {"name": tool_name, "args": raw_args},
                        "result": {
                            "success": result.success,
                            "data": result.data,
                            "error": result.error,
                        },
                        "metadata": result.metadata,
                    }
                )

                # 5.4.3 Record think() reasoning
                if tool_name == "think":
                    reasoning_trace.append(raw_args.get("reasoning", ""))

                # Add tool result to messages for next iteration
                messages.append(
                    {"role": "tool", "content": str(result.data)}
                )
        else:
            # No tool calls = direct answer (finalize)
            answer = response.content or "I'm not sure how to respond."
            return {
                "answer": answer,
                "loop_terminated": "finalize",
                "memory_updated": len(tool_results) > 0,
                "turns": turn,
                "reasoning_effort": reasoning_effort,
            }

    # 6. Max turns reached - force finalize
    answer = (
        f"I've considered this for {max_turns} turns. "
        "Here's what I found: "
        + str(tool_results)
    )
    return {
        "answer": answer,
        "loop_terminated": "max_turns",
        "memory_updated": True,
        "turns": turn,
        "reasoning_effort": reasoning_effort,
    }