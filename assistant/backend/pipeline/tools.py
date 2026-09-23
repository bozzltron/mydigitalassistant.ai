"""Tool framework (Phase 6 M5): native Ollama tool-calling on the chat model.

A minimal registry of household-safe, local-first tools. The chat model
decides when to call them; results are fed back until it answers in prose.
No cloud APIs — web_search goes through the local SearXNG instance only.
"""

from __future__ import annotations

import logging
import re
import urllib.parse
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser

import httpx
from pydantic import BaseModel, Field

from assistant.backend.config import settings
from assistant.backend.memory.store import MemoryStore
from assistant.backend.pipeline.llm_client import ChatMessage, OllamaClient

logger = logging.getLogger(__name__)


MAX_TOOL_ROUNDS = 3
MAX_FETCH_BYTES = 500_000
FETCH_TIMEOUT_SECONDS = 15


class _HTMLTextExtractor(HTMLParser):
    """Strip HTML tags and return plain text."""

    def __init__(self) -> None:
        super().__init__()
        self._text: list[str] = []
        self._bullets: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("br", "hr"):
            self._text.append("\n")
        elif tag in ("p", "div", "li"):
            self._text.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("p", "div"):
            self._text.append("\n")

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if text:
            self._text.append(text)

    @property
    def text(self) -> str:
        joined = "".join(self._text)
        return " ".join(
            " ".join(line.split())
            for line in joined.split("\n")
            if line.strip()
        )


def _strip_html(html: str) -> str:
    try:
        extractor = _HTMLTextExtractor()
        extractor.feed(html)
        text = extractor.text
    except Exception:
        text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


@dataclass
class AssistantTool:
    """A callable tool exposed to the chat model via the Ollama tools API."""

    name: str
    description: str
    parameters: dict  # JSON schema for the function arguments
    handler: Callable[..., Awaitable[str]]

    def to_def(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def _current_datetime() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %A (%Z)")


async def _handle_datetime(**_: object) -> str:
    return _current_datetime()


async def _fetch_single_url(url: str) -> str:
    """Fetch a URL, strip HTML, return plain text. No external calls."""
    try:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return f"Error: only http/https URLs are supported, got {parsed.scheme!r}"
    except Exception as e:
        return f"Error: malformed URL {e}"

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(FETCH_TIMEOUT_SECONDS, read=20.0),
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; AssistantBot/1.0)"},
        ) as client:
            # Simple robots.txt check
            allowed = await _check_robots_txt(client, url)
            if not allowed:
                return f"Error: {url} is blocked by robots.txt"

            r = await client.get(url)
            content_type = r.headers.get("content-type", "")
            if "text/html" not in content_type and "text/plain" not in content_type:
                return r.text[:2000]

            raw = r.content[:MAX_FETCH_BYTES]
            try:
                raw = raw.decode(r.encoding or "utf-8", errors="replace")
            except Exception:
                raw = raw.decode("utf-8", errors="replace")

            text = _strip_html(raw)

            if not text.strip():
                return (
                    "Error: page appears to be JavaScript-rendered "
                    "(empty after HTML strip). Try searching for the page "
                    "content instead."
                )

            snippet = text[:3000]
            if len(text) > 3000:
                snippet += f"\n... [{len(text):,} total characters, truncated to first 3000]"
            return snippet

    except httpx.TimeoutException:
        return f"Error: timeout fetching {url} ({FETCH_TIMEOUT_SECONDS}s)"
    except Exception as e:
        return f"Error fetching {url}: {e}"


async def _check_robots_txt(client: httpx.AsyncClient, url: str) -> bool:
    """Check robots.txt for a given URL. Default to allowing."""
    try:
        parsed = urllib.parse.urlparse(url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        r = await client.get(robots_url, timeout=5.0)
        if r.status_code != 200:
            return True  # no robots.txt = allow
        robots_text = r.text.lower()
        if "disallow: /" in robots_text and "user-agent: *" in robots_text:
            return False
        return True
    except Exception:
        return True  # on error, allow


def _make_fetch_url_handler(
    store: MemoryStore | None = None,
    llm_client: OllamaClient | None = None,
):
    """Create a fetch_url handler that auto-extracts facts into memory.

    Returns an async callable: handler(url) -> str
    """
    async def handler(url: str) -> str:
        content = await _fetch_single_url(url)
        if content.startswith("Error:"):
            return content

        if store and llm_client:
            try:
                from assistant.backend.pipeline.extractor import (
                    apply_extraction,
                    extract_facts_from_document,
                )

                extraction = await extract_facts_from_document(
                    content, url, llm_client
                )
                if extraction.slots or extraction.associations:
                    await apply_extraction(
                        extraction,
                        store,
                        source_type="web_fetch",
                        source_url=url,
                        source_reliability=0.7,
                    )
                    logger.info(
                        "fetch_url: extracted %d slots, %d assocs from %s",
                        len(extraction.slots),
                        len(extraction.associations),
                        url,
                    )
            except Exception as e:
                logger.warning("fetch_url: auto-extract failed for %s: %s", url, e)

        return content

    return handler


# ---------------------------------------------------------------------------
# Schema definitions (validated via Pydantic before dispatch)
# ---------------------------------------------------------------------------

class UpsertSlotArgs(BaseModel):
    frame_name: str = Field(
        ..., description="Entity/concept name (e.g. 'person_john')")
    slot_key: str = Field(
        ..., description="Attribute name (e.g. 'birthday')")
    slot_value: str = Field(
        ..., description="Value to store")
    confidence: float = Field(
        0.5, ge=0, le=1, description="Initial confidence (0.5=uncertain)")
    essential: bool = Field(
        False, description="Protected from GC/forgetting")
    priority: int = Field(
        0, description="Higher = more important for retrieval")
    source_type: str = Field(
        "conversation",
        description="conversation | search | document | correction | inference",
    )
    source_episode_id: str | None = Field(
        None, description="Link to originating turn")


class UpsertAssociationArgs(BaseModel):
    source_frame: str = Field(
        ..., description="Frame name")
    target_frame: str = Field(
        ..., description="Frame name")
    relation_type: str = Field(
        ...,
        description="related | causes | part_of | instance_of | contradicts | supports | custom",
    )
    confidence: float = Field(
        0.5, ge=0, le=1, description="Confidence in association")
    bidirectional: bool = Field(
        False, description="Create reverse relation too")


class MarkEssentialArgs(BaseModel):
    frame_name: str = Field(
        ..., description="Frame to protect")
    slot_key: str | None = Field(
        None, description="Optional: protect specific slot only")
    essential: bool = Field(
        True, description="Set essential=true")


class RecallArgs(BaseModel):
    query: str = Field(
        ..., description="Natural language query (embedded for similarity)")
    frame_types: list[str] | None = Field(
        None, description="Optional filter: frame name patterns")
    max_results: int = Field(
        10, ge=1, description="Max frames to return")
    min_confidence: float = Field(
        0.3, ge=0, le=1, description="Minimum confidence threshold")
    include_associations: bool = Field(
        True, description="Walk graph to connected frames")


GetFrameArgs = dict[str, str]  # just frame_name

GetSlotHistoryArgs = dict[str, str]  # frame_name + slot_key


class SearchEpisodesArgs(BaseModel):
    query: str = Field(
        ..., description="Natural language query")
    session_id: str | None = Field(
        None, description="Restrict to session")
    max_results: int = Field(
        5, ge=1, description="Max episodes to return")


# External tool args

class WebSearchArgs(BaseModel):
    query: str = Field(
        ..., description="Sanitized search query")
    num_results: int = Field(
        5, ge=1, le=20, description="Number of results")
    min_relevance: float = Field(
        0.3, ge=0, le=1, description="Cosine similarity threshold")


class FetchUrlArgs(BaseModel):
    url: str = Field(
        ..., description="URL to fetch")
    extract_facts: bool = Field(
        True, description="Auto-extract facts into memory")


class ReadFileArgs(BaseModel):
    path: str | None = Field(
        None, description="Relative path in sandbox (e.g., 'notes/todo.txt', 'data.csv')")
    frame_id: int | None = Field(
        None, description="Frame ID of uploaded file to read")
    frame_name: str | None = Field(
        None, description="Frame name of uploaded file to read")


class ListFilesArgs(BaseModel):
    pass


# File sandbox tool args
class WriteFileArgs(BaseModel):
    path: str = Field(
        ..., description="Relative path in sandbox (e.g., 'notes/todo.txt', 'scripts/analyze.py')")
    content: str = Field(..., description="File content to write")
    overwrite: bool = Field(False, description="Allow overwriting existing file")


class EditFileArgs(BaseModel):
    path: str = Field(..., description="Relative path in sandbox")
    old_text: str = Field(..., description="Exact text to replace")
    new_text: str = Field(..., description="Replacement text")
    replace_all: bool = Field(True, description="Replace all occurrences (default: true)")


class DeleteFileArgs(BaseModel):
    path: str = Field(..., description="Relative path in sandbox")


class GlobArgs(BaseModel):
    pattern: str = Field(
        ..., description="Glob pattern (e.g., '*.txt', 'notes/**/*.md', '**/*.py')")


class RunScheduledTaskArgs(BaseModel):
    task_name: str = Field(
        ..., description="Name of task to run immediately")


class ComputeArgs(BaseModel):
    expression: str = Field(
        ..., description="Natural language description of computation needed")
    context: dict | None = Field(
        None, description="Optional: variable bindings (e.g., {'rate': 0.07, 'years': 10})")
    precision: int = Field(
        4, description="Decimal places in result")


# ---------------------------------------------------------------------------
# Meta tool args
# ---------------------------------------------------------------------------

class PlanArgs(BaseModel):
    goal: str = Field(
        ..., description="The overall goal/task")
    steps: list[dict] = Field(
        ...,
        description="List of {tool, args, description} steps to execute")


class ThinkArgs(BaseModel):
    reasoning: str = Field(
        ..., description="Free-form reasoning trace")
    confidence: float = Field(
        ..., ge=0, le=1, description="Confidence in current conclusion")


class FinalizeArgs(BaseModel):
    answer: str = Field(
        ..., description="Final response to user")
    extraction_summary: dict | None = Field(
        None, description="Optional: summarize what was learned")


# ---------------------------------------------------------------------------
# Builtin tools registry – returns LLM-ready tool definitions
# ---------------------------------------------------------------------------

# Reduced tool set for the fast 1.5B tools model (Phase: Performance)
# Only tools that the tools model actually needs for file ops, memory writes, and reasoning
TOOLS_FOR_TOOL_MODEL = {
    "write_file", "read_file", "edit_file", "delete_file", "glob", "list_files",
    "recall", "upsert_slot", "upsert_association", "mark_essential",
    "think", "finalize",
}
# Excluded: web_search, fetch_url, compute, run_scheduled_task, plan, search_episodes


def _get_tools_for_model(
    tools: list[dict],
    model_name: str,
    llm_client: OllamaClient | None = None,
) -> list[dict]:
    """Filter tools based on the model being used.

    The tools model (1.5B) gets a reduced set for faster function calling.
    Other models get the full tool set.
    """
    if model_name == getattr(llm_client, "tools_model", "qwen2.5-coder:1.5b"):
        return [t for t in tools if t["function"]["name"] in TOOLS_FOR_TOOL_MODEL]
    return tools


def builtin_tools(
    search_tool=None,
    store: MemoryStore | None | None = None,
    llm_client: OllamaClient | None | None = None,
    embed_fn: Callable | None | None = None,
) -> list[dict]:
    """Return tool definitions for the Ollama tools API.

    Each entry is {"type": "function", "function": {name, description, parameters}}.
    The returned tools cover memory writes, reads, external searches, and meta ops.
    """
    def _make_def(name, description, args_model):
        return {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": args_model.model_json_schema(),
            },
        }

    tools = [
        _make_def(
            "list_files",
            "List all files in the assistant's sandbox directory. "
            "Returns file names, paths, types, sizes. "
            "Use for: discovering what files exist, getting a full inventory.",
            ListFilesArgs,
        ),
        _make_def(
            "write_file",
            (
                "Create a new file or overwrite an existing file in the sandbox. "
                "Use for: saving notes, creating scripts, writing reports, "
                "generating code, drafting documents. "
                "Path is relative to sandbox root (e.g., 'notes/meeting.txt', "
                "'scripts/analyze.py'). "
                "Set overwrite=true to replace existing file. Auto-creates parent "
                "directories."
            ),
            WriteFileArgs,
        ),
        _make_def(
            "read_file",
            "Read the full content of a file in the sandbox by its relative path. "
            "Use when you need to examine a file's contents before editing or referencing it. "
            "Path is relative to sandbox root (e.g., 'notes/meeting.txt', 'data.csv').",
            ReadFileArgs,
        ),
        _make_def(
            "edit_file",
            "Make a surgical edit to an existing file by replacing exact text. "
            "Use for: modifying config files, fixing code, updating documents, correcting typos. "
            "Provide the exact old_text to replace and the new_text. "
            "Set replace_all=false to replace only the first occurrence.",
            EditFileArgs,
        ),
        _make_def(
            "delete_file",
            "Delete a file from the sandbox. Also removes the associated memory frame. "
            "Use when a file is no longer needed. Path is relative to sandbox root.",
            DeleteFileArgs,
        ),
        _make_def(
            "glob",
            "Find files matching a glob pattern in the sandbox. "
            "Use for: discovering files, listing directory contents, finding files by extension. "
            "Patterns: '*.txt' (all txt files), 'notes/*.md' (md files in notes/), "
            "'**/*.py' (all Python files recursively).",
            GlobArgs,
        ),
        _make_def(
            "upsert_slot",
            "Store or update a fact in memory. Creates frame if missing.",
            UpsertSlotArgs,
        ),
        _make_def(
            "upsert_association",
            "Create or strengthen a typed relation between two frames.",
            UpsertAssociationArgs,
        ),
        _make_def(
            "mark_essential",
            "Protect a frame/slot from garbage collection (e.g., user identity, critical facts).",
            MarkEssentialArgs,
        ),
        _make_def(
            "recall",
            "Semantic memory lookup. Returns frames/slots matching "
            "query via embedding similarity + graph walk.",
            RecallArgs,
        ),
        _make_def(
            "search_episodes",
            "Search past conversation turns semantically (episode_embeddings).",
            SearchEpisodesArgs,
        ),
        _make_def(
            "web_search",
            "Search the web via configured backend (SearXNG or Brave). "
            "Returns structured results with URLs.",
            WebSearchArgs,
        ),
        _make_def(
            "fetch_url",
            "Fetch and extract text from a URL. Auto-extracts facts into memory.",
            FetchUrlArgs,
        ),
        _make_def(
            "read_file",
            "Read the content of an uploaded file by frame ID or frame name. "
            "Returns the full file content stored on disk.",
            ReadFileArgs,
        ),
        _make_def(
            "run_scheduled_task",
            "Execute a scheduled task by name immediately (run_now).",
            RunScheduledTaskArgs,
        ),
        _make_def(
            "compute",
            "Execute mathematical computation via dedicated math model. "
            "Use for: financial models (NPV, IRR), statistics, calculus, "
            "linear algebra, optimization, unit conversions, dimensional analysis.",
            ComputeArgs,
        ),
        _make_def(
            "plan",
            "Create a multi-step plan for complex tasks. "
            "Returns step list for orchestrator to execute.",
            PlanArgs,
        ),
        _make_def(
            "think",
            "Internal reasoning step. No external effect. "
            "Use to decompose problems, weigh evidence, self-correct.",
            ThinkArgs,
        ),
        _make_def(
            "finalize",
            "Signal completion. Return final answer to user. Ends the loop.",
            FinalizeArgs,
        ),
    ]

    # Gate web_search on search_tool being enabled
    if search_tool is not None and getattr(search_tool, "enabled", False):
        pass
    else:
        tools = [t for t in tools if t["function"]["name"] != "web_search"]

    # Gate compute on math_model being configured and supporting tools
    if (
        llm_client is None
        or not getattr(llm_client, "math_model", None)
        or not llm_client.math_model
    ):
        tools = [t for t in tools if t["function"]["name"] != "compute"]
    else:
        # Check if math_model supports tools (async check - we can't do it here synchronously)
        # The tool will just fail gracefully at runtime if the model doesn't support tools
        pass

    return tools


# ---------------------------------------------------------------------------
# Run the function-calling memory loop
# ---------------------------------------------------------------------------

async def run_tool_loop(
    llm_client: OllamaClient,
    messages: list[dict[str, str]],
    tools: list[dict],
    user_id: str = "",
    session_id: str = "",
    max_rounds: int | None = None,
    reasoning_effort: str = "medium",
    stream: bool = False,
    think: bool = False,
    num_predict: int | None = None,
    model: str | None = None,
) -> dict[str, object]:
    """Main function-calling memory loop.

    Runs until LLM calls finalize() or max_turns reached.
    Returns dict with answer, loop_terminated, memory_updated, turns,
    reasoning_effort.

    Uses the dedicated tools_model (1.5B) by default for faster tool calling.
    Uses async parallel execution for independent tool calls.
    """
    import logging

    from assistant.backend.pipeline.async_tools import (
        ToolCall as AsyncToolCall,
    )
    from assistant.backend.pipeline.async_tools import (
        execute_tools_parallel,
    )

    logger = logging.getLogger(__name__)

    tool_names = [t["function"]["name"] for t in tools]
    logger.info("DEBUG run_tool_loop: Starting with tools: %s", tool_names)
    logger.debug("DEBUG run_tool_loop: Full tool definitions: %s", tools)

    # Define turn limits inline
    max_reasoning_turns: dict[str, int] = {
        "none": 0,
        "low": 1,
        "medium": 3,
        "high": 5,
    }
    reasoning_temperature: dict[str, float] = {
        "none": 0.7,
        "low": 0.5,
        "medium": 0.3,
        "high": 0.2,
    }

    # Determine max turns
    if max_rounds is not None:
        max_turns = max_rounds
    elif reasoning_effort not in max_reasoning_turns:
        reasoning_effort = "medium"
        max_turns = max_reasoning_turns[reasoning_effort]
    else:
        max_turns = max_reasoning_turns[reasoning_effort]

    temperature = reasoning_temperature[reasoning_effort]

    # Initialize loop state
    turn = 0
    tool_results: list[dict] = []
    reasoning_trace: list[str] = []

    # Use tools_model (1.5B) by default for faster tool calling
    # Fall back to utility_model if tools_model is not available (e.g., in tests)
    loop_model = model or getattr(llm_client, "tools_model", None) or settings.utility_model

    # Main loop
    while turn < max_turns:
        turn += 1

        logger.info(
            "DEBUG run_tool_loop: Turn %d, calling LLM (%s) with %d messages, "
            "think=%s, num_predict=%s",
            turn,
            loop_model,
            len(messages),
            think,
            num_predict,
        )
        response = await llm_client.chat(
            messages=messages,
            tools=tools,
            tool_choice="auto",
            model=loop_model,
            temperature=temperature,
            think=think,
            num_predict=num_predict,
        )

        logger.info("DEBUG run_tool_loop: LLM response: tool_calls=%s, content=%s", 
            bool(response.tool_calls), response.content[:100] if response.content else "None")

        # Check for tool calls
        if response.tool_calls:
            # Convert to async tool calls
            async_calls = [
                AsyncToolCall(name=call.name, arguments=call.arguments or {})
                for call in response.tool_calls
            ]

            # Execute tools in parallel where possible
            tool_results_list = await execute_tools_parallel(
                async_calls,
                user_id=user_id,
                session_id=session_id,
            )

            # Process results in order
            for call, result in zip(response.tool_calls, tool_results_list, strict=True):
                tool_name = call.name
                raw_args = call.arguments or {}

                logger.info("DEBUG run_tool_loop: Tool %s result: success=%s, data=%s, error=%s",
                    tool_name,
                    result.success,
                    str(result.data)[:200],
                    result.error)

                # finalize ends the loop immediately
                if tool_name == "finalize":
                    answer = raw_args.get(
                        "answer", "Thank you for the information."
                    )
                    return {
                        "answer": answer,
                        "loop_terminated": "finalize",
                        "memory_updated": True,
                        "turns": turn,
                        "reasoning_effort": reasoning_effort,
                        "reasoning_trace": (
                            "\n\n".join(reasoning_trace) if reasoning_trace else None
                        ),
                    }

                # Record tool result for next iteration
                tool_results.append(
                    {
                        "call": {"name": tool_name, "args": raw_args},
                        "result": {
                            "success": result.success,
                            "data": result.data,
                            "error": result.error,
                        },
                        "metadata": result.metadata or {},
                    }
                )

                # Record think() reasoning
                if tool_name == "think":
                    reasoning_trace.append(
                        raw_args.get("reasoning", ""))

                # Add tool result to messages for next iteration
                messages.append(
                    ChatMessage(role="tool", content=str(result.data), name=tool_name)
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
                "reasoning_trace": (
                    "\n\n".join(reasoning_trace) if reasoning_trace else None
                ),
            }

    # Max turns reached - force finalize
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
        "reasoning_trace": (
            "\n\n".join(reasoning_trace) if reasoning_trace else None
        ),
    }