# Tool execution dispatcher
# Dispatches tool calls to implementations, validates args, handles timeouts

from __future__ import annotations

import asyncio
import logging
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

        if not url:
            return ToolResult(success=False, error="url is required")

        from assistant.backend.pipeline.tools import _make_fetch_url_handler

        llm_client = _orchestrator.llm_client if _orchestrator is not None else None
        handler = _make_fetch_url_handler(
            store=_store if extract_facts else None,
            llm_client=llm_client if extract_facts else None,
        )
        content = await handler(url)

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
        from assistant.backend.pipeline.filesystem import (
            PathTraversalError,
            SizeLimitError,
            read_sandbox_file,
        )

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
            if file_safe_name:
                try:
                    content = read_sandbox_file(file_safe_name)
                except FileNotFoundError:
                    pass  # Not on disk — fall back to memory slots.
                except Exception as e:
                    logger.warning(f"Failed to read sandbox file {file_safe_name}: {e}")

            # A missing file reports missing. This used to fall back to
            # `file_content_preview` from memory, which meant a disk failure
            # silently served a stale 200-character copy and the model answered
            # believing it had read the file. Memory holds what a file *is*, not
            # what it contains — see plans/2026-10-01-file-support-diagnosis.md.
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

        # 1. literal sandbox path
        try:
            content = read_sandbox_file(path)
            resolved_path = path
        except FileNotFoundError:
            pass  # Try resolving it as an uploaded-file reference.

        # 2. frame name given as path, e.g. "file_subscribers_active.csv"
        if content is None and _store is not None:
            base = Path(path).name
            candidate = path if base.startswith("file_") else f"file_{base}"
            try:
                frame = await _store.get_frame_by_name(candidate)
                if frame is not None:
                    safe = await _file_safe_name_for_frame(_store, frame)
                    if safe:
                        try:
                            content = read_sandbox_file(safe)
                            resolved_path = safe
                            resolved_frame = frame
                        except FileNotFoundError:
                            pass
            except Exception as e:  # best-effort: DB may be uninitialized
                logger.debug(f"read_file frame-name resolution unavailable: {e}")

        # 3. "file_<name>" -> "<name>" (historical disk naming)
        if content is None:
            stripped = _strip_frame_prefix(path)
            if stripped != path:
                try:
                    content = read_sandbox_file(stripped)
                    resolved_path = stripped
                except FileNotFoundError:
                    pass

        # 4. stale/partial name -> unique fuzzy match against uploaded files
        if content is None and _store is not None:
            try:
                frame = await _resolve_file_frame_fuzzy(_store, user_id, path)
                if frame is not None:
                    safe = await _file_safe_name_for_frame(_store, frame)
                    if safe:
                        try:
                            content = read_sandbox_file(safe)
                            resolved_path = safe
                            resolved_frame = frame
                        except FileNotFoundError:
                            pass
            except Exception as e:  # best-effort: DB may be uninitialized
                logger.debug(f"read_file fuzzy resolution unavailable: {e}")

        if content is None:
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

        data: dict = {"path": resolved_path, "content": content, "size": len(content)}
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

        from assistant.backend.pipeline.filesystem import (
            PathTraversalError,
            SizeLimitError,
            get_sandbox_root,
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
                frame.id,
                "file_safe_name",
                str(written_path.relative_to(get_sandbox_root())),
                source_type="file_create",
            )
            # No content slot. Memory holds what a file *is*, never what it
            # *contains*: the bytes are on disk and read verbatim via read_file.
            # The preview used to be written here and excluded at render time
            # (retrieval.py FILE_CONTENT_HINT_SLOTS), which left a stale copy that
            # the read_file disk-failure fallback could serve in place of the real
            # file. See plans/2026-10-01-file-support-diagnosis.md.

            # CSV special handling: create row frames (capped — past
            # CSV_MAX_ROW_FRAMES only row_count/columns metadata is stored;
            # row data stays on disk for read_file, so memory cannot explode).
            if written_path.suffix.lower() == ".csv":
                try:
                    reader = csv.reader(content.splitlines())
                    rows = list(reader)
                    if rows:
                        headers = rows[0]
                        row_count = len(rows) - 1
                        row_frame_cap = settings.csv_max_row_frames
                        if row_count <= row_frame_cap:
                            for i, row in enumerate(rows[1:], 1):
                                row_frame = await _store.create_frame(
                                    f"file_{written_path.name}_row_{i}", "record",
                                    source_type="csv_row", owner_user_id=user_id_int
                                )
                                for col_idx, col in enumerate(headers):
                                    # Tolerate ragged rows: pad short rows and
                                    # ignore extra cells instead of raising in
                                    # zip(strict=True) and orphaning the frames
                                    # already written for this file.
                                    val = row[col_idx] if col_idx < len(row) else ""
                                    slot_key = re.sub(
                                        r"[^a-zA-Z0-9_]", "_", col.lower().strip()
                                    )
                                    if not slot_key:
                                        slot_key = f"col_{col_idx}"
                                    await _store.upsert_slot(
                                        row_frame.id, slot_key, val, source_type="csv_row"
                                    )
                                await _store.create_association(frame.id, row_frame.id, "part_of")
                        else:
                            logger.warning(
                                "write_file %s has %d rows; storing metadata only "
                                "(CSV_MAX_ROW_FRAMES=%d)",
                                written_path.name, row_count, row_frame_cap,
                            )
                        await _store.upsert_slot(
                            frame.id, "row_count", str(row_count), source_type="file_create"
                        )
                        await _store.upsert_slot(
                            frame.id, "columns", json.dumps(headers), source_type="file_create"
                        )
                except Exception as e:
                    logger.warning(f"CSV row frame creation failed: {e}")

        return ToolResult(success=True, data={
            "path": str(written_path.relative_to(get_sandbox_root())),
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

    # 1. Look up executor
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