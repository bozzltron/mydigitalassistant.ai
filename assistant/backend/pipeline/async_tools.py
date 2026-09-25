"""Async tool execution with dependency detection.

Parallelizes independent tool calls while preserving order for dependent calls.
"""

import asyncio
import logging
from dataclasses import dataclass

from assistant.backend.pipeline.tool_executor import execute_tool

logger = logging.getLogger(__name__)


@dataclass
class ToolCall:
    """A tool call from the model."""
    name: str
    arguments: dict


@dataclass
class ToolResult:
    """Result of a tool execution."""
    tool_name: str
    success: bool
    data: dict
    error: str
    metadata: dict | None = None


def format_tool_result(result: ToolResult) -> str:
    """Render a tool result for the model to consume.

    Failures must be visible to the loop model so it can recover (e.g. pivot
    to ``list_files`` after a missing ``read_file`` path) instead of blindly
    repeating the same call. Hides success noise (None/empty data).
    """
    if result.error:
        return f"ERROR: {result.error}"
    if result.data is None or result.data == "":
        return "OK (no data)"
    return str(result.data)


# Tool dependency rules: tool A after tool B means A depends on B
# This is a simple heuristic based on tool types
TOOL_DEPENDENCIES: dict[str, list[str]] = {
    # read_file depends on glob/list_files (need to find path first)
    "read_file": ["glob", "list_files"],
    # edit_file depends on read_file (need to see content first)
    "edit_file": ["read_file", "glob", "list_files"],
    # delete_file depends on glob/list_files (need to find path)
    "delete_file": ["glob", "list_files"],
    # write_file can depend on glob if creating in specific dir
    "write_file": ["glob", "list_files"],
    # fetch_url depends on web_search (need URL first)
    "fetch_url": ["web_search"],
    # compute can depend on recall (need data first)
    "compute": ["recall", "search_episodes"],
    # run_scheduled_task depends on knowing task name (from list/recall)
    "run_scheduled_task": ["recall", "list_files"],
}

# Tools that are typically independent (can run in parallel)
INDEPENDENT_TOOL_TYPES = {
    "read_file", "glob", "list_files", "recall", "search_episodes",
    "web_search", "fetch_url", "compute", "think", "upsert_slot",
    "upsert_association", "mark_essential",
}


def find_independent_groups(tool_calls: list[ToolCall]) -> list[list[ToolCall]]:
    """Group tool calls into independent batches for parallel execution.

    Returns a list of groups, where each group can be executed in parallel.
    Groups are executed sequentially (group 1, then group 2, etc.).

    Dependency rules:
    - read_file after glob/list_files -> depends
    - edit_file after read_file -> depends
    - Multiple read_file -> independent
    - recall + glob -> independent
    """
    if not tool_calls:
        return []

    # Build dependency graph
    n = len(tool_calls)
    depends_on: list[set[int]] = [set() for _ in range(n)]

    for i, call_i in enumerate(tool_calls):
        for j, call_j in enumerate(tool_calls):
            if i == j:
                continue
            # Check if call_i depends on call_j
            if _depends_on(call_i, call_j):
                depends_on[i].add(j)

    # Topological sort with level assignment (Kahn's algorithm variant)
    # Each level = independent group that can run in parallel
    levels: list[list[int]] = []
    remaining = set(range(n))
    current_level: list[int] = []

    while remaining:
        # Find nodes with no unmet dependencies
        # If levels is empty, all dependencies are met (no previous levels)
        # Otherwise, check if all dependencies are in previous levels
        completed = set().union(*levels) if levels else set()
        ready = [
            i for i in remaining
            if depends_on[i].issubset(completed)
        ]

        if not ready:
            # Cycle or missing dependency - run remaining sequentially
            ready = list(remaining)

        current_level = ready
        levels.append(current_level)
        remaining -= set(current_level)

    # Convert indices back to ToolCall objects
    return [[tool_calls[i] for i in level] for level in levels]


def _depends_on(call_a: ToolCall, call_b: ToolCall) -> bool:
    """Check if tool call A depends on tool call B."""
    deps = TOOL_DEPENDENCIES.get(call_a.name, [])
    if call_b.name in deps:
        # Additional check: for read_file/edit_file, check if they reference same path
        read_edit_delete = ("read_file", "edit_file", "delete_file")
        glob_list = ("glob", "list_files")
        if call_a.name in read_edit_delete and call_b.name in glob_list:
            # If glob pattern could match the file path, consider it a dependency
            path_a = call_a.arguments.get("path", "")
            pattern_b = call_b.arguments.get("pattern", "")
            if pattern_b and path_a:
                # Simple check: if the glob pattern could match the path
                if _pattern_matches(pattern_b, path_a):
                    return True
        elif call_a.name == "edit_file" and call_b.name == "read_file":
            # edit_file after read_file on same path
            path_a = call_a.arguments.get("path", "")
            path_b = call_b.arguments.get("path", "")
            if path_a and path_b and path_a == path_b:
                return True
        elif call_a.name == "fetch_url" and call_b.name == "web_search":
            # fetch_url after web_search - always depends if web_search was called
            return True
        else:
            return True
    return False


def _pattern_matches(pattern: str, path: str) -> bool:
    """Simple glob pattern matching."""
    import fnmatch
    return fnmatch.fnmatch(path, pattern)


async def execute_tools_parallel(
    tool_calls: list[ToolCall],
    user_id: str = "",
    session_id: str = "",
) -> list[ToolResult]:
    """Execute tool calls in parallel where possible, respecting dependencies.

    Returns list of ToolResult in the same order as input tool_calls.
    """
    if not tool_calls:
        return []

    groups = find_independent_groups(tool_calls)
    results: list[ToolResult | None] = [None] * len(tool_calls)

    # Map tool_call to its index for result placement
    call_to_index = {id(call): i for i, call in enumerate(tool_calls)}

    for group in groups:
        # Execute all calls in this group in parallel
        async def execute_one(call: ToolCall) -> tuple[int, ToolResult]:
            idx = call_to_index[id(call)]
            try:
                result = await execute_tool(
                    call.name,
                    call.arguments,
                    user_id=user_id,
                    session_id=session_id,
                )
                return idx, ToolResult(
                    tool_name=call.name,
                    success=getattr(result, "success", True),
                    data=getattr(result, "data", {}),
                    error=getattr(result, "error", ""),
                    metadata=getattr(result, "metadata", None),
                )
            except Exception as e:
                logger.error(f"Tool execution error for {call.name}: {e}")
                return idx, ToolResult(
                    tool_name=call.name,
                    success=False,
                    data={},
                    error=str(e),
                )

        # Run all in parallel
        group_results = await asyncio.gather(*[execute_one(call) for call in group])

        # Place results in correct positions
        for idx, result in group_results:
            results[idx] = result

    return results  # type: ignore