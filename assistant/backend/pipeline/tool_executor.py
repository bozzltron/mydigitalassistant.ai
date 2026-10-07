# Tool execution dispatcher
# Dispatches tool calls to implementations, validates args, handles timeouts

from __future__ import annotations

import asyncio
import logging
import random
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import ValidationError

from assistant.backend.config import settings
from assistant.backend.memory.models import Frame
from assistant.backend.memory.store import MemoryStore

# Slot-key namespaces owned by the system, not the model. Defined in extractor.py
# so the extraction pipeline and the tool loop enforce one shared denylist.
# `file_safe_name` is consumed as a filesystem path by the /files endpoints, so a
# model-supplied value there is a path primitive. See test_file_slot_reserved_keys.py.
from assistant.backend.pipeline.extractor import RESERVED_SLOT_PREFIXES
from assistant.backend.retry import is_transient_error_message

if TYPE_CHECKING:
    from assistant.backend.pipeline.orchestrator import Orchestrator
    from assistant.backend.pipeline.search import WebSearchTool

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# MemoryStore instance - will be injected or created from db_path
# ---------------------------------------------------------------------------

# Global store instance (set during initialization)
_store: MemoryStore | None = None
# Global embed function for recall tool
_embed_fn: Callable | None = None
# Embedding model name for search_similar_frames. Resolved from settings
# rather than carrying a literal: sqlite-vec partitions vectors by model
# name, so searching the wrong label returns zero frames, not an error.
_embedding_model: str = settings.embedding_model
# Global search tool instance
_search_tool: WebSearchTool | None = None
# Global Orchestrator. `run_scheduled_task` needs the full cognitive loop, and
# `fetch_url` needs an llm_client for fact extraction; both live on the
# Orchestrator, so it is injected rather than rebuilt here. Imported under
# TYPE_CHECKING only -- orchestrator.py imports this module, so a runtime import
# would be circular.
_orchestrator: Orchestrator | None = None


def init_store(
    db_path: str,
    embed_fn: Callable | None = None,
    embedding_model: str | None = None,
    search_tool: WebSearchTool | None = None,
    orchestrator: Orchestrator | None = None,
) -> None:
    """Initialize the global MemoryStore instance, embed function, and search tool."""
    global _store, _embed_fn, _embedding_model, _search_tool, _orchestrator
    _store = MemoryStore(db_path)
    _embed_fn = embed_fn
    _embedding_model = embedding_model or settings.embedding_model
    _search_tool = search_tool
    _orchestrator = orchestrator
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
        RenameFileArgs,
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
    register_tool("rename_file", RenameFileArgs, execute_rename_file)
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
    "rename_file": 10.0,
    "glob": 10.0,
    "list_files": 10.0,
    # A run-now executes the full loop (router + forced search + tool loop +
    # generation), so it can outlast a single LLM call; the old 60s cap cut off
    # big tasks mid-run. The scheduler's own firing path has no cap.
    "run_scheduled_task": settings.scheduled_task_timeout_seconds,
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

def validate_args(tool_name: str, raw_args: dict) -> dict | None:
    """Validate raw dict args against the tool's args class.

    Returns the validated dict, or ``None`` when validation fails. The old
    behaviour returned the raw args on failure, which made every Pydantic
    constraint (types, ranges, required fields) purely advisory.
    """
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
        return None


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

        # `file_*` slots describe where a file lives on disk. They are written by
        # the upload/write_file paths, never by the model -- and a model-supplied
        # value would be a filesystem path primitive, because the /files endpoints
        # read `file_safe_name` straight off this table. Deny the prefix at the
        # boundary so the value cannot be forged. See test_file_slot_reserved_keys.py.
        if slot_key.startswith(RESERVED_SLOT_PREFIXES):
            return ToolResult(
                success=False,
                error=(
                    f"slot_key {slot_key!r} is reserved: the 'file_' namespace is "
                    "owned by the upload pipeline and cannot be set by the model"
                ),
            )

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
        #
        # source_episode_id: the tool loop has no episode of its own, so absent an
        # explicit id this stays NULL. The previous expression was inverted --
        # `hash(session_id) % 2**31 if source_episode_id is None else None` wrote
        # a fake id when the model supplied *none* and NULL when it supplied a
        # real one. `hash()` on str is salted per process, so the fake id also
        # drifted across restarts and could collide with a genuine episode id,
        # silently attributing a fact to an unrelated turn.
        episode_id: int | None = None
        if source_episode_id is not None:
            try:
                episode_id = int(source_episode_id)
            except (TypeError, ValueError):
                logger.warning(
                    "Ignoring non-integer source_episode_id %r", source_episode_id
                )

        result = await _store.upsert_slot(
            frame_id=frame_id,
            key=slot_key,
            value=slot_value,
            source_episode_id=episode_id,
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


async def execute_upsert_association(args: dict, user_id: str, session_id: str = "") -> ToolResult:
    """Create or strengthen a typed relation between two frames."""
    if _store is None:
        return ToolResult(success=False, error="MemoryStore not initialized")

    try:
        source_name = args.get("source_frame", "")
        target_name = args.get("target_frame", "")
        relation_type = args.get("relation_type", "")
        confidence = args.get("confidence", 0.5)
        bidirectional = args.get("bidirectional", False)

        if not source_name or not target_name:
            return ToolResult(
                success=False, error="source_frame and target_frame are required"
            )
        if not relation_type:
            return ToolResult(success=False, error="relation_type is required")

        # The tool schema and its description speak in frame *names*, but
        # `create_association` takes int frame ids -- it never resolved the
        # names itself. With PRAGMA foreign_keys = ON every call raised
        # IntegrityError, and because the executor swallows exceptions into a
        # ToolResult the model was told the tool had a transient failure and
        # would retry. Verified: name-based raises, id-based succeeds.

        source = await _store.get_frame_by_name(source_name)
        if source is None:
            return ToolResult(
                success=False, error=f"no frame named {source_name!r}"
            )
        target = await _store.get_frame_by_name(target_name)
        if target is None:
            return ToolResult(
                success=False, error=f"no frame named {target_name!r}"
            )
        if source.id == target.id:
            return ToolResult(
                success=False,
                error="source_frame and target_frame are the same frame",
            )

        await _store.create_association(
            from_frame_id=source.id,
            to_frame_id=target.id,
            relation_type=relation_type,
            confidence=confidence,
        )

        if bidirectional:
            await _store.create_association(
                from_frame_id=target.id,
                to_frame_id=source.id,
                relation_type=relation_type,
                confidence=confidence,
            )

        return ToolResult(
            success=True,
            data={
                "source_frame": source_name,
                "target_frame": target_name,
                "relation_type": relation_type,
                "bidirectional": bool(bidirectional),
            },
        )
    except Exception as e:
        logger.error(f"upsert_association failed: {e}", exc_info=True)
        return ToolResult(success=False, error=str(e))


async def execute_mark_essential(args: dict, user_id: str, session_id: str = "") -> ToolResult:
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
            embedding_model=_embedding_model,
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
                        "source": assoc.from_frame_id,
                        "target": assoc.to_frame_id,
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


async def execute_get_frame(args: dict, user_id: str, session_id: str = "") -> ToolResult:
    """Retrieve full frame with all slots and associations."""
    if _store is None:
        return ToolResult(success=False, error="MemoryStore not initialized")

    try:
        frame_name = args.get("frame_name", "")
        frame = await _store.get_frame_by_name(frame_name)

        if frame is None:
            return ToolResult(success=False, error=f"Frame '{frame_name}' not found")

        # Load slots and associations from store
        slots = await _store.get_slots_for_frame(frame.id)
        associations = await _store.get_all_associations_for_frame(frame.id)

        # Convert to dict
        slots_dict = {
            slot.key: {"value": slot.value, "confidence": slot.confidence}
            for slot in slots
        }

        associations_list = []
        for assoc in associations:
            associations_list.append(
                {
                    "source": assoc.from_frame_id,
                    "target": assoc.to_frame_id,
                    "relation": assoc.relation_type,
                    "confidence": assoc.confidence,
                }
            )

        return ToolResult(
            success=True,
            data={
                "frame": {
                    "name": frame.name,
                    "slots": slots_dict,
                    "associations": associations_list,
                }
            },
        )
    except Exception as e:
        logger.error(f"get_frame failed: {e}", exc_info=True)
        return ToolResult(success=False, error=str(e))


async def execute_get_slot_history(args: dict, user_id: str, session_id: str = "") -> ToolResult:
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


async def execute_search_episodes(args: dict, user_id: str, session_id: str = "") -> ToolResult:
    """Search past conversation turns semantically."""
    if _store is None:
        return ToolResult(success=False, error="MemoryStore not initialized")

    try:
        query = args.get("query", "")
        max_results = args.get("max_results", 5)

        if not query.strip():
            return ToolResult(success=False, error="query is required")
        if _embed_fn is None:
            return ToolResult(
                success=False,
                error=(
                    "episode search needs the embedder, which is not configured; "
                    "ask the user directly instead"
                ),
            )

        # `consolidate.search_episodes` was imported here but does not exist, so
        # every call to this tool raised ImportError and the model was told the
        # tool failed. The store's vector search is the real implementation; it
        # needs the query embedding and the model label that owns the vectors.
        embedding = await _embed_fn(query)

        exclude: list[str] = []
        requested_session = args.get("session_id") or session_id
        if requested_session:
            exclude.append(requested_session)

        matches = await _store.search_similar_episodes(
            embedding=embedding,
            user_id=int(user_id) if user_id else None,
            embedding_model=_embedding_model,
            limit=max_results,
            exclude_session_ids=exclude or None,
        )

        results = [
            {
                "episode_id": ep.id,
                "session_id": ep.session_id,
                "role": ep.role,
                "content": ep.content,
                "similarity": round(sim, 4),
            }
            for ep, sim in matches
        ]
        return ToolResult(
            success=True,
            data={"query": query, "results": results, "count": len(results)},
        )
    except Exception as e:
        logger.error(f"search_episodes failed: {e}", exc_info=True)
        return ToolResult(success=False, error=str(e))


# ---------------------------------------------------------------------------
# External tool executors
# ---------------------------------------------------------------------------

async def execute_web_search(args: dict, user_id: str, session_id: str = "") -> ToolResult:
    """Search the web via configured backend."""
    global _search_tool
    try:
        if _search_tool is None:
            return ToolResult(success=False, error="Search tool not initialized")

        query = args.get("query", "")
        num_results = args.get("num_results", 5)

        results, info = await _search_tool.search_with_info(query, num_results=num_results)

        return ToolResult(success=True, data={"results": results, "search_info": info})
    except Exception as e:
        logger.error(f"web_search failed: {e}", exc_info=True)
        return ToolResult(success=False, error=str(e))


async def execute_fetch_url(args: dict, user_id: str, session_id: str = "") -> ToolResult:
    """Fetch and extract text from URL. Auto-extracts facts.

    The previous body imported `assistant.backend.pipeline.fetch`, a module that
    does not exist, so this advertised tool always failed. The working
    implementation is `_make_fetch_url_handler` in tools.py -- the same one the
    CLI's fetch path uses. It fetches, strips HTML, respects robots.txt, and (when
    a store and llm_client are wired in) extracts facts into memory with
    `source_type="web_fetch"`, which is what this tool's description promises.

    Note: `extract_facts=False` is honoured by skipping the memory write, which
    `_make_fetch_url_handler` does not parameterise, so the handler is built with
    or without the store/llm_client depending on the flag.
    """
    try:
        url = args.get("url", "")
        extract_facts = args.get("extract_facts", True)
        follow_links = int(args.get("follow_links", 0) or 0)

        if not url:
            return ToolResult(success=False, error="url is required")

        from assistant.backend.pipeline.tools import _make_fetch_url_handler

        llm_client = _orchestrator.llm_client if _orchestrator is not None else None
        handler = _make_fetch_url_handler(
            store=_store if extract_facts else None,
            llm_client=llm_client if extract_facts else None,
        )
        content = await handler(url, follow_links)

        # The handler signals failure with a string prefix rather than raising.
        if isinstance(content, str) and content.startswith("Error:"):
            return ToolResult(success=False, error=content)

        result = {"url": url, "content": content, "extract_facts": bool(extract_facts)}

        return ToolResult(success=True, data=result)
    except Exception as e:
        logger.error(f"fetch_url failed: {e}", exc_info=True)
        return ToolResult(success=False, error=str(e))


# ---------------------------------------------------------------------------
# File read resolution
# ---------------------------------------------------------------------------

# Frame source types whose full content lives on disk (read via read_file)
# rather than in slots: user uploads and tool-created sandbox files.
FILE_FRAME_SOURCE_TYPES = ("file_upload", "file_create")


def _strip_frame_prefix(name: str) -> str:
    """Strip a leading ``file_`` frame-name prefix from a file reference.

    Uploaded files are stored on disk under their exact name (e.g.
    ``subscribers_active.csv``) while their memory frame is named
    ``file_subscribers_active.csv``. The model frequently quotes the frame
    name as a read path, so we map between the two.
    """
    base = Path(name).name
    return base[len("file_"):] if base.startswith("file_") else base


def _name_tokens(name: str) -> set[str]:
    """Significant (non-numeric) filename tokens, for fuzzy matching."""
    return {t for t in re.split(r"[^A-Za-z0-9]+", name) if t and not t.isdigit()}


async def _file_safe_name_for_frame(store: MemoryStore, frame: Frame) -> str:
    """On-disk sandbox name of a file frame (its ``file_safe_name`` slot)."""
    slots = await store.get_slots_for_frame(frame.id)
    return next((s.value for s in slots if s.key == "file_safe_name"), "")


def _coerce_user_id(user_id: str | None) -> int | None:
    """Parse a user id without raising on empty/garbage values."""
    try:
        return int(user_id) if user_id not in (None, "") else None
    except (TypeError, ValueError):
        return None


async def _user_file_frames(store: MemoryStore, user_id: str) -> list[Frame]:
    """File frames visible to a user (uploads + tool-created files).

    Returns an empty list when the user is unknown; fuzzy resolution must not
    guess between users' files.
    """
    owner = _coerce_user_id(user_id)
    if owner is None:
        return []
    frames = await store.list_frames(owner_user_id=owner)
    return [
        f
        for f in frames
        if f.source_type in FILE_FRAME_SOURCE_TYPES and f.priority > 0
    ]


async def _resolve_file_frame_fuzzy(
    store: MemoryStore, user_id: str, reference: str
) -> Frame | None:
    """Match a possibly stale file reference to the current file frame.

    Past conversations quote file names that no longer exist verbatim
    (renames, name-preservation changes). We still resolve them when the
    reference is unambiguous: same extension plus either exact/suffix name
    containment or a >=2 significant-token overlap. Only a *unique* best
    match is returned so we never guess between two similar files.
    """
    probe = _strip_frame_prefix(reference)
    probe_tokens = _name_tokens(probe)
    probe_ext = Path(reference).suffix.lower()

    best_score = -1
    best: Frame | None = None
    best_count = 0
    for frame in await _user_file_frames(store, user_id):
        slots = {s.key: s.value for s in await store.get_slots_for_frame(frame.id)}
        cand_name = slots.get("file_name") or slots.get("file_safe_name") or ""
        cand_ext = Path(cand_name).suffix.lower()
        if cand_ext and probe_ext and cand_ext != probe_ext:
            continue
        if (
            cand_name
            and (probe == cand_name or probe.endswith(cand_name) or cand_name.endswith(probe))
        ):
            score = 3
        else:
            shared = _name_tokens(cand_name) & probe_tokens
            if len(shared) < 2:
                continue
            score = 2
        if score > best_score:
            best_score, best, best_count = score, frame, 1
        elif score == best_score:
            best_count += 1
    return best if best is not None and best_count == 1 else None


async def _available_file_names(store: MemoryStore, user_id: str) -> str:
    """Comma-separated list of the user's file names, for error messages."""
    names = set()
    for frame in await _user_file_frames(store, user_id):
        slots = {s.key: s.value for s in await store.get_slots_for_frame(frame.id)}
        if slots.get("file_name"):
            names.add(slots["file_name"])
    return ", ".join(sorted(names)) or "(none)"


# Extensions with no extractor, where the bytes on disk are already the content
# the model should read. Everything else goes through `extract_file_content` —
# html/xml/eml are text but their extractors strip markup or parse the message;
# reading those raw would hand the model `<h1>Title</h1>` or MIME boundaries.
PLAIN_TEXT_EXTS = frozenset({"txt", "md", "log", "yaml", "yml"})

# What `read_file` hands the model raw. `.ics` is included even though it has an
# extractor: the extractor summarises the calendar (and caps at five events) for
# memory, but a summary cannot be edited back into a calendar, so the model needs
# the raw document to inspect or repair one. See docs/FILES.md.
READ_RAW_EXTS = PLAIN_TEXT_EXTS | {"ics"}

# Formats that are binary documents on disk. `read_file` *extracts* them to text,
# but the bytes are not that text, so a find-and-replace can never match: the
# model sees "Ada Lovelace" while the file is a zip. Editing one means read ->
# rewrite (write_file re-renders real bytes). `edit_file` refuses these rather
# than reporting a misleading "old_text not found".
BINARY_DOCUMENT_EXTS = frozenset(
    {"pdf", "docx", "xlsx", "xls", "pptx", "odt", "ods", "odp", "rtf"}
)

# How much extracted text a single read_file returns to the model. This is NOT a
# file-size limit: the file is stored whole on disk and nothing is refused at any
# size. It bounds only what can ride in one turn's context window, which is a
# property of the model, not a policy on the user's files. The marker tells the
# model it saw a fragment so it can say so rather than answer from a silent cut.
MAX_READ_CHARS_FOR_MODEL = 60_000


async def _read_file_text(file_path: str, ext: str) -> tuple[str | None, str | None]:
    """Return ``(text, error)`` for a sandbox file, extracting when needed.

    Formats in ``READ_RAW_EXTS`` (plain text, plus ``.ics``) are read directly.
    Everything else goes through ``extract_file_content``, the same path upload
    uses — the model must read a document the way upload understood it, not as
    raw bytes. Reading a PDF as UTF-8 was the bug: a 6.7 MB press kit extracted
    to 9,213 clean characters at upload and returned binary noise (or nothing) on
    read. The same rule covers html/xml/eml, which are text but whose extractors
    strip markup or parse the message. ``.ics`` is the exception — read raw so a
    calendar can be inspected and edited (see ``READ_RAW_EXTS``).

    ``extract_file_content`` extracts from the bytes it is given, not from the
    path, so the bytes are read here and handed over.

    Returns ``(None, reason)`` when the format is known but no text could be
    recovered (e.g. an image-only PDF with no text layer), so the caller can tell
    the model "not readable as text" instead of handing it emptiness.
    """
    from assistant.backend.pipeline.files import extract_file_content
    from assistant.backend.pipeline.filesystem import resolve_sandbox_path

    path = resolve_sandbox_path(file_path)

    if ext in READ_RAW_EXTS:
        # FileNotFoundError propagates: "missing" and "unreadable" are different
        # answers and the caller reports them differently.
        return path.read_text(encoding="utf-8", errors="replace"), None

    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        raise
    except OSError as e:
        return None, f"'{file_path}' could not be opened ({e})"

    try:
        result = await extract_file_content(file_path, ext, raw)
    except Exception as e:
        logger.warning("Extraction failed for %s (.%s): %s", file_path, ext, e)
        return None, f"could not extract text from .{ext} file"

    text = (result.text or "").strip()
    if not text:
        return None, (
            f"the .{ext} file opened but contains no extractable text"
            " (it may be scanned images rather than text)"
        )
    return text, None


def _bounded_for_model(text: str) -> str:
    """Trim extracted text to the model's share, with an honest marker.

    Never applied to the file on disk — this is the context-window bound and
    nothing else. The marker states the true total so the model can tell the user
    it read a fragment rather than believing it read the whole document.
    """
    if len(text) <= MAX_READ_CHARS_FOR_MODEL:
        return text
    return (
        text[:MAX_READ_CHARS_FOR_MODEL]
        + f"\n\n... [truncated for this turn: {len(text):,} characters total]"
    )


async def execute_read_file(args: dict, user_id: str, session_id: str) -> ToolResult:
    """Read a sandbox file or an uploaded file.

    ``path`` resolution order:
      1. literal sandbox path (``"subscribers_active.csv"``)
      2. the frame name of an uploaded file (``"file_subscribers_active.csv"``)
      3. the path with a leading ``file_`` prefix stripped
      4. a unique fuzzy match against the user's uploaded files, so names
         quoted in old conversations still resolve to the current file
    """
    try:
        from assistant.backend.pipeline.filesystem import PathTraversalError

        frame_id = args.get("frame_id")
        frame_name = args.get("frame_name")
        path = args.get("path", "")

        # ---- Read an uploaded file by frame_id / frame_name ----------------
        if frame_id is not None or frame_name is not None:
            if _store is None:
                return ToolResult(success=False, error="MemoryStore not initialized")

            frame: Frame | None = None
            if frame_id is not None:
                frame = await _store.get_frame(frame_id)
            elif frame_name:
                frame = await _store.get_frame_by_name(frame_name)

            if frame is None:
                # Stale name from an old conversation -> current frame.
                frame = await _resolve_file_frame_fuzzy(_store, user_id, frame_name or "")

            if frame is None:
                available = await _available_file_names(_store, user_id)
                return ToolResult(
                    success=False,
                    error=(
                        f"Frame not found: {frame_id or frame_name}. "
                        f"Available uploaded files: {available}. "
                        "Use list_files() to list current files."
                    ),
                )

            # Check ownership
            owner = _coerce_user_id(user_id)
            if (
                frame.owner_user_id is not None
                and owner is not None
                and frame.owner_user_id != owner
            ):
                return ToolResult(
                    success=False,
                    error="Access denied: file belongs to another user"
                )

            slots = await _store.get_slots_for_frame(frame.id)
            slots_dict = {slot.key: slot.value for slot in slots}

            file_name = slots_dict.get("file_name", "unknown")
            file_ext = slots_dict.get("file_ext", "")
            file_safe_name = slots_dict.get("file_safe_name", "")

            content = ""
            read_error: str | None = None
            if file_safe_name:
                try:
                    content, read_error = await _read_file_text(
                        file_safe_name, (file_ext or "").lower()
                    )
                    content = content or ""
                except FileNotFoundError:
                    pass  # Not on disk — report it below, never fall back to memory.
                except Exception as e:
                    logger.warning(f"Failed to read sandbox file {file_safe_name}: {e}")
                    read_error = str(e)

            # A file we opened but could not turn into text is reported as such.
            # Returning the raw bytes as "content" is what shipped binary noise to
            # the model; returning empty read to it as an unreadable file.
            if not content and read_error:
                return ToolResult(
                    success=False,
                    error=f"Could not read '{file_name}': {read_error}.",
                )

            # A missing file reports missing. This used to fall back to
            # `file_content_preview` from memory, which meant a disk failure
            # silently served a stale 200-character copy and the model answered
            # believing it had read the file. Memory holds what a file *is*, not
            # what it contains — see docs/FILES.md.
            if not content:
                return ToolResult(
                    success=False,
                    error=(
                        f"File '{file_name}' is recorded in memory but is not "
                        f"readable in the sandbox"
                        + (f" at {file_safe_name}" if file_safe_name else "")
                        + ". It may have been moved or deleted outside the assistant."
                    ),
                )

            bounded = _bounded_for_model(content)
            return ToolResult(
                success=True,
                data={
                    "frame_id": frame.id,
                    "frame_name": frame.name,
                    "file_name": file_name,
                    "file_ext": file_ext,
                    "content": bounded,
                    "size": len(bounded),
                    "total_chars": len(content),
                },
            )

        # ---- Read from the sandbox by path --------------------------------
        if not path:
            return ToolResult(
                success=False,
                error="path is required (or provide frame_id/frame_name)"
            )

        requested = path
        content: str | None = None
        resolved_path = ""
        resolved_frame: Frame | None = None

        def _ext(path_str: str) -> str:
            return path_str.rsplit(".", 1)[-1].lower() if "." in path_str else ""

        # Set when a file was found but could not be turned into text, so the
        # caller reports *that* rather than "File not found" — which would send
        # the model hunting for a file it already has.
        unreadable: str | None = None

        async def _try(resolved: str) -> str | None:
            """Read `resolved` as text, extracting when its extension needs it.

            Returns None when the file is absent or resolution should move on to
            the next strategy. A file that exists but cannot be read sets
            `unreadable` and still returns None, so the final error is accurate.

            `PathTraversalError` deliberately propagates: a path escaping the
            sandbox is a security rejection, and swallowing it into "not found"
            would hide an attack attempt as a missing file.
            """
            nonlocal unreadable
            try:
                text, err = await _read_file_text(resolved, _ext(resolved))
            except FileNotFoundError:
                return None
            except PermissionError as e:
                unreadable = f"'{resolved}' is not readable ({e})"
                return None
            except PathTraversalError:
                raise
            except Exception as e:
                logger.debug(f"read_file could not read {resolved}: {e}")
                return None
            if text is None and err:
                unreadable = f"'{resolved}': {err}"
            return text

        # 1. literal sandbox path
        content = await _try(path)
        if content is not None:
            resolved_path = path

        # 2. frame name given as path, e.g. "file_subscribers_active.csv"
        if content is None and _store is not None:
            base = Path(path).name
            candidate = path if base.startswith("file_") else f"file_{base}"
            try:
                frame = await _store.get_frame_by_name(candidate)
                if frame is not None:
                    safe = await _file_safe_name_for_frame(_store, frame)
                    if safe:
                        text = await _try(safe)
                        if text is not None:
                            content = text
                            resolved_path = safe
                            resolved_frame = frame
            except Exception as e:  # best-effort: DB may be uninitialized
                logger.debug(f"read_file frame-name resolution unavailable: {e}")

        # 3. "file_<name>" -> "<name>" (historical disk naming)
        if content is None:
            stripped = _strip_frame_prefix(path)
            if stripped != path:
                text = await _try(stripped)
                if text is not None:
                    content = text
                    resolved_path = stripped

        # 4. stale/partial name -> unique fuzzy match against uploaded files
        if content is None and _store is not None:
            try:
                frame = await _resolve_file_frame_fuzzy(_store, user_id, path)
                if frame is not None:
                    safe = await _file_safe_name_for_frame(_store, frame)
                    if safe:
                        text = await _try(safe)
                        if text is not None:
                            content = text
                            resolved_path = safe
                            resolved_frame = frame
            except Exception as e:  # best-effort: DB may be uninitialized
                logger.debug(f"read_file fuzzy resolution unavailable: {e}")

        if content is None:
            # A file that exists but could not be read is a different error from
            # one that does not exist, and the model acts differently on each.
            if unreadable:
                return ToolResult(
                    success=False,
                    error=f"Could not read {unreadable}.",
                )
            error = f"File not found: {requested!r}"
            if _store is not None:
                try:
                    available = await _available_file_names(_store, user_id)
                    error += (
                        f". Not a sandbox path and no unique uploaded-file match. "
                        f"Available files: {available}. "
                        "Use list_files() to list current files."
                    )
                except Exception:  # best-effort: DB may be uninitialized
                    pass
            return ToolResult(success=False, error=error)

        bounded = _bounded_for_model(content)
        data: dict = {
            "path": resolved_path,
            "content": bounded,
            "size": len(bounded),
            "total_chars": len(content),
        }
        if resolved_frame is not None:
            data["frame_id"] = resolved_frame.id
            data["frame_name"] = resolved_frame.name
            # No content hint is refreshed here. This used to write the first 200
            # characters into the frame on every read — the fourth such write site,
            # after create, edit, and upload — which put content into memory as a
            # side effect of reading it and left a copy that went stale immediately.
            # The bytes are on disk; reading them is the whole job.
        elif resolved_path != requested:
            data["resolved_from"] = requested
        return ToolResult(success=True, data=data)
    except PathTraversalError as e:
        return ToolResult(success=False, error=str(e))
    except FileNotFoundError as e:
        return ToolResult(success=False, error=str(e))
    except Exception as e:
        logger.error(f"read_file failed: {e}", exc_info=True)
        return ToolResult(success=False, error=str(e))


async def execute_write_file(args: dict, user_id: str, session_id: str) -> ToolResult:
    """Create or overwrite a file in the sandbox.

    The content is rendered into real bytes for the file's format (docx/pdf/xlsx/
    …, not a string with a document extension) and the memory frame is built by
    ``apply_file_to_memory`` — the same step upload uses, so an agent-written file
    and an uploaded one produce the same memory. An extension with no writer is
    refused rather than written as a fake file. See the file_write_parity
    experiment.
    """
    try:
        from assistant.backend.pipeline.files import (
            apply_file_to_memory,
            render_file_bytes,
        )
        from assistant.backend.pipeline.filesystem import (
            PathTraversalError,
            get_sandbox_root,
            resolve_sandbox_path,
            write_sandbox_bytes,
        )

        path = args.get("path", "")
        content = args.get("content", "")
        overwrite = args.get("overwrite", False)

        if not path:
            return ToolResult(success=False, error="path is required")

        # Resolve first so a traversal attempt is reported as such, before any
        # extension complaint masks it.
        resolved = resolve_sandbox_path(path)

        ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
        if not ext:
            return ToolResult(
                success=False,
                error="path must include a file extension (e.g. report.md)",
            )

        data, render_error = render_file_bytes(ext, content)
        if data is None:
            return ToolResult(success=False, error=render_error)

        written_path = write_sandbox_bytes(path, data, overwrite)

        if _store is not None:
            try:
                user_id_int = int(user_id)
            except (ValueError, TypeError):
                user_id_int = 1
            # The sandbox-relative path (not just the basename): a nested file
            # 'notes/x.txt' must resolve by its stored name. read_file maps frame
            # name -> this value.
            safe_name = str(written_path.relative_to(get_sandbox_root()))
            summary = await apply_file_to_memory(
                _store,
                frame_name=f"file_{written_path.name}",
                safe_filename=safe_name,
                ext=ext,
                content_bytes=data,
                user_id=user_id_int,
                source_type="file_create",
                source_reliability=0.8,
            )
        else:
            summary = {"frame_id": None}

        return ToolResult(success=True, data={
            "path": str(resolved.relative_to(get_sandbox_root())),
            "size": len(data),
            "frame_id": summary.get("frame_id"),
        })
    except PathTraversalError as e:
        return ToolResult(success=False, error=str(e))
    except FileExistsError as e:
        return ToolResult(success=False, error=str(e))
    except Exception as e:
        logger.error(f"write_file failed: {e}", exc_info=True)
        return ToolResult(success=False, error=str(e))


def _flexible_whitespace_pattern(old_text: str) -> str:
    """A regex for `old_text` where any run of whitespace matches any other.

    The most common reason an otherwise-correct `old_text` fails to match is
    whitespace: a collapsed blank line, a trailing space, tabs vs spaces. This
    tolerates that and nothing else — words and punctuation must still be exact,
    because replacing the wrong region is worse than failing.
    """
    parts = re.split(r"(\s+)", old_text)
    return "".join(r"\s+" if part.isspace() else re.escape(part) for part in parts)


def _closest_region(content: str, old_text: str, context: int = 3) -> str | None:
    """A few lines of `content` nearest the best lexical match for `old_text`.

    Returned in the failure message so the model can correct its `old_text`
    instead of hitting a dead end (model-first recovery).
    """
    lines = content.splitlines()
    target = old_text.strip().splitlines()
    probe = target[0].strip() if target else old_text.strip()
    probe_tokens = set(re.findall(r"[A-Za-z0-9]+", probe.lower()))
    if not probe_tokens:
        return None
    best_index, best_score = None, 0
    for index, line in enumerate(lines):
        score = len(probe_tokens & set(re.findall(r"[A-Za-z0-9]+", line.lower())))
        if score > best_score:
            best_index, best_score = index, score
    if best_index is None:
        return None
    lo = max(0, best_index - context)
    hi = min(len(lines), best_index + context + 1)
    return "\n".join(lines[lo:hi])


async def execute_edit_file(args: dict, user_id: str, session_id: str) -> ToolResult:
    """Make a surgical edit to an existing text file.

    Text only, and whitespace-tolerant. A binary document (`.docx`, `.pdf`, …)
    cannot be edited in place — the bytes are not the text `read_file` shows — so
    it is refused with a redirect to read + rewrite via `write_file`.
    """
    try:
        from pathlib import Path

        from assistant.backend.pipeline.filesystem import (
            PathTraversalError,
            read_sandbox_file,
            write_sandbox_file,
        )

        path = args.get("path", "")
        old_text = args.get("old_text", "")
        new_text = args.get("new_text", "")
        replace_all = args.get("replace_all", True)

        if not path:
            return ToolResult(success=False, error="path is required")
        if not old_text.strip():
            return ToolResult(success=False, error="old_text cannot be blank")

        ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
        if ext in BINARY_DOCUMENT_EXTS:
            return ToolResult(
                success=False,
                error=(
                    f".{ext} is a binary document and cannot be edited in place. "
                    "Read it with read_file, then rewrite the whole file with "
                    f"write_file (which re-renders a real .{ext})."
                ),
            )

        content = read_sandbox_file(path)

        # Exact first, then whitespace-tolerant. Never fuzzy beyond whitespace:
        # matching the wrong region silently is worse than failing loudly.
        pattern = re.escape(old_text)
        count = content.count(old_text)
        if count == 0:
            pattern = _flexible_whitespace_pattern(old_text)
            count = len(re.findall(pattern, content))

        if count == 0:
            message = (
                f"old_text not found in {path} ({len(content)} chars). Read the "
                "file and copy the exact text to replace."
            )
            region = _closest_region(content, old_text)
            if region:
                message += f"\nClosest region:\n{region}"
            return ToolResult(success=False, error=message)

        if replace_all:
            new_content = re.sub(pattern, lambda _m: new_text, content)
            changes = count
        else:
            new_content = re.sub(pattern, lambda _m: new_text, content, count=1)
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
                # Only identity changes here. The content is on disk and is read
                # verbatim; a preview copy would go stale the moment this edit
                # landed, which is precisely the drift the boundary exists to stop.
                await _store.upsert_slot(
                    frame.id, "file_size", str(len(new_content)), source_type="file_edit"
                )

        return ToolResult(success=True, data={
            "path": path, "changes": changes, "new_size": len(new_content)
        })
    except PathTraversalError as e:
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

        # Memory cleanup: deleting a file prunes its frame and any CSV row
        # frames permanently — soft-delete (forget_frame) leaves them visible
        # in the brain graph, dangling after the physical file is gone.
        if _store is not None:
            frame_name = f"file_{Path(path).name}"
            frame = await _store.get_frame_by_name(frame_name)
            if frame:
                await _store.prune_file_frame(frame.id)

        return ToolResult(success=True, data={"path": path})
    except PathTraversalError as e:
        return ToolResult(success=False, error=str(e))
    except FileNotFoundError as e:
        return ToolResult(success=False, error=str(e))
    except Exception as e:
        logger.error(f"delete_file failed: {e}", exc_info=True)
        return ToolResult(success=False, error=str(e))


async def execute_rename_file(args: dict, user_id: str, session_id: str) -> ToolResult:
    """Rename a sandbox file, and its memory frame with it.

    A rename is a first-class operation, not a read+write+delete dance the model
    has to compose: the disk file and the ``file_<name>`` frame move together, so
    the old name stops resolving and the new one works immediately. The extension
    is kept fixed — renaming a ``.txt`` to ``.docx`` would leave text bytes under a
    document name.
    """
    try:
        from pathlib import Path

        from assistant.backend.pipeline.filesystem import (
            PathTraversalError,
            get_sandbox_root,
            rename_sandbox_file,
        )

        path = args.get("path", "")
        new_name = args.get("new_name", "")
        if not path or not new_name:
            return ToolResult(success=False, error="path and new_name are required")

        if Path(new_name).suffix.lower() != Path(path).suffix.lower():
            return ToolResult(
                success=False,
                error=(
                    "new_name must keep the same extension "
                    f"({Path(path).suffix or 'none'}) — use write_file to change a "
                    "file's format"
                ),
            )

        new_base = Path(new_name).name
        new_frame_name = f"file_{new_base}"

        # Check the memory-name clash *before* touching disk, so a refusal cannot
        # leave a renamed file whose frame name collides with another file.
        old_frame = None
        if _store is not None:
            old_frame = await _store.get_frame_by_name(f"file_{Path(path).name}")
            clash = await _store.get_frame_by_name(new_frame_name)
            if clash is not None and (old_frame is None or clash.id != old_frame.id):
                return ToolResult(
                    success=False,
                    error=f"a file named '{new_base}' already exists",
                )

        old_path, new_path = rename_sandbox_file(path, new_name)
        new_rel = str(new_path.relative_to(get_sandbox_root()))

        frame_moved = False
        if old_frame is not None:
            await _store.update_frame(old_frame.id, name=new_frame_name)
            # Derived state, not a belief: a file's name is a fact about where it
            # is, and a rename is a mutation, not a contradicting claim. These
            # overwrite rather than going through the conflict ladder, which would
            # keep the old name (both sides are file metadata at the same rung).
            await _store.set_derived_slot(
                old_frame.id, "file_name", new_rel, source_type="file_create"
            )
            await _store.set_derived_slot(
                old_frame.id, "file_safe_name", new_rel, source_type="file_create"
            )
            frame_moved = True

        return ToolResult(success=True, data={
            "old_path": str(old_path.relative_to(get_sandbox_root())),
            "path": new_rel,
            "frame_moved": frame_moved,
        })
    except PathTraversalError as e:
        return ToolResult(success=False, error=str(e))
    except FileNotFoundError as e:
        return ToolResult(success=False, error=str(e))
    except FileExistsError as e:
        return ToolResult(success=False, error=str(e))
    except Exception as e:
        logger.error(f"rename_file failed: {e}", exc_info=True)
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

                # Only process frames that have proper file metadata
                if not slots_dict.get("file_name") or not slots_dict.get("file_ext"):
                    continue

                file_name = slots_dict.get("file_name", "unknown")
                file_safe_name = slots_dict.get("file_safe_name", "")

                # Try to find matching sandbox file
                sandbox_info = sandbox_by_name.get(file_safe_name, {})

                file_size_val = slots_dict.get("file_size")
                file_size = (
                    int(file_size_val)
                    if file_size_val and str(file_size_val).strip()
                    else None
                )

                files.append({
                    "frame_id": frame.id,
                    "frame_name": frame.name,
                    "file_name": file_name,
                    "file_ext": slots_dict.get("file_ext", ""),
                    "file_size": file_size,
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


async def execute_run_scheduled_task(args: dict, user_id: str, session_id: str = "") -> ToolResult:
    """Execute a scheduled task immediately (run_now).

    This is AGENTS.md critical path #2. The previous body imported
    `assistant.backend.scheduler.run_now`, which is not exported by that package,
    so every invocation raised ImportError and the failure was reported to the
    model as a recoverable tool error -- the feature had never worked.

    Delegates to the Orchestrator, which owns `run_scheduled_task` (the full
    cognitive loop) and the same name lookup the chat-side "run my briefing now"
    path uses, so the two cannot drift.
    """
    try:
        task_name = (args.get("task_name") or "").strip()
        if not task_name:
            return ToolResult(success=False, error="task_name is required")

        if _orchestrator is None:
            return ToolResult(
                success=False,
                error="orchestrator not initialized; cannot run scheduled tasks",
            )
        if _store is None:
            return ToolResult(success=False, error="MemoryStore not initialized")

        tasks = await _store.get_scheduled_tasks(
            owner_user_id=int(user_id) if user_id else None
        )
        match = next((t for t in tasks if t["name"] == task_name), None)
        if match is None:
            available = [t["name"] for t in tasks]
            return ToolResult(
                success=False,
                error=(
                    f"no scheduled task named {task_name!r}. "
                    f"Available: {available or 'none'}"
                ),
            )

        result = await _orchestrator.run_scheduled_task(
            match["prompt"], int(user_id), match["name"]
        )

        # Record the run so `last_run` and the task's result summary are truthful.
        # The chat-side run_now path does this too; missing it here is what let
        # "what did my briefing find" drift out of sync with reality.
        from datetime import UTC, datetime

        await _store.update_scheduled_task_run(
            frame_id=match["id"],
            last_run=datetime.now(UTC).isoformat(),
            last_result_summary=(result or "")[:2000],
        )

        return ToolResult(
            success=True,
            data={
                "task_name": match["name"],
                "frame_id": match["id"],
                "result": result,
            },
        )
    except Exception as e:
        logger.error(f"run_scheduled_task failed: {e}", exc_info=True)
        return ToolResult(success=False, error=str(e))


async def execute_compute(args: dict, user_id: str, session_id: str = "") -> ToolResult:
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

async def execute_plan(args: dict, user_id: str, session_id: str = "") -> ToolResult:
    """Return a structured plan step list for orchestrator to execute."""
    try:
        goal = args.get("goal", "")
        steps = args.get("steps", [])

        return ToolResult(success=True, data={"goal": goal, "steps": steps})
    except Exception as e:
        logger.error(f"plan failed: {e}", exc_info=True)
        return ToolResult(success=False, error=str(e))


async def execute_think(args: dict, user_id: str, session_id: str = "") -> ToolResult:
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


async def execute_finalize(args: dict, user_id: str, session_id: str = "") -> ToolResult:
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

# Tools that only read (filesystem, memory). Their calls are idempotent, so a
# transient failure is retried here rather than handed to the model as an error
# it must recover from. Side-effecting tools are deliberately excluded: repeating
# a write/delete/upsert can double-apply it, and the tool loop already lets the
# model — which can see the result — decide whether to repeat the call.
# web_search/fetch_url are excluded too: they already retry inside their own HTTP
# layer, and nesting the two would multiply attempts.
_READ_ONLY_TOOLS = frozenset(
    {"list_files", "read_file", "glob", "recall", "search_episodes"}
)
_TOOL_RETRY_ATTEMPTS = 3


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

    logger.debug("execute_tool: tool=%s arg_keys=%s", tool_name, sorted(raw_args))

    # 1. Look up executor. The registry is seeded on demand rather than only by
    # `init_store`, because it is module-level global state: a caller that runs a tool
    # before any store is wired got "Unknown tool: write_file" for a tool that plainly
    # exists. That made the builtin set depend on whether some earlier caller happened
    # to initialise a store -- an ordering dependency between tests, and a latent
    # version of the same problem in production startup paths.
    if not TOOL_REGISTRY:
        _register_builtin_tools()
    if tool_name not in TOOL_REGISTRY:
        return ToolResult(success=False, error=f"Unknown tool: {tool_name}")

    # 2. Validate args. A failure is a failure -- constraints are not advisory.
    validated = validate_args(tool_name, raw_args)
    if validated is None:
        return ToolResult(
            success=False,
            error=f"Invalid arguments for tool {tool_name}: {raw_args!r}",
        )

    schema_info = TOOL_REGISTRY[tool_name]
    func = schema_info["func"]
    timeout = schema_info["timeout"]

    # 3. Execute with timeout. Read-only tools are idempotent, so a transient
    # failure is retried; side-effecting tools are not (see _READ_ONLY_TOOLS).
    attempts = _TOOL_RETRY_ATTEMPTS if tool_name in _READ_ONLY_TOOLS else 1
    result: ToolResult
    for attempt in range(1, attempts + 1):
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
            result = ToolResult(success=False, error=str(e))

        error = getattr(result, "error", "") or ""
        if (
            getattr(result, "success", True)
            or attempt >= attempts
            or not is_transient_error_message(error)
        ):
            break
        delay = min(0.25 * (2 ** (attempt - 1)), 2.0) * random.uniform(0.8, 1.2)
        logger.warning(
            "Tool %s transient failure (attempt %d/%d): %s; retrying in %.2fs",
            tool_name,
            attempt,
            attempts,
            error,
            delay,
        )
        await asyncio.sleep(delay)

    # 4. Attach latency metadata
    result.metadata = getattr(result, "metadata", {})
    result.metadata["latency_ms"] = int((time.time() - start) * 1000)
    result.metadata["tool"] = tool_name

    return result