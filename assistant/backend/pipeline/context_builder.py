# Context building for the function-calling loop
# Handles tiered memory context assembly and prompt construction

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from assistant.backend.config import settings
from assistant.backend.memory.store import (
    get_essential_frames,
    semantic_search,
    get_recent_episodes,
)
# ---------------------------------------------------------------------------

CONTEXT_TOKEN_BUDGET = getattr(settings, "CONTEXT_TOKEN_BUDGET", 6000)
CORE_FRAMES_TOKEN_BUDGET = getattr(settings, "CORE_FRAMES_TOKEN_BUDGET", 500)
ACTIVE_FRAMES_TOKEN_BUDGET = getattr(settings, "ACTIVE_FRAMES_TOKEN_BUDGET", 1500)
EPISODES_TOKEN_BUDGET = getattr(settings, "EPISODES_TOKEN_BUDGET", 1000)
TOOL_RESULTS_TOKEN_BUDGET = getattr(settings, "TOOL_RESULTS_TOKEN_BUDGET", 2000)
REASONING_TRACE_TOKEN_BUDGET = getattr(settings, "REASONING_TRACE_TOKEN_BUDGET", 1000)


# ---------------------------------------------------------------------------
# Context bundle dataclass
# ---------------------------------------------------------------------------


class ContextBundle:
    """Assembled context for a single loop iteration."""

    core: list[dict]  # essential frames always injected
    active: list[dict]  # pre-fetched likely-relevant frames
    recent_episodes: list[dict]  # last N turns verbatim
    user_message: str


# ---------------------------------------------------------------------------
# Tier 1: Core frames (always in system prompt)
# ---------------------------------------------------------------------------


async def load_core_frames(user_id: str) -> list[dict]:
    """Essential frames that are always injected into the system prompt."""
    frames = await get_essential_frames(user_id=user_id)
    # Convert to serializable dict format
    result = []
    for f in frames:
        result.append(
            {
                "frame_name": f.name,
                "slots": {
                    k: {"value": v.value, "confidence": v.confidence}
                    for k, v in f.slots.items()
                },
            }
        )
    return result


# ---------------------------------------------------------------------------
# Tier 2: Active frames (pre-fetched via query embedding)
# ---------------------------------------------------------------------------

async def load_active_frames(user_message: str, user_id: str, limit: int = 15) -> list[dict]:
    """Pre-fetch frames likely relevant to the current user query."""
    query_embedding = await embed_query(user_message)
    results = await semantic_search(
        embedding=query_embedding,
        user_id=user_id,
        limit=limit,
    )
    # Convert to serializable dict format
    result = []
    for r in results:
        result.append(
            {
                "frame_name": r.frame_name,
                "similarity": r.similarity,
                "confidence": r.confidence,
                "priority": r.priority,
            }
        )
    return result


# ---------------------------------------------------------------------------
# Tier 3: Recent episodes (verbatim last N turns)
# ---------------------------------------------------------------------------

async def load_recent_episodes(session_id: str, limit: int = 6) -> list[dict]:
    """Recent conversation turns, verbatim, for context."""
    episodes = await get_recent_episodes(session_id=session_id, limit=limit)
    result = []
    for e in episodes:
        result.append(
            {
                "role": e.role,
                "content": e.content,
                "timestamp": e.timestamp.isoformat() if e.timestamp else None,
            }
        )
    return result


# ---------------------------------------------------------------------------
# Tier 4: Tool results from previous iterations
# ---------------------------------------------------------------------------

def format_tool_result(result: dict) -> dict:
    """Format a tool result for inclusion in the prompt."""
    return {
        "tool": result.get("call", {}).get("name", "unknown"),
        "args": result.get("call", {}).get("args", {}),
        "result": result.get("result", {}),
        "metadata": result.get("metadata", {}),
    }


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------


SYSTEM_PROMPT_TEMPLATE = """You are a cognitive assistant with a structured memory system.

MEMORY MODEL:
- Frames: entities/concepts/events (e.g., "person_john", "project_alpha")
- Slots: typed key/value on frames with confidence (0-1), source, priority
- Associations: typed relations between frames (related, causes, part_of, etc.)
- Episodes: conversation turns linked to touched frames

TOOLS AVAILABLE:
{tool_definitions}

INSTRUCTIONS:
1. Use tools to read/write memory. Think before answering.
2. Call recall() when you need information not in context.
3. Call upsert_slot() when you learn something worth remembering.
4. Call web_search() + fetch_url() for external information.
5. Call think() to decompose complex problems.
6. Call plan() for multi-step tasks.
7. Call finalize(answer) when you have a complete response.

CONSTRAINTS:
- Never fabricate facts. Only cite from search results or memory.
- Confidence ladder applies: source_reliability → confidence → priority → recency.
- Conflicts auto-resolved via AGM revision; you'll see conflict details in tool results.
- Essential frames protected from forgetting.

Current user: {user_id}
Current session: {session_id}
"""


async def build_initial_context(
    user_id: str,
    session_id: str,
    user_message: str,
) -> ContextBundle:
    """Assemble the four-tier context bundle for a new loop iteration."""
    # Tier 1: Core frames (always)
    core = await load_core_frames(user_id)

    # Tier 2: Active frames (pre-fetched via query embedding)
    active = await load_active_frames(user_message, user_id)

    # Tier 3: Recent episodes
    recent = await load_recent_episodes(session_id)

    return ContextBundle(
        core=core,
        active=active,
        recent_episodes=recent,
        user_message=user_message,
    )


def format_system_prompt(
    user_id: str,
    session_id: str,
    tool_definitions_text: str,
) -> str:
    """Format the stable system prompt (cached by Ollama)."""
    return SYSTEM_PROMPT_TEMPLATE.format(
        tool_definitions=tool_definitions_text,
        user_id=user_id,
        session_id=session_id,
    )


def build_loop_prompt(
    bundle: ContextBundle,
    tool_results: list[dict],
    reasoning_trace: list[str],
    user_message: str,
) -> list[dict[str, str]]:
    """
    Build the full message list for an LLM call.

    Returns a list of {role, content} dicts compatible with Ollama chat API.
    """
    messages: list[dict[str, str]] = []

    # 1. System prompt (stable, cached)
    # Note: in practice this is injected once and cached; we include it here
    # for completeness but the actual caching happens at the Ollama client level.
    # messages.append({"role": "system", "content": format_system_prompt(...)})

    # 2. Core frames (always injected)
    if bundle.core:
        core_text = "Core memory frames:\n" + str(bundle.core)
        messages.append({"role": "system", "content": core_text})

    # 3. Active frames (pre-fetched)
    if bundle.active:
        active_text = "Active frames (pre-fetched):\n" + str(bundle.active)
        messages.append({"role": "system", "content": active_text})

    # 4. Recent episodes (verbatim)
    if bundle.recent_episodes:
        episode_lines = []
        for ep in bundle.recent_episodes:
            role_label = "User" if ep["role"] == "user" else "Assistant"
            episode_lines.append(f"{role_label}: {ep['content']}")
        episodes_text = "Recent conversation:\n" + "\n".join(episode_lines)
        messages.append({"role": "system", "content": episodes_text})

    # 5. Tool results from previous turns
    for tr in tool_results:
        # Truncate if too verbose
        result_str = f"Tool: {tr.get('tool', 'unknown')}\nArgs: {tr.get('args', {})}\nResult: {tr.get('result', {})}"
        messages.append({"role": "tool", "content": result_str})

    # 6. Reasoning trace
    if reasoning_trace:
        trace_text = "Reasoning so far:\n" + "\n".join(f"- {r}" for r in reasoning_trace[-5:])
        messages.append({"role": "system", "content": trace_text})

    # 7. Current user message
    messages.append({"role": "user", "content": user_message})

    return messages