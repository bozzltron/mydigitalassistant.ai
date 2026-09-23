# Tool execution dispatcher
# Dispatches tool calls to implementations, validates args, handles timeouts

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable

from pydantic import ValidationError

from assistant.backend.memory.store import MemoryStore

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# MemoryStore instance - will be injected or created from db_path
# ---------------------------------------------------------------------------

# Global store instance (set during initialization)
_store: MemoryStore | None = None
# Global embed function for recall tool
_embed_fn: Callable | None = None


def init_store(db_path: str, embed_fn: Callable | None = None) -> None:
    """Initialize the global MemoryStore instance and embed function."""
    global _store, _embed_fn
    _store = MemoryStore(db_path)
    _embed_fn = embed_fn
    _register_builtin_tools()


def _register_builtin_tools() -> None:
    """Register all builtin tools with their executors."""
    from assistant.backend.pipeline.tools import (
        ComputeArgs,
        DeleteFileArgs,
        EditFileArgs,
        FetchUrlArgs,
        FinalizeArgs,
        GlobArgs,
        ListFilesArgs,
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
        WriteFileArgs,
    )

    register_tool("list_files", ListFilesArgs, execute_list_files)
    register_tool("write_file", WriteFileArgs, execute_write_file)
    register_tool("read_file", ReadFileArgs, execute_read_file)
    register_tool("edit_file", EditFileArgs, execute_edit_file)
    register_tool("delete_file", DeleteFileArgs, execute_delete_file)
    register_tool("glob", GlobArgs, execute_glob)
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
    "write_file": 10.0,
    "edit_file": 10.0,
    "delete_file": 10.0,
    "glob": 10.0,
    "list_files": 10.0,
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
    if _embed_fn is None:
        # Gracefully handle missing embed function (e.g., in tests)
        return ToolResult(success=True, data={"results": [], "count": 0})

    try:
        query = args.get("query", "")
        if not query:
            return ToolResult(success=False, error="query required")

        frame_types = args.get("frame_types")
        max_results = args.get("max_results", 10)
        min_confidence = args.get("min_confidence", 0.3)
        include_associations = args.get("include_associations", True)

        # Embed the query
        embedding_resp = await _embed_fn(query)
        if hasattr(embedding_resp, "embedding"):
            embedding = embedding_resp.embedding
        else:
            embedding = embedding_resp

        # Search frames via sqlite-vec
        results = await _store.search_similar_frames(
            embedding=embedding,
            user_id=int(user_id),
            limit=max_results,
            min_distance=1.0 - min_confidence,
        )

        # Format results
        formatted = []
        for frame, slots, similarity in results:
            if frame_types and not any(frame.name.startswith(ft) for ft in frame_types):
                continue

            slot_data = {s.key: {"value": s.value, "confidence": s.confidence} for s in slots}

            assoc_data = []
            if include_associations:
                associations = await _store.get_all_associations_for_frame(frame.id)
                for assoc in associations:
                    assoc_data.append({
                        "source": assoc.source_frame,
                        "target": assoc.target_frame,
                        "relation": assoc.relation_type,
                        "confidence": assoc.confidence
                    })

            formatted.append({
                "frame_id": frame.id,
                "frame_name": frame.name,
                "frame_type": frame.type,
                "confidence": frame.confidence,
                "similarity": similarity,
                "slots": slot_data,
                "associations": assoc_data
            })

        return ToolResult(success=True, data={"results": formatted, "count": len(formatted)})
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
    """Read file by sandbox path or uploaded file by frame_id/frame_name."""
    try:
        from pathlib import Path

        from assistant.backend.pipeline.filesystem import (
            PathTraversalError,
            SizeLimitError,
            read_sandbox_file,
        )

        # Check if reading uploaded file by frame_id or frame_name
        frame_id = args.get("frame_id")
        frame_name = args.get("frame_name")
        path = args.get("path", "")

        if frame_id is not None or frame_name is not None:
            if _store is None:
                return ToolResult(success=False, error="MemoryStore not initialized")

            if frame_id is not None:
                frame = await _store.get_frame(frame_id)
            else:
                frame = await _store.get_frame_by_name(frame_name)

            if frame is None:
                return ToolResult(success=False, error=f"Frame not found: {frame_id or frame_name}")

            # Check ownership
            if frame.owner_user_id is not None and frame.owner_user_id != int(user_id):
                return ToolResult(
                    success=False,
                    error="Access denied: file belongs to another user"
                )

            # Get file content from frame slots
            slots = await _store.get_slots_for_frame(frame.id)
            slots_dict = {slot.key: slot.value for slot in slots}

            file_name = slots_dict.get("file_name", "unknown")
            file_ext = slots_dict.get("file_ext", "")
            file_safe_name = slots_dict.get("file_safe_name", "")

            content = ""

            # First, try to read full content from sandbox file (if we have a safe_name)
            if file_safe_name:
                try:
                    from assistant.backend.pipeline.filesystem import read_sandbox_file
                    content = read_sandbox_file(file_safe_name)
                except FileNotFoundError:
                    pass  # File not in sandbox, try memory slots
                except Exception as e:
                    logger.warning(f"Failed to read sandbox file {file_safe_name}: {e}")

            # If no sandbox content, try memory slots
            if not content:
                content = (
                    slots_dict.get("file_content")
                    or slots_dict.get("file_content_preview")
                    or ""
                )

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

        # Otherwise, read from sandbox by path
        if not path:
            return ToolResult(
                success=False,
                error="path is required (or provide frame_id/frame_name)"
            )

        content = read_sandbox_file(path)

        # Also try to find and update memory frame preview
        if _store is not None:
            frame_name = f"file_{Path(path).name}"
            frame = await _store.get_frame_by_name(frame_name)
            if frame:
                await _store.upsert_slot(
                    frame.id, "file_content_preview", content[:200], source_type="file_read"
                )

        return ToolResult(
            success=True,
            data={"path": path, "content": content, "size": len(content)},
        )
    except PathTraversalError as e:
        return ToolResult(success=False, error=str(e))
    except SizeLimitError as e:
        return ToolResult(success=False, error=str(e))
    except FileNotFoundError as e:
        return ToolResult(success=False, error=str(e))
    except Exception as e:
        logger.error(f"read_file failed: {e}", exc_info=True)
        return ToolResult(success=False, error=str(e))


async def execute_write_file(args: dict, user_id: str, session_id: str) -> ToolResult:
    """Create or overwrite a file in the sandbox."""
    try:
        import csv
        import json
        import re
        from pathlib import Path

        from assistant.backend.pipeline.filesystem import (
            PathTraversalError,
            SizeLimitError,
            write_sandbox_file,
        )

        path = args.get("path", "")
        content = args.get("content", "")
        overwrite = args.get("overwrite", False)

        if not path:
            return ToolResult(success=False, error="path is required")

        written_path = write_sandbox_file(path, content, overwrite)

        # Create/update memory frame
        if _store is not None:
            frame_name = f"file_{written_path.name}"
            frame = await _store.get_frame_by_name(frame_name)
            if not frame:
                try:
                    user_id_int = int(user_id)
                except ValueError:
                    user_id_int = 1
                frame = await _store.create_frame(
                    frame_name, "entity",
                    source_type="file_create", owner_user_id=user_id_int, source_reliability=0.8
                )

            await _store.upsert_slot(
                frame.id, "file_name", written_path.name, source_type="file_create"
            )
            await _store.upsert_slot(
                frame.id, "file_ext", written_path.suffix.lstrip("."), source_type="file_create"
            )
            await _store.upsert_slot(
                frame.id, "file_size", str(len(content)), source_type="file_create"
            )
            await _store.upsert_slot(
                frame.id, "file_safe_name", written_path.name, source_type="file_create"
            )
            await _store.upsert_slot(
                frame.id, "file_content_preview", content[:200], source_type="file_create"
            )

            # CSV special handling: create row frames
            if written_path.suffix.lower() == ".csv":
                try:
                    reader = csv.reader(content.splitlines())
                    rows = list(reader)
                    if rows:
                        headers = rows[0]
                        for i, row in enumerate(rows[1:], 1):
                            row_frame = await _store.create_frame(
                                f"file_{written_path.name}_row_{i}", "record",
                                source_type="csv_row", owner_user_id=user_id_int
                            )
                            for col, val in zip(headers, row, strict=True):
                                slot_key = re.sub(r'[^a-zA-Z0-9_]', '_', col.lower().strip())
                                if not slot_key:
                                    slot_key = f"col_{i}"
                                await _store.upsert_slot(
                                    row_frame.id, slot_key, val, source_type="csv_row"
                                )
                            await _store.create_association(frame.id, row_frame.id, "part_of")
                        await _store.upsert_slot(
                            frame.id, "row_count", str(len(rows) - 1), source_type="file_create"
                        )
                        await _store.upsert_slot(
                            frame.id, "columns", json.dumps(headers), source_type="file_create"
                        )
                except Exception as e:
                    logger.warning(f"CSV row frame creation failed: {e}")

        return ToolResult(success=True, data={
            "path": str(written_path.relative_to(Path("/app/data"))),
            "size": len(content),
            "frame_id": frame.id if _store and frame else None
        })
    except PathTraversalError as e:
        return ToolResult(success=False, error=str(e))
    except SizeLimitError as e:
        return ToolResult(success=False, error=str(e))
    except FileExistsError as e:
        return ToolResult(success=False, error=str(e))
    except Exception as e:
        logger.error(f"write_file failed: {e}", exc_info=True)
        return ToolResult(success=False, error=str(e))


async def execute_edit_file(args: dict, user_id: str, session_id: str) -> ToolResult:
    """Make a surgical edit to an existing file."""
    try:
        from pathlib import Path

        from assistant.backend.pipeline.filesystem import (
            PathTraversalError,
            SizeLimitError,
            read_sandbox_file,
            write_sandbox_file,
        )

        path = args.get("path", "")
        old_text = args.get("old_text", "")
        new_text = args.get("new_text", "")
        replace_all = args.get("replace_all", True)

        if not path:
            return ToolResult(success=False, error="path is required")
        if old_text == "":
            return ToolResult(success=False, error="old_text cannot be empty")

        # Read current content
        content = read_sandbox_file(path)

        if old_text not in content:
            return ToolResult(success=False, error="old_text not found in file")

        if replace_all:
            new_content = content.replace(old_text, new_text)
            changes = content.count(old_text)
        else:
            new_content = content.replace(old_text, new_text, 1)
            changes = 1

        if new_content == content:
            return ToolResult(success=False, error="No changes made")

        # Atomic write
        write_sandbox_file(path, new_content, overwrite=True)

        # Update memory frame
        if _store is not None:
            frame_name = f"file_{Path(path).name}"
            frame = await _store.get_frame_by_name(frame_name)
            if frame:
                await _store.upsert_slot(
                    frame.id, "file_content_preview", new_content[:200], source_type="file_edit"
                )
                await _store.upsert_slot(
                    frame.id, "file_size", str(len(new_content)), source_type="file_edit"
                )

        return ToolResult(success=True, data={
            "path": path, "changes": changes, "new_size": len(new_content)
        })
    except PathTraversalError as e:
        return ToolResult(success=False, error=str(e))
    except SizeLimitError as e:
        return ToolResult(success=False, error=str(e))
    except FileNotFoundError as e:
        return ToolResult(success=False, error=str(e))
    except Exception as e:
        logger.error(f"edit_file failed: {e}", exc_info=True)
        return ToolResult(success=False, error=str(e))


async def execute_delete_file(args: dict, user_id: str, session_id: str) -> ToolResult:
    """Delete a file from the sandbox."""
    try:
        from pathlib import Path

        from assistant.backend.pipeline.filesystem import (
            PathTraversalError,
            delete_sandbox_file,
        )

        path = args.get("path", "")
        if not path:
            return ToolResult(success=False, error="path is required")

        delete_sandbox_file(path)

        # Soft-delete memory frame and any row frames
        if _store is not None:
            frame_name = f"file_{Path(path).name}"
            frame = await _store.get_frame_by_name(frame_name)
            if frame:
                # Check for CSV row frames
                row_count_slot = await _store.get_slot(frame.id, "row_count")
                if row_count_slot and int(row_count_slot.value or "0") > 0:
                    associations = await _store.get_all_associations_for_frame(frame.id)
                    for assoc in associations:
                        if assoc.relation_type == "part_of":
                            await _store.forget_frame(assoc.to_frame_id)
                await _store.forget_frame(frame.id)

        return ToolResult(success=True, data={"path": path})
    except PathTraversalError as e:
        return ToolResult(success=False, error=str(e))
    except FileNotFoundError as e:
        return ToolResult(success=False, error=str(e))
    except Exception as e:
        logger.error(f"delete_file failed: {e}", exc_info=True)
        return ToolResult(success=False, error=str(e))


async def execute_glob(args: dict, user_id: str, session_id: str) -> ToolResult:
    """Find files matching a glob pattern in the sandbox."""
    try:
        from assistant.backend.pipeline.filesystem import PathTraversalError, list_sandbox_files

        pattern = args.get("pattern", "**/*")
        files = list_sandbox_files(pattern)

        return ToolResult(success=True, data={"files": files, "count": len(files)})
    except PathTraversalError as e:
        return ToolResult(success=False, error=str(e))
    except Exception as e:
        logger.error(f"glob failed: {e}", exc_info=True)
        return ToolResult(success=False, error=str(e))


async def execute_list_files(args: dict, user_id: str, session_id: str) -> ToolResult:
    """List all files in the sandbox, enriched with memory frame data."""
    try:
        from pathlib import Path

        from assistant.backend.pipeline.filesystem import list_sandbox_files

        # Get physical files from sandbox
        sandbox_files = list_sandbox_files("**/*")
        sandbox_by_name = {f["path"]: f for f in sandbox_files}

        files = []

        # Also get memory frames for enrichment
        if _store is not None:
            frames = await _store.list_frames(owner_user_id=int(user_id))
            file_frames = [
                f
                for f in frames
                if f.source_type in ("file_upload", "file_create") and f.priority > 0
            ]

            for frame in file_frames:
                slots = await _store.get_slots_for_frame(frame.id)
                slots_dict = {slot.key: slot.value for slot in slots}

                file_name = slots_dict.get("file_name", "unknown")
                file_safe_name = slots_dict.get("file_safe_name", "")

                # Try to find matching sandbox file
                sandbox_info = sandbox_by_name.get(file_safe_name, {})

                files.append({
                    "frame_id": frame.id,
                    "frame_name": frame.name,
                    "file_name": file_name,
                    "file_ext": slots_dict.get("file_ext", ""),
                    "file_size": slots_dict.get("file_size"),
                    "content_preview": slots_dict.get("file_content_preview", ""),
                    "created_at": frame.created_at if frame.created_at else None,
                    "path": sandbox_info.get("path"),
                    "modified": sandbox_info.get("modified"),
                })

        # Add any sandbox files that don't have memory frames (orphaned)
        # Only add orphaned files if no user_id filter (global view)
        if not user_id:
            for sf in sandbox_files:
                if not any(f.get("path") == sf["path"] for f in files):
                    files.append({
                        "frame_id": None,
                        "frame_name": None,
                        "file_name": Path(sf["path"]).name,
                        "file_ext": sf["ext"],
                        "file_size": sf["size"],
                        "content_preview": "",
                        "created_at": None,
                        "path": sf["path"],
                        "modified": sf["modified"],
                    })

        return ToolResult(
            success=True,
            data={"files": files, "count": len(files)}
        )
    except Exception as e:
        logger.error(f"list_files failed: {e}", exc_info=True)
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

    logger.info("DEBUG execute_tool: Called tool=%s args=%s", tool_name, raw_args)

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