import asyncio
import logging
import shutil
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import Depends as _Depends
from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from assistant.backend.config import settings
from assistant.backend.db.schema import init_db
from assistant.backend.db.sqlcipher import connect
from assistant.backend.memory.metadata import (
    METADATA_KEY_EMBEDDING_MODEL,
    get_metadata,
    set_metadata,
)
from assistant.backend.memory.models import (
    Association,
    Conflict,
    Episode,
    Feedback,
    Frame,
    Slot,
    User,
)
from assistant.backend.memory.retrieval import Retriever
from assistant.backend.memory.store import (
    MemoryStore,
    merge_match_scores,
)
from assistant.backend.memory.working_memory import WorkingMemory
from assistant.backend.pipeline.extractor import (
    CorrectionResult,
    apply_correction,
    extract_correction,
    validate_correction,
)
from assistant.backend.pipeline.llm_client import OllamaClient
from assistant.backend.pipeline.orchestrator import ChatRequest, ChatResponse, Orchestrator
from assistant.backend.pipeline.orchestrator import OrchestratorDeps as _OrchestratorDeps
from assistant.backend.pipeline.search import WebSearchTool

logger = logging.getLogger(__name__)


async def _check_embedding_model_mismatch(db_path: str) -> None:
    """Check if the configured embedding model matches what's stored in metadata.

    If metadata has no embedding_model entry (first run after migration), seed it.
    If it differs from settings.embedding_model, log a warning with re-embed instructions.
    """
    stored_model = await get_metadata(db_path, METADATA_KEY_EMBEDDING_MODEL)
    current_model = settings.embedding_model
    if stored_model is None:
        await set_metadata(db_path, METADATA_KEY_EMBEDDING_MODEL, current_model)
        logger.info("Seeded metadata with embedding_model=%s", current_model)
    elif stored_model != current_model:
        logger.warning(
            "Embedding model mismatch: metadata has '%s' but settings have '%s'. "
            "Run 'assistant db reembed --model %s' to re-embed all frames.",
            stored_model,
            current_model,
            current_model,
        )


# Global state for the app (initialized in lifespan)
_state: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize DB, store, retriever, llm client on startup."""
    # App loggers (scheduler housekeeping, consolidation, episode top-up) emit
    # at INFO — without a root handler those logs vanish. Uvicorn configures
    # only its own loggers.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    db_path = settings.database_path
    # Ensure parent dir exists
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    await init_db(db_path)

    await _check_embedding_model_mismatch(db_path)

    store = MemoryStore(db_path)
    working_memory = WorkingMemory(
        db_path=db_path,
        max_size=settings.working_memory_max_size,
        boost=settings.working_memory_boost,
    )
    llm_client = OllamaClient(
        base_url=settings.ollama_url,
        chat_model=settings.chat_model,
        utility_model=settings.utility_model,
        embedding_model=settings.embedding_model,
        coder_model=settings.coder_model,
        verify_tls=settings.ollama_tls_cert if settings.ollama_tls_cert else True,
        chat_num_ctx=settings.chat_num_ctx,
        utility_num_ctx=settings.utility_num_ctx,
        timeout=settings.ollama_timeout,
        keep_alive=settings.ollama_keep_alive,
    )
    retriever = Retriever(
        store=store,
        llm_client=llm_client,
        embedding_model=settings.embedding_model,
        working_memory=working_memory,
    )
    search_tool = WebSearchTool(
        base_url=settings.search_base_url,
        enabled=True,    # Always enabled - core requirement
    )
    orchestrator = Orchestrator(
        deps=_OrchestratorDeps(
            store=store,
            retriever=retriever,
            llm_client=llm_client,
            search_tool=search_tool,
        )
    )

    _state["store"] = store
    _state["working_memory"] = working_memory
    _state["llm_client"] = llm_client
    _state["retriever"] = retriever
    _state["orchestrator"] = orchestrator
    _state["search_tool"] = search_tool

    scheduler_task = None
    if settings.scheduler_enabled:
        import signal

        from assistant.backend.scheduler.runner import _signal_handler

        try:
            signal.signal(signal.SIGTERM, _signal_handler)
            signal.signal(signal.SIGINT, _signal_handler)
        except ValueError:
            pass  # Not in main thread (e.g., test environments)

        from assistant.backend.scheduler.runner import start_scheduler

        scheduler_task = asyncio.create_task(start_scheduler(store, orchestrator))
        logger.info("Scheduler background task started")

    logger.info(
        "Assistant started. DB: %s, Ollama: %s, Search: enabled",
        db_path,
        settings.ollama_url,
    )

    yield

    if scheduler_task:
        scheduler_task.cancel()
        try:
            await scheduler_task
        except asyncio.CancelledError:
            pass
        logger.info("Scheduler background task stopped")

    await llm_client.close()
    await search_tool.close()
    _state.clear()


app = FastAPI(
    title="Cognitive Digital Assistant",
    description=(
        "Privacy-first assistant with frame/slot memory, learns from every conversation."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

# Mount static files for the web UI
_static_path = Path(__file__).parent / "static"
_static_path.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(_static_path)), name="static")


def get_store() -> MemoryStore:
    return _state["store"]


def get_orchestrator() -> Orchestrator:
    return _state["orchestrator"]


# Web UI
@app.get("/chat-ui")
async def chat_ui():
    """Serve the web chat interface."""
    from fastapi.responses import FileResponse
    index_path = Path(__file__).parent / "static" / "chat.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    raise HTTPException(status_code=404, detail="chat.html not found")


@app.get("/assistant/name")
async def get_assistant_name(store: MemoryStore = _Depends(get_store)):
    """Return the assistant's own name, from the identity_name frame if set."""
    from .pipeline.extractor import IDENTITY_FRAME, IDENTITY_NAME_SLOT

    name_frame = await store.get_frame_by_name(IDENTITY_FRAME)
    if name_frame:
        slot = await store.get_slot(name_frame.id, IDENTITY_NAME_SLOT)
        if slot:
            return {"name": slot.value}
    return {"name": settings.assistant_name}


@app.get("/brain-ui")
async def brain_ui():
    """Serve the brain visualization interface."""
    from fastapi.responses import FileResponse
    index_path = Path(__file__).parent / "static" / "brain.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    raise HTTPException(status_code=404, detail="brain.html not found")


# Health
@app.get("/health")
async def health():
    """Health check. Verifies Ollama is reachable and reports the model fleet."""
    llm = _state.get("llm_client")
    ollama_ok = await llm.health_check() if llm else False
    thinking_supported = False
    if llm and ollama_ok:
        try:
            thinking_supported = await llm.supports_thinking(settings.chat_model)
        except Exception:  # pragma: no cover - probe must never break health
            thinking_supported = False
    return {
        "status": "ok",
        "ollama_reachable": ollama_ok,
        "models": {
            "chat": settings.chat_model,
            "utility": settings.utility_model,
            "embedding": settings.embedding_model,
            "coder": settings.coder_model or settings.chat_model,
        },
        "thinking_supported": thinking_supported,
    }


# Search
@app.get("/search")
async def search_frames(
    q: str,
    limit: int = 10,
    min_similarity: float = 0.3,
    store: MemoryStore = _Depends(get_store),
    orch: Orchestrator = _Depends(get_orchestrator),
):
    """Search frames by similarity.

    Returns frames whose embeddings are similar to the query.
    Results include frame details, slots, and similarity score.
    """
    if not q.strip():
        raise HTTPException(status_code=400, detail="Query cannot be empty")

    # Embed the query
    query_response = await orch.llm_client.embed(q)
    
    # Search using sqlite-vec
    results = await store.search_similar_frames(
        embedding=query_response.embedding,
        user_id=0,   # not user-specific in this endpoint
        limit=limit,
        min_distance=1.0 - min_similarity,
     )
    
    return {
        "query": q,
        "results": [
            {
                "frame_id": frame.id,
                "name": frame.name,
                "type": frame.type,
                "confidence": frame.confidence,
                "slots": [
                    {"key": s.key, "value": s.value, "confidence": s.confidence}
                    for s in slots
                ],
                "similarity": round(similarity, 3),
            }
            for frame, slots, similarity in results
        ],
        "total_found": len(results),
    }


# Chat

# Live stage progress for in-flight turns (transparency in the chat UI).
# Keyed by client-supplied turn_id; small, pruned, best-effort only.
_turn_progress: dict[str, dict] = {}
_TURN_PROGRESS_MAX = 200


def _prune_turn_progress() -> None:
    if len(_turn_progress) <= _TURN_PROGRESS_MAX:
        return
    now = time.time()
    for tid in [
        t
        for t, v in _turn_progress.items()
        if v.get("done") or now - v.get("started_at", 0) > 600
    ]:
        _turn_progress.pop(tid, None)


@app.post("/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest, orch: Orchestrator = _Depends(get_orchestrator)
):
    """Send a message to the assistant. Returns the response with trace info."""
    turn_id = request.turn_id
    if turn_id:
        _prune_turn_progress()
        _turn_progress[turn_id] = {
            "stage": "queued",
            "detail": "getting started",
            "started_at": time.time(),
            "done": False,
        }

        async def progress(stage: str, detail: str) -> None:
            entry = _turn_progress.get(turn_id)
            if entry is not None:
                entry["stage"] = stage
                entry["detail"] = detail

        try:
            return await orch.chat(request, progress=progress)
        finally:
            entry = _turn_progress.get(turn_id)
            if entry is not None:
                entry["done"] = True
    return await orch.chat(request)


@app.get("/chat/status/{turn_id}")
async def chat_status(turn_id: str):
    """Live pipeline stage for an in-flight turn (polled by the chat UI)."""
    entry = _turn_progress.get(turn_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Unknown turn_id")
    return {
        "stage": entry["stage"],
        "detail": entry["detail"],
        "elapsed_s": round(time.time() - entry["started_at"], 1),
        "done": entry["done"],
    }


# Voice transcription
@app.post("/transcribe")
async def transcribe(file: UploadFile = None):
    """Transcribe an audio blob using local Whisper.

    Accepts multipart form upload with 'file' field.
    Returns {"text": "transcribed content"}.
    """
    if file is None:
        raise HTTPException(status_code=400, detail="No audio data provided")

    import tempfile
    from pathlib import Path

    from assistant.backend.pipeline.whisper import transcribe_audio

    suffix = Path(file.filename).suffix if file.filename else ".webm"
    logger.info(
        "Transcription request received: filename=%s content_type=%s",
        file.filename,
        file.content_type,
    )
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = Path(tmp.name)
    logger.info("Saved audio to %s (%d bytes)", tmp_path, len(content))

    try:
        text = await transcribe_audio(tmp_path)
        logger.info("Transcription result: %r", text)
        return {"text": text.strip()}
    except Exception as e:
        logger.error("Transcription failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Transcription failed: {e}") from e
    finally:
        tmp_path.unlink(missing_ok=True)


# Link preview
@app.get("/og-preview")
async def get_og_preview(url: str):
    """Fetch Open Graph metadata for a URL.

    Returns a PreviewCard with og:title, og:description, og:image, og:site_name.
    Results are cached in-memory for 1 hour.
    SSRF-protected: blocks private/internal hosts.
    """
    from assistant.backend.pipeline.og_preview import fetch_og_preview

    if not url:
        raise HTTPException(status_code=400, detail="url query param required")

    card = await fetch_og_preview(url)
    if card is None:
        return {"url": url, "found": False}
    return {
        "url": card.url,
        "found": True,
        "title": card.title,
        "description": card.description,
        "image": card.image,
        "site_name": card.site_name,
    }


# Users
@app.post("/users", response_model=User)
async def create_user(name: str, store: MemoryStore = _Depends(get_store)):
    """Create a new household user."""
    existing = await store.get_user_by_name(name)
    if existing:
        raise HTTPException(status_code=409, detail=f"User '{name}' already exists")
    return await store.create_user(name)


@app.get("/users", response_model=list[User])
async def list_users(store: MemoryStore = _Depends(get_store)):
    return await store.list_users()


@app.get("/users/{user_id}", response_model=User)
async def get_user(user_id: int, store: MemoryStore = _Depends(get_store)):
    user = await store.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


# Memory introspection
@app.get("/memory/frames", response_model=list[Frame])
async def list_frames(type: str | None = None, store: MemoryStore = _Depends(get_store)):
    """List all frames in memory. Optionally filter by type."""
    return await store.list_frames(type=type)


@app.get("/memory/frames/{frame_id}", response_model=Frame)
async def get_frame(frame_id: int, store: MemoryStore = _Depends(get_store)):
    frame = await store.get_frame(frame_id)
    if not frame:
        raise HTTPException(status_code=404, detail="Frame not found")
    return frame


@app.get("/memory/frames/{frame_id}/slots", response_model=list[Slot])
async def get_frame_slots(frame_id: int, store: MemoryStore = _Depends(get_store)):
    return await store.get_slots_for_frame(frame_id)


@app.get("/memory/frames/by-name/{name}", response_model=Frame)
async def get_frame_by_name(name: str, store: MemoryStore = _Depends(get_store)):
    frame = await store.get_frame_by_name(name)
    if not frame:
        raise HTTPException(status_code=404, detail=f"No frame named '{name}'")
    return frame


@app.get("/memory/associations", response_model=list[Association])
async def list_associations(store: MemoryStore = _Depends(get_store)):
    """List all associations (edges) for the brain graph."""
    return await store.get_all_associations()


@app.get("/memory/frames/{frame_id}/associations", response_model=list[Association])
async def get_frame_associations(frame_id: int, store: MemoryStore = _Depends(get_store)):
    """Get all associations (incoming + outgoing) for a specific frame."""
    return await store.get_all_associations_for_frame(frame_id)


@app.get("/memory/conflicts", response_model=list[Conflict])
async def list_conflicts(status: str | None = None, store: MemoryStore = _Depends(get_store)):
    return await store.get_conflicts(status=status)


@app.post("/memory/conflicts/{conflict_id}/resolve")
async def resolve_conflict(
    conflict_id: int, value: str, store: MemoryStore = _Depends(get_store)
):
    """Manually resolve a conflict by setting the slot to a specific value."""
    slot = await store.manual_override_conflict(conflict_id, value)
    return {"status": "resolved", "slot": slot}


@app.post("/memory/frames/{frame_id}/forget")
async def forget_frame(
    frame_id: int, store: MemoryStore = _Depends(get_store)
):
    """Soft-delete a frame by setting priority to 0.

    Essential frames cannot be forgotten. Use 'list frames' to see which frames are forgotten.
    """
    frame = await store.get_frame(frame_id)
    if not frame:
        raise HTTPException(status_code=404, detail="Frame not found")
    if frame.essential:
        raise HTTPException(
            status_code=409,
            detail="Essential frames cannot be forgotten. Remove essential flag first.",
        )
    forgotten = await store.forget_frame(frame_id)
    return {"status": "forgotten", "frame": forgotten}


@app.post("/memory/slots/{slot_id}/forget")
async def forget_slot(
    slot_id: int, store: MemoryStore = _Depends(get_store)
):
    """Soft-delete a slot by setting priority to 0.

    Essential slots cannot be forgotten.
    """
    slot = await store.get_slot_by_id(slot_id)
    if not slot:
        raise HTTPException(status_code=404, detail="Slot not found")
    if slot.essential:
        raise HTTPException(
            status_code=409,
            detail="Essential slots cannot be forgotten.",
        )
    forgotten = await store.forget_slot(slot_id)
    return {"status": "forgotten", "slot": forgotten}


# Topic transparency search (Brain Observatory)
class TopicMatch(BaseModel):
    frame: Frame
    slots: list[Slot]
    # Semantic similarity 0..1; None when the match came from keyword fallback
    similarity: float | None = None
    associations: list[Association] = []
    episodes: list[Episode] = []
    conflicts: list[Conflict] = []


class TopicSearchResponse(BaseModel):
    query: str
    # False when embedding failed (Ollama down) and only keyword matching ran
    semantic_search: bool
    # Handler revision, for verifying deploys of ranking changes
    backend_rev: int = 2
    matches: list[TopicMatch]


@app.get("/memory/search", response_model=TopicSearchResponse)
async def memory_search(
    q: str,
    limit: int = 8,
    store: MemoryStore = _Depends(get_store),
):
    """Search memory for everything known about a topic.

    Semantic (embedding) search over frames, unioned with a keyword pass over
    frame names and slot keys/values. Each match carries its full slot list,
    associations, episodes that touched it, and pending conflicts — the point
    is transparency: see what the agent stored about a topic, with what
    confidence, and what it never captured.
    """
    q = q.strip()
    if not q:
        raise HTTPException(status_code=400, detail="Empty query")

    matches: dict[int, TopicMatch] = {}
    semantic_ok = True
    try:
        llm_client: OllamaClient | None = _state.get("llm_client")
        embed_response = await llm_client.embed(q)
        results = await store.search_similar_frames(
            embedding=embed_response.embedding,
            user_id=None,  # observatory view: all users' + shared frames
            embedding_model=settings.embedding_model,
            limit=limit * 2,
            min_distance=settings.retrieval_min_distance,
        )
        # search_similar_frames returns SIMILARITY (1 - cosine distance,
        # higher = better) — clamp, don't invert.
        for frame, slots, similarity in results:
            matches[frame.id] = TopicMatch(
                frame=frame,
                slots=slots,
                similarity=max(0.0, min(1.0, similarity)),
            )
    except Exception as e:
        logger.warning("Topic semantic search unavailable (%s); keyword-only", e)
        semantic_ok = False

    # Keyword pass always runs: exact names/acronyms embeddings blur, and the
    # only signal when the embedding model is unreachable. Lexical matches are
    # blended INTO the semantic ranking (0.55 + 0.4·coverage) so an exact
    # multi-word topic hit outranks the flat ~0.5 fuzzy band instead of being
    # buried beneath it.
    scores = merge_match_scores(
        {mid: m.similarity or 0.0 for mid, m in matches.items()},
        [
            (kf.id, strength)
            for kf, strength in await store.search_frames_lexical(q, limit=limit)
        ],
    )
    for frame_id, score in scores.items():
        existing = matches.get(frame_id)
        if existing is not None and (existing.similarity or 0.0) >= score:
            continue
        frame = existing.frame if existing else await store.get_frame(frame_id)
        if frame is None:
            continue
        matches[frame_id] = TopicMatch(
            frame=frame,
            slots=await store.get_slots_for_frame(frame_id),
            similarity=score,
        )

    logger.info("Topic search %r: %d matches after lexical blend", q, len(matches))
    ranked = sorted(
        matches.values(),
        key=lambda m: (m.similarity is not None, m.similarity or 0.0),
        reverse=True,
    )[:limit]


    pending_conflicts = [
        c for c in await store.get_conflicts(status="pending") if c.frame_id
    ]
    conflicts_by_frame: dict[int, list[Conflict]] = {}
    for c in pending_conflicts:
        conflicts_by_frame.setdefault(c.frame_id, []).append(c)

    for m in ranked:
        m.associations = await store.get_all_associations_for_frame(m.frame.id)
        m.episodes = await store.get_episodes_for_frames([m.frame.id], limit=6)
        m.conflicts = conflicts_by_frame.get(m.frame.id, [])

    return TopicSearchResponse(
        query=q, semantic_search=semantic_ok, backend_rev=2, matches=ranked
    )


# Episodes (for debugging / inspection)
@app.get("/users/{user_id}/episodes", response_model=list[Episode])
async def get_user_episodes(
    user_id: int, limit: int = 50, store: MemoryStore = _Depends(get_store)
):
    return await store.get_episodes_for_user(user_id, limit=limit)


class SessionMessage(BaseModel):
    role: str
    content: str
    timestamp: str | None = None


@app.get("/chat/session/{session_id}/messages", response_model=list[SessionMessage])
async def get_session_messages(
    session_id: str,
    user_id: int,
    limit: int = 50,
    store: MemoryStore = _Depends(get_store),
):
    """Conversation turns for a session, oldest first — chat UI restore.

    user_id is required and filters the episodes so one household member
    cannot replay another member's session by guessing the session id.
    """
    episodes = [
        e
        for e in await store.get_episodes_for_session(session_id)
        if e.user_id == user_id
    ]
    return [
        SessionMessage(role=e.role, content=e.content, timestamp=e.timestamp)
        for e in episodes[-limit:]
    ]


# DB backup / restore (CLI container cannot access the DB file directly)
@app.post("/db/backup")
async def db_backup(store: MemoryStore = _Depends(get_store)):
    """Create a backup of the SQLite DB in the data volume.

    Returns the backup filename. The file lives in /app/data/ inside the
    backend container (the assistant-data volume). To retrieve it, the user
    can `docker cp` it out, or mount the data dir to the host.
    """
    db_path = Path(store.db_path)
    if not db_path.exists():
        raise HTTPException(status_code=500, detail=f"DB not found at {db_path}")

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_name = f"backup-{timestamp}.db"
    backup_path = db_path.parent / backup_name

    src = connect(str(db_path))
    dst = connect(str(backup_path))
    with dst:
        src.backup(dst)
    src.close()
    dst.close()

    return {
        "status": "ok",
        "backup_filename": backup_name,
        "backup_path": str(backup_path),
        "db_size_bytes": db_path.stat().st_size,
        "backup_size_bytes": backup_path.stat().st_size,
    }


@app.post("/db/restore")
async def db_restore(backup_filename: str, store: MemoryStore = _Depends(get_store)):
    """Restore the SQLite DB from a backup file in the data volume.

    WARNING: This overwrites the current DB. The backend should be stopped
    first (or accept that in-flight requests may fail).
    """
    db_path = Path(store.db_path)
    data_dir = db_path.parent.resolve()
    backup_path = (data_dir / backup_filename).resolve()

    # Safety check: refuse to restore a file that's not in the data dir
    try:
        backup_path.relative_to(data_dir)
    except ValueError as _err:
        raise HTTPException(
            status_code=400, detail="Backup path is outside the data directory"
        ) from _err

    if not backup_path.exists():
        raise HTTPException(status_code=404, detail=f"Backup not found: {backup_filename}")

    shutil.copy2(backup_path, db_path)
    return {
        "status": "ok",
        "restored_from": backup_filename,
        "db_path": str(db_path),
    }


@app.get("/db/backups")
async def list_backups(store: MemoryStore = _Depends(get_store)):
    """List available backup files in the data volume."""
    db_path = Path(store.db_path)
    data_dir = db_path.parent
    backups = []
    for f in sorted(data_dir.glob("backup-*.db")):
        backups.append({
            "filename": f.name,
            "size_bytes": f.stat().st_size,
            "created_at": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
        })
    return {"backups": backups}


# Feedback


class FeedbackRequest(BaseModel):
    episode_id: str | None = None
    message_id: str
    kind: str
    comment: str | None = None


class FeedbackResponse(BaseModel):
    status: str
    feedback: Feedback
    slots_updated: int = 0


@app.post("/feedback", response_model=FeedbackResponse)
async def submit_feedback(
    request: FeedbackRequest, store: MemoryStore = _Depends(get_store)
):
    """Submit feedback (reaction or correction) for an assistant message.

    - positive: boosts confidence of touched frames/slots
    - negative: lowers confidence of touched frames/slots
    - correction: records the correction text; apply it via POST /correction,
      which routes through the LLM correction pipeline
    """
    valid_kinds = {"positive", "negative", "correction"}
    if request.kind not in valid_kinds:
        raise HTTPException(status_code=400, detail=f"kind must be one of {valid_kinds}")

    feedback = await store.create_feedback(
        episode_id=request.episode_id,
        message_id=request.message_id,
        kind=request.kind,
        comment=request.comment,
    )

    slots_updated = 0
    if request.kind == "positive":
        slots_updated = await store.apply_positive_feedback(request.episode_id)
    elif request.kind == "negative":
        slots_updated = await store.apply_negative_feedback(request.episode_id)

    return FeedbackResponse(
        status="ok",
        feedback=feedback,
        slots_updated=slots_updated,
    )


class CorrectionRequest(BaseModel):
    message_id: str
    episode_id: str | None = None
    correction_text: str


class CorrectionResponse(BaseModel):
    status: str
    slots_corrected: int = 0
    frame_name: str | None = None
    slot_key: str | None = None
    new_value: str | None = None
    conflict: bool = False
    validation_summary: str | None = None


@app.post("/correction", response_model=CorrectionResponse)
async def submit_correction(
    request: CorrectionRequest,
    store: MemoryStore = _Depends(get_store),
):
    """Submit a natural-language correction.

    Routes through the LLM-based correction pipeline:
    1. extract_correction — parse {frame_name, slot_key, new_value} from free text
    2. validate_correction — optionally corroborate with web search
    3. apply_correction — upsert the slot with high reliability

    The user's raw correction text is stored as a feedback record for audit.
    """
    correction_text = request.correction_text.strip()
    if not correction_text:
        raise HTTPException(status_code=400, detail="correction_text is required")

    llm_client: OllamaClient = _state["llm_client"]
    search_tool: WebSearchTool = _state["search_tool"]

    source_episode_id = None
    if request.episode_id:
        episodes = await store.get_episodes_for_session(request.episode_id)
        if episodes:
            source_episode_id = episodes[-1].id

    await store.create_feedback(
        episode_id=request.episode_id,
        message_id=request.message_id,
        kind="correction",
        comment=correction_text,
    )

    correction: CorrectionResult | None = await extract_correction(
        correction_text, llm_client
    )

    if not correction or not correction.frame_name:
        return CorrectionResponse(
            status="Could not understand correction. Try rephrasing.",
            slots_corrected=0,
        )

    validation_summary = None
    conflict = False

    if correction and correction.slot_key and correction.new_value:
        frame = await store.get_frame_by_name(correction.frame_name)
        current_value = None
        if frame:
            slot = await store.get_slot(frame.id, correction.slot_key)
            if slot:
                current_value = slot.value

        if search_tool and search_tool.enabled:
            validation = await validate_correction(
                correction, current_value, store, search_tool, llm_client
            )
            validation_summary = validation.summary
            if validation.contradicted:
                return CorrectionResponse(
                    status="Correction contradicted by web search. Not applied.",
                    slots_corrected=0,
                    validation_summary=validation_summary,
                )

        result = await apply_correction(correction, store, source_episode_id)
        slots_corrected = result.get("slots_corrected", 0)
        conflict = result.get("conflict", False)

        conversational_status = (
            f"Got it — updated {correction.frame_name}.{correction.slot_key} to "
            f"'{correction.new_value}'."
            if slots_corrected > 0
            else "Correction applied but no slots were updated."
        )
        if conflict:
            conversational_status += " (Auto-resolved a conflict.)"

        return CorrectionResponse(
            status=conversational_status,
            slots_corrected=slots_corrected,
            frame_name=correction.frame_name,
            slot_key=correction.slot_key,
            new_value=correction.new_value,
            conflict=conflict,
            validation_summary=validation_summary,
        )

    return CorrectionResponse(
        status="Correction parsed but missing required fields.",
        slots_corrected=0,
    )


# Brain export / import


class BrainImportRequest(BaseModel):
    brain_json: dict
    mode: str = "merge"  # "merge" | "overwrite"


class BrainImportResponse(BaseModel):
    status: str
    imported: dict


class ScheduledTaskResponse(BaseModel):
    id: int
    name: str
    description: str
    schedule_cron: str
    prompt: str
    enabled: bool
    last_run: str | None
    next_run: str | None
    last_result_summary: str | None
    source_type: str | None
    created_at: str | None


@app.get("/tasks", response_model=list[ScheduledTaskResponse])
async def list_tasks(
    user_id: int | None = None,
    store: MemoryStore = _Depends(get_store),
):
    """List all scheduled tasks, optionally filtered by user_id.

    System tasks are excluded unless user_id is omitted (admin view).
    """
    tasks = await store.get_scheduled_tasks(owner_user_id=user_id)
    return [
        ScheduledTaskResponse(
            id=t["id"],
            name=t["name"],
            description=t.get("description", ""),
            schedule_cron=t.get("schedule_cron", ""),
            prompt=t.get("prompt", ""),
            enabled=bool(t["enabled"]),
            last_run=t.get("last_run"),
            next_run=t.get("next_run"),
            last_result_summary=t.get("last_result_summary"),
            source_type=t.get("source_type"),
            created_at=t.get("created_at"),
        )
        for t in tasks
    ]


@app.delete("/tasks/{task_id}")
async def delete_task(
    task_id: int,
    store: MemoryStore = _Depends(get_store),
):
    """Soft-delete a scheduled task."""
    await store.delete_scheduled_task(task_id)
    return {"status": "ok"}


@app.get("/tasks/{task_id}/result")
async def get_task_result(
    task_id: int,
    store: MemoryStore = _Depends(get_store),
):
    """Get the last result from a scheduled task."""
    tasks = await store.get_scheduled_tasks(include_system=True)
    for t in tasks:
        if t["id"] == task_id:
            return {
                "last_run": t.get("last_run"),
                "last_result_summary": t.get("last_result_summary"),
            }
    raise HTTPException(status_code=404, detail="Task not found")


@app.post("/brain/export")
async def export_brain(store: MemoryStore = _Depends(get_store)):
    """Export the full brain memory to a portable JSON file.

    Returns JSON with: version, exported_at, frames (with slots), associations,
    episodes, feedbacks, conflicts. Embeddings are NOT included.
    """
    brain = await store.export_brain()
    return brain


@app.post("/brain/import", response_model=BrainImportResponse)
async def import_brain(
    request: BrainImportRequest,
    store: MemoryStore = _Depends(get_store),
):
    """Import a brain JSON export.

    Modes:
    - merge: upsert frames by name, add slots (existing data preserved)
    - overwrite: delete all existing memory, then re-import (creates backup first)

    Returns counts of imported items and backup path if overwrite mode was used.
    """
    if request.mode not in ("merge", "overwrite"):
        raise HTTPException(status_code=400, detail="mode must be 'merge' or 'overwrite'")

    try:
        imported = await store.import_brain(request.brain_json, mode=request.mode)
        return BrainImportResponse(
            status="ok",
            imported=imported,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
