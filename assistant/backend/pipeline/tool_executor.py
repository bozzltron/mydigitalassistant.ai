# Tool execution dispatcher
# Dispatches tool calls to implementations, validates args, handles timeouts

from __future__ import annotations

import asyncio
import logging
import time

from pydantic import ValidationError

from assistant.backend.memory.store import MemoryStore

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# MemoryStore instance - will be injected or created from db_path
# ---------------------------------------------------------------------------

# Global store instance (set during initialization)
_store: MemoryStore | None = None


def init_store(db_path: str) -> None:
    """Initialize the global MemoryStore instance."""
    global _store
    _store = MemoryStore(db_path)
    _register_builtin_tools()


def _register_builtin_tools() -> None:
    """Register all builtin tools with their executors."""
    from assistant.backend.pipeline.tools import (
        ComputeArgs,
        FetchUrlArgs,
        FinalizeArgs,
        MarkEssentialArgs,
        PlanArgs,
        ReadFileArgs,
        RecallArgs,
        RunScheduledTaskArgs,
        SearchEpisodesArgs,
        ThinkArgs,
        UpsertAssociationArgs,
        UpsertSlotArgs,
        WebSearchArgs,
    )

    register_tool("upsert_slot", UpsertSlotArgs, execute_upsert_slot)
    register_tool("upsert_association", UpsertAssociationArgs, execute_upsert_association)
    register_tool("mark_essential", MarkEssentialArgs, execute_mark_essential)
    register_tool("recall", RecallArgs, execute_recall)
    register_tool("get_frame", UpsertSlotArgs, execute_get_frame)  # frame_name only
    register_tool(
        "get_slot_history", UpsertSlotArgs, execute_get_slot_history
    )  # frame_name + slot_key
    register_tool("search_episodes", SearchEpisodesArgs, execute_search_episodes)
    register_tool("web_search", WebSearchArgs, execute_web_search)
    register_tool("fetch_url", FetchUrlArgs, execute_fetch_url)
    register_tool("read_file", ReadFileArgs, execute_read_file)
    register_tool("run_scheduled_task", RunScheduledTaskArgs, execute_run_scheduled_task)
    register_tool("compute", ComputeArgs, execute_compute)
    register_tool("plan", PlanArgs, execute_plan)
    register_tool("think", ThinkArgs, execute_think)
    register_tool("finalize", FinalizeArgs, execute_finalize)


# ---------------------------------------------------------------------------
# Tool result shape
# ---------------------------------------------------------------------------

class ToolResult:
    """Result from tool execution."""

    def __init__(
        self,
        success: bool = True,
        data: dict[str, object] | None = None,
        error: str | None = None,
        metadata: dict[str, object] | None = None,
    ):
        self.success = success
        self.data = data or {}
        self.error = error
        self.metadata = metadata or {}


# ---------------------------------------------------------------------------
# Tool timeout configuration
# ---------------------------------------------------------------------------

TOOL_TIMEOUTS: dict[str, float] = {
    "web_search": 30.0,
    "fetch_url": 30.0,
    "read_file": 10.0,
    "run_scheduled_task": 60.0,
    "compute": 60.0,
    "upsert_slot": 10.0,
    "upsert_association": 10.0,
    "mark_essential": 10.0,
    "recall": 10.0,
    "get_frame": 10.0,
    "get_slot_history": 10.0,
    "search_episodes": 10.0,
    "plan": 30.0,
    "think": 30.0,
    "finalize": 30.0,
}


# ---------------------------------------------------------------------------
# Tool registry: maps tool name to (args_class, executor_func)
# ---------------------------------------------------------------------------

TOOL_REGISTRY: dict[str, dict[str, object]] = {}


def register_tool(
    name: str,
    args_class,
    executor_func,
    timeout: float | None = None,
) -> None:
    """Register a tool with its args class and executor."""
    TOOL_REGISTRY[name] = {
        "args_class": args_class,
        "func": executor_func,
        "timeout": timeout or TOOL_TIMEOUTS.get(name, 30.0),
    }


# ---------------------------------------------------------------------------
# Argument validation
# ---------------------------------------------------------------------------

def validate_args(tool_name: str, raw_args: dict) -> dict:
    """Validate raw dict args against the tool's args class."""
    if tool_name not in TOOL_REGISTRY:
        logger.warning(f"Unknown tool: {tool_name}")
        return raw_args

    schema_info = TOOL_REGISTRY[tool_name]
    args_class = schema_info["args_class"]

    try:
        validated = args_class(**raw_args)
        return validated.model_dump()
    except ValidationError as e:
        logger.warning(f"Args validation failed for {tool_name}: {e}")
        return raw_args


# ---------------------------------------------------------------------------
# Individual tool executors
# ---------------------------------------------------------------------------


async def execute_upsert_slot(args: dict, user_id: str, session_id: str) -> ToolResult:
    """Store or update a fact in memory. Creates frame if missing."""
    if _store is None:
        return ToolResult(success=False, error="MemoryStore not initialized")

    try:
        frame_name = args.get("frame_name", "")
        slot_key = args.get("slot_key", "")
        slot_value = args.get("slot_value", "")
        essential = args.get("essential", False)
        priority = args.get("priority", 0)
        source_type = args.get("source_type", "conversation")
        source_episode_id = args.get("source_episode_id")

        # Convert frame_name to frame_id via get_frame_by_name
        frame = await _store.get_frame_by_name(frame_name)
        if frame is None:
            # Create new frame with user_id as owner
            try:
                user_id_int = int(user_id)
            except ValueError:
                user_id_int = 1  # default
            frame = await _store.create_frame(
                frame_name,
                "entity",
                owner_user_id=user_id_int,
                source_type=source_type,
                source_reliability=0.7,
            )
            frame_id = frame.id
        else:
            frame_id = frame.id

        # Call MemoryStore.upsert_slot with correct signature
        result = await _store.upsert_slot(
            frame_id=frame_id,
            key=slot_key,
            value=slot_value,
            source_episode_id=hash(session_id) % (2**31) if source_episode_id is None else None,
            essential=1 if essential else 0,
            priority=priority,
            source_type=source_type,
        )

        # result is a (Slot, Conflict | None) tuple
        new_slot = result[0]
        new_confidence = new_slot.confidence if new_slot else 0.5
        had_conflict = result[1] is not None

        return ToolResult(
            success=True,
            data={
                "frame_id": frame_id,
                "slot_key": slot_key,
                "new_value": slot_value,
                "confidence": new_confidence,
                "conflict": had_conflict,
            },
        )
    except Exception as e:
        logger.error(f"upsert_slot failed: {e}", exc_info=True)
        return ToolResult(success=False, error=str(e))


async def execute_upsert_association(args: dict, user_id: str) -> ToolResult:
    """Create or strengthen a typed relation between two frames."""
    if _store is None:
        return ToolResult(success=False, error="MemoryStore not initialized")

    try:
        source_frame = args.get("source_frame", "")
        target_frame = args.get("target_frame", "")
        relation_type = args.get("relation_type", "")
        confidence = args.get("confidence", 0.5)
        bidirectional = args.get("bidirectional", False)

        await _store.create_association(
            source_frame,
            target_frame,
            relation_type,
            confidence=confidence,
        )

        if bidirectional:
            await _store.create_association(
                target_frame,
                source_frame,
                relation_type,
                confidence=confidence,
            )

        return ToolResult(
            success=True,
            data={"source_frame": source_frame, "target_frame": target_frame},
        )
    except Exception as e:
        logger.error(f"upsert_association failed: {e}", exc_info=True)
        return ToolResult(success=False, error=str(e))


async def execute_mark_essential(args: dict, user_id: str) -> ToolResult:
    """Protect a frame/slot from garbage collection."""
    if _store is None:
        return ToolResult(success=False, error="MemoryStore not initialized")

    try:
        frame_name = args.get("frame_name", "")
        essential = args.get("essential", True)

        await _store.set_frame_priority(frame_name, priority=10 if essential else 0)

        return ToolResult(success=True, data={"frame_name": frame_name, "essential": essential})
    except Exception as e:
        logger.error(f"mark_essential failed: {e}", exc_info=True)
        return ToolResult(success=False, error=str(e))


# ---------------------------------------------------------------------------
# Memory read executors
# ---------------------------------------------------------------------------

async def execute_recall(args: dict, user_id: str, session_id: str = "") -> ToolResult:
    """Semantic memory lookup via embedding + graph walk."""
    if _store is None:
        return ToolResult(success=False, error="MemoryStore not initialized")

    try:
        _ = args.get("query", "")

        # Need to embed the query first - use a simple approach
        # For now, return empty results as embedding requires LLM client
        return ToolResult(success=True, data={"results": []})
    except Exception as e:
        logger.error(f"recall failed: {e}", exc_info=True)
        return ToolResult(success=False, error=str(e))


async def execute_get_frame(args: dict, user_id: str) -> ToolResult:
    """Retrieve full frame with all slots and associations."""
    if _store is None:
        return ToolResult(success=False, error="MemoryStore not initialized")

    try:
        frame_name = args.get("frame_name", "")
        frame = await _store.get_frame_by_name(frame_name)

        if frame is None:
            return ToolResult(success=False, error=f"Frame '{frame_name}' not found")

        # Convert to dict
        slots_dict = {
            slot.key: {"value": slot.value, "confidence": slot.confidence}
            for slot in frame.slots
        }

        associations = []
        for assoc in frame.associations:
            associations.append(
                {
                    "source": assoc.source_frame,
                    "target": assoc.target_frame,
                    "relation": assoc.relation_type,
                    "confidence": assoc.confidence,
                }
            )

        return ToolResult(
            success=True,
            data={"frame": {"name": frame.name, "slots": slots_dict, "associations": associations}},
        )
    except Exception as e:
        logger.error(f"get_frame failed: {e}", exc_info=True)
        return ToolResult(success=False, error=str(e))


async def execute_get_slot_history(args: dict, user_id: str) -> ToolResult:
    """Audit trail for a slot."""
    if _store is None:
        return ToolResult(success=False, error="MemoryStore not initialized")

    try:
        slot_key = args.get("slot_key", "")

        history = (
            await _store.get_slot_history(slot_key)
            if hasattr(_store, "get_slot_history")
            else []
        )

        return ToolResult(success=True, data={"history": history})
    except Exception as e:
        logger.error(f"get_slot_history failed: {e}", exc_info=True)
        return ToolResult(success=False, error=str(e))


async def execute_search_episodes(args: dict, user_id: str) -> ToolResult:
    """Search past conversation turns semantically."""
    if _store is None:
        return ToolResult(success=False, error="MemoryStore not initialized")

    try:
        query = args.get("query", "")
        session_id = args.get("session_id")
        max_results = args.get("max_results", 5)

        # Use the existing search_episodes from consolidation
        from assistant.backend.memory.consolidate import search_episodes as ce_search

        results = await ce_search(query, session_id=session_id, max_results=max_results)

        return ToolResult(success=True, data={"results": results})
    except Exception as e:
        logger.error(f"search_episodes failed: {e}", exc_info=True)
        return ToolResult(success=False, error=str(e))


# ---------------------------------------------------------------------------
# External tool executors
# ---------------------------------------------------------------------------

async def execute_web_search(args: dict, user_id: str) -> ToolResult:
    """Search the web via configured backend."""
    try:
        from assistant.backend.pipeline.search import search_with_info

        query = args.get("query", "")
        num_results = args.get("num_results", 5)

        results, info = await search_with_info(query, num_results=num_results)

        return ToolResult(success=True, data={"results": results, "search_info": info})
    except Exception as e:
        logger.error(f"web_search failed: {e}", exc_info=True)
        return ToolResult(success=False, error=str(e))


async def execute_fetch_url(args: dict, user_id: str) -> ToolResult:
    """Fetch and extract text from URL. Auto-extracts facts."""
    try:
        from assistant.backend.pipeline.fetch import fetch_and_extract

        url = args.get("url", "")
        extract_facts = args.get("extract_facts", True)

        result = await fetch_and_extract(url, extract_facts=extract_facts)

        return ToolResult(success=True, data=result)
    except Exception as e:
        logger.error(f"fetch_url failed: {e}", exc_info=True)
        return ToolResult(success=False, error=str(e))


async def execute_read_file(args: dict, user_id: str, session_id: str) -> ToolResult:
    """Read the content of an uploaded file by frame ID or frame name."""
    if _store is None:
        return ToolResult(success=False, error="MemoryStore not initialized")

    try:
        frame_id = args.get("frame_id")
        frame_name = args.get("frame_name")

        if not frame_id and not frame_name:
            return ToolResult(success=False, error="Either frame_id or frame_name must be provided")

        # Get the frame
        if frame_id:
            frame = await _store.get_frame(frame_id)
        else:
            frame = await _store.get_frame_by_name(frame_name)

        if frame is None:
            return ToolResult(success=False, error="File frame not found")

        # Get slots to find file_safe_name
        slots = await _store.get_slots_for_frame(frame.id)
        slots_dict = {slot.key: slot.value for slot in slots}

        file_safe_name = slots_dict.get("file_safe_name")
        file_name = slots_dict.get("file_name", "unknown")
        file_ext = slots_dict.get("file_ext", "")

        if not file_safe_name:
            return ToolResult(
                success=False,
                error="File not found on disk (missing file_safe_name slot)",
            )

        # Read file from disk
        from pathlib import Path
        data_dir = Path("/app/data")
        file_path = data_dir / file_safe_name

        if not file_path.exists():
            return ToolResult(success=False, error=f"File not found on disk: {file_safe_name}")

        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            return ToolResult(success=False, error=f"Failed to read file: {e}")

        return ToolResult(
            success=True,
            data={
                "frame_id": frame.id,
                "frame_name": frame.name,
                "file_name": file_name,
                "file_ext": file_ext,
                "content": content,
                "size": len(content),
            },
        )
    except Exception as e:
        logger.error(f"read_file failed: {e}", exc_info=True)
        return ToolResult(success=False, error=str(e))


async def execute_run_scheduled_task(args: dict, user_id: str) -> ToolResult:
    """Execute a scheduled task immediately (run_now)."""
    try:
        from assistant.backend.scheduler import run_now as scheduler_run_now

        task_name = args.get("task_name", "")
        result = await scheduler_run_now(task_name, user_id=user_id)

        return ToolResult(success=True, data=result)
    except Exception as e:
        logger.error(f"run_scheduled_task failed: {e}", exc_info=True)
        return ToolResult(success=False, error=str(e))


async def execute_compute(args: dict, user_id: str) -> ToolResult:
    """Execute mathematical computation via dedicated math model."""
    try:
        from assistant.backend.config import settings
        from assistant.backend.pipeline.llm_client import OllamaClient

        expression = args.get("expression", "")
        context = args.get("context")
        precision = args.get("precision", 4)

        # Build the code to execute
        code = expression
        if context:
            # Add context variables
            var_assignments = "\n".join(f"{k} = {v}" for k, v in context.items())
            code = f"{var_assignments}\n\n# Compute:\n{expression}"

        # Get LLM client
        llm_client = OllamaClient(
            base_url=settings.ollama_url,
            chat_model=settings.chat_model,
            utility_model=settings.utility_model,
            embedding_model=settings.embedding_model,
            math_model=settings.math_model,
            math_num_ctx=settings.math_num_ctx,
            math_keep_alive=settings.math_keep_alive,
            timeout=settings.ollama_timeout,
            chat_num_ctx=settings.chat_num_ctx,
            utility_num_ctx=settings.utility_num_ctx,
            keep_alive=settings.ollama_keep_alive,
        )

        try:
            result = await llm_client.execute_python(code, timeout=30)
        finally:
            await llm_client.close()

        # Format result with precision
        try:
            # Try to extract a numeric result and format it
            import re
            numbers = re.findall(r'[-+]?\d*\.\d+|\d+', result)
            if numbers:
                # Take the last number as the result
                value = float(numbers[-1])
                formatted = f"{value:.{precision}f}"
                result = (
                    f"{result}\n\n**Result (formatted to {precision} "
                    f"decimal places):** {formatted}"
                )
        except Exception:
            pass

        return ToolResult(success=True, data={"result": result, "expression": expression})
    except Exception as e:
        logger.error(f"compute failed: {e}", exc_info=True)
        return ToolResult(success=False, error=str(e))


# ---------------------------------------------------------------------------
# Meta tool executors
# ---------------------------------------------------------------------------

async def execute_plan(args: dict, user_id: str) -> ToolResult:
    """Return a structured plan step list for orchestrator to execute."""
    try:
        goal = args.get("goal", "")
        steps = args.get("steps", [])

        return ToolResult(success=True, data={"goal": goal, "steps": steps})
    except Exception as e:
        logger.error(f"plan failed: {e}", exc_info=True)
        return ToolResult(success=False, error=str(e))


async def execute_think(args: dict, user_id: str) -> ToolResult:
    """Internal reasoning step. No external effect. Records to trace."""
    try:
        reasoning = args.get("reasoning", "")

        return ToolResult(
            success=True,
            data={"ack": "reasoning recorded", "reasoning": reasoning},
        )
    except Exception as e:
        logger.error(f"think failed: {e}", exc_info=True)
        return ToolResult(success=False, error=str(e))


async def execute_finalize(args: dict, user_id: str) -> ToolResult:
    """Signal completion. Return final answer to user. Ends the loop."""
    # finalize is handled in the loop, not here
    try:
        answer = args.get("answer", "Thank you.")
        return ToolResult(success=True, data={"answer": answer})
    except Exception as e:
        logger.error(f"finalize failed: {e}", exc_info=True)
        return ToolResult(success=False, error=str(e))


# ---------------------------------------------------------------------------
# Main dispatcher
# ---------------------------------------------------------------------------

async def execute_tool(
    tool_name: str,
    raw_args: dict,
    user_id: str,
    session_id: str,
) -> ToolResult:
    """
    Dispatch a tool call to the appropriate executor with timeout + validation.

    Args:
        tool_name: Name of the tool to execute
        raw_args: Raw dict of arguments from LLM
        user_id: User identifier for memory scoping
        session_id: Session identifier

    Returns:
        ToolResult with success/data/error metadata
    """
    start = time.time()

    # 1. Validate args
    validated = validate_args(tool_name, raw_args)

    # 2. Look up executor
    if tool_name not in TOOL_REGISTRY:
        return ToolResult(success=False, error=f"Unknown tool: {tool_name}")

    schema_info = TOOL_REGISTRY[tool_name]
    func = schema_info["func"]
    timeout = schema_info["timeout"]

    # 3. Execute with timeout
    try:
        result = await asyncio.wait_for(
            func(validated, user_id, session_id),
            timeout=timeout,
        )
    except TimeoutError:
        logger.warning(f"Tool {tool_name} timed out after {timeout}s")
        return ToolResult(success=False, error=f"Tool timeout after {timeout}s")
    except Exception as e:
        logger.error(f"Tool {tool_name} error: {e}", exc_info=True)
        return ToolResult(success=False, error=str(e))

    # 4. Attach latency metadata
    result.metadata = getattr(result, "metadata", {})
    result.metadata["latency_ms"] = int((time.time() - start) * 1000)
    result.metadata["tool"] = tool_name

    return result