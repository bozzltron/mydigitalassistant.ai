import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime
from enum import Enum
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import Depends as _Depends
from fastapi import FastAPI, File, HTTPException, Query, Response, UploadFile
from fastapi.responses import FileResponse
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
from assistant.backend.pipeline.llm_client import ChatMessage, OllamaClient
from assistant.backend.pipeline.orchestrator import ChatRequest, ChatResponse, Orchestrator
from assistant.backend.pipeline.orchestrator import OrchestratorDeps as _OrchestratorDeps
from assistant.backend.pipeline.search import WebSearchTool
from assistant.backend.pipeline.url_safety import (
    UnsafeURLError,
    assert_public_url,
    safe_stream,
)

logger = logging.getLogger(__name__)


async def _check_embedding_model_mismatch(db_path: str) -> None:
    """Warn when the stored vectors were not produced by the configured model.

    Two independent questions, because either one alone is misleading:

    1. Do the *labels* in frame_embeddings/episode_embeddings match the
       configured model?  A vector is only useful if it can be found, and every
       search filters by label.
    2. Do live frames actually *have* a vector under the configured label?  This
       is the one that matters and the one that was missed: the metadata key said
       the right model, so nothing warned, while 1894 of 1895 frames had no
       vector the retriever could ever find. The metadata key is a claim; this is
       the measurement.
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

    from assistant.backend.db.sqlcipher import aiosqlite_connect

    try:
        async with aiosqlite_connect(db_path) as db:
            frame_labels = await db.execute_fetchall(
                "SELECT embedding_model, COUNT(*) FROM frame_embeddings "
                "GROUP BY embedding_model ORDER BY 2 DESC"
            )
            # execute_fetchall, not execute_fetchone: the SQLCipher wrapper only
            # forwards part of the aiosqlite Connection surface, and the missing
            # method raised inside the try below, turning the whole audit into a
            # debug line. Same reason this returns quietly rather than loudly --
            # a diagnostic must not be able to stop the assistant from starting.
            unembedded = await db.execute_fetchall(
                "SELECT COUNT(*) FROM frames f "
                "WHERE f.deleted_at IS NULL "
                "AND NOT EXISTS ("
                "  SELECT 1 FROM frame_embeddings fe"
                "  WHERE fe.frame_id = f.id AND fe.embedding_model = ?"
                ")",
                (current_model,),
            )
    except Exception as exc:
        logger.warning("Embedding model audit could not run: %s", exc)
        return

    if not frame_labels:
        return

    labels = ", ".join(f"{model}={count}" for model, count in frame_labels)
    missing = unembedded[0][0] if unembedded else 0

    if len(frame_labels) > 1 and missing:
        # Only a warning when it strands something. `reembed` deliberately keeps
        # the previous model's vectors, so a completed migration always leaves two
        # labels behind; warning about that unconditionally would fire on every
        # boot forever and teach the reader to skip this line. The condition that
        # actually hurts is a live frame reachable only under a stale label.
        logger.warning(
            "frame_embeddings holds vectors from more than one model (%s), and %d "
            "live frames have none under '%s'. Searches filter on embedding_model, "
            "so those frames cannot be found at all. Run "
            "'assistant db reembed --model %s'.",
            labels,
            missing,
            current_model,
            current_model,
        )
    elif len(frame_labels) > 1:
        # Stale labels, but every live frame is covered by the configured model:
        # the old vectors are dead weight, not a defect. Worth knowing, not worth
        # interrupting anyone about.
        logger.info(
            "frame_embeddings also holds %d vectors under other labels (%s); every "
            "live frame is covered by '%s', so retrieval is unaffected. Safe to prune.",
            sum(count for model, count in frame_labels if model != current_model),
            labels,
            current_model,
        )

    if missing:
        logger.warning(
            "%d live frames have no '%s' embedding and are therefore "
            "unretrievable by similarity. Run 'assistant db reembed --model %s'. "
            "Stored vectors: %s",
            missing,
            current_model,
            current_model,
            labels,
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
    migrated = await store.migrate_scheduled_tasks_to_slots()
    if migrated:
        logger.info("Migrated %d scheduled tasks to slots", migrated)
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
        math_model=settings.math_model,
        math_num_ctx=settings.math_num_ctx,
        math_keep_alive=settings.math_keep_alive,
        verify_tls=settings.ollama_tls_cert if settings.ollama_tls_cert else True,
        chat_num_ctx=settings.chat_num_ctx,
        utility_num_ctx=settings.utility_num_ctx,
        timeout=settings.ollama_timeout,
        keep_alive=settings.ollama_keep_alive,
        tools_model=settings.tools_model,
        tools_num_ctx=settings.tools_num_ctx,
        tools_keep_alive=settings.tools_keep_alive,
        max_model=settings.max_model,
        max_num_ctx=settings.max_num_ctx,
        max_keep_alive=settings.max_keep_alive,
    )
    retriever = Retriever(
        store=store,
        llm_client=llm_client,
        embedding_model=settings.embedding_model,
        working_memory=working_memory,
    )
    search_tool = WebSearchTool(
        base_url=settings.search_base_url,
        enabled=True,
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

    # Initialize tool executor with store and embed function for recall tool
    from assistant.backend.pipeline.tool_executor import init_store
    try:
        init_store(
            db_path,
            embed_fn=orchestrator.embed_fn(),
            embedding_model=orchestrator.llm_client.embedding_model,
            search_tool=search_tool,
            orchestrator=orchestrator,
        )
        logger.info("Tool executor initialized successfully")
    except Exception as e:
        logger.error("Failed to initialize tool executor: %s", e, exc_info=True)

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
    # Before the loop that owns them goes away, or the pooled connections'
    # worker threads would hold up interpreter shutdown.
    await store.close()
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
app.mount("/assets", StaticFiles(directory=str(_static_path / "assets")), name="assets")


def get_store() -> MemoryStore:
    return _state["store"]


def get_orchestrator() -> Orchestrator:
    return _state["orchestrator"]


# Web UI
@app.get("/chat-ui")
async def chat_ui():
    """Serve the web chat interface (SolidJS SPA)."""
    from fastapi.responses import FileResponse
    index_path = Path(__file__).parent / "static" / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    raise HTTPException(status_code=404, detail="index.html not found")


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
    """Serve the brain visualization interface (SolidJS SPA)."""
    from fastapi.responses import FileResponse
    index_path = Path(__file__).parent / "static" / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    raise HTTPException(status_code=404, detail="index.html not found")


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
            "max": settings.max_model or settings.chat_model,
        },
        "thinking_supported": thinking_supported,
    }


@app.get("/settings")
async def get_settings() -> dict:
    """Return frontend-facing settings so the UI can show/hide the Brave toggle."""
    return {
        "brave_enabled": settings.brave_enabled,
        "brave_configured": bool(settings.brave_api_key),
    }


# Search
@app.get("/search")
async def search_frames(
    q: str,
    limit: int = Query(default=10, ge=1, le=100),
    min_similarity: float | None = None,
    min_relevance: float | None = None,
    store: MemoryStore = _Depends(get_store),
    orch: Orchestrator = _Depends(get_orchestrator),
):
    """Search frames by similarity.

    Returns frames whose embeddings are similar to the query.
    Results include frame details, slots, and similarity score.

    The threshold is accepted under both spellings: ``min_relevance`` (what the
    frontend and the retriever call it) and ``min_similarity`` (this endpoint's
    historical name). Accepting only one meant a client sending the other got the
    0.3 default silently.
    """
    threshold = min_relevance if min_relevance is not None else min_similarity
    if threshold is None:
        threshold = 0.3

    if not q.strip():
        raise HTTPException(status_code=400, detail="Query cannot be empty")

    # Embed the query
    query_response = await orch.llm_client.embed(q)

    # Search using sqlite-vec
    results = await store.search_similar_frames(
        embedding=query_response.embedding,
        user_id=0,   # not user-specific in this endpoint
        embedding_model=settings.embedding_model,
        limit=limit,
        min_distance=1.0 - threshold,
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
    request: ChatRequest,
    orch: Orchestrator = _Depends(get_orchestrator),
    store: MemoryStore = _Depends(get_store),
):
    """Send a message to the assistant. Returns the response with trace info.

    Supports JSON body {user_id, message, session_id, turn_id, attached_files}.
    """
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

    # Process attached files: upload to memory so agent can use read_file tool
    uploaded_files = []
    file_contents = []
    if request.attached_files:
        for fc in request.attached_files:
            filename = fc.get("name", "unknown")
            ext = fc.get("ext", "txt")
            text_content = fc.get("text", "")
            content_bytes = text_content.encode("utf-8")
            
            # Validate file type
            allowed_types = {"txt", "csv", "json", "xml", "html", "ics"}
            if ext not in allowed_types:
                # Skip unsupported files but log
                logger.warning(f"Skipping unsupported file type: .{ext}")
                continue
            
            # Size limit (10MB)
            if len(content_bytes) > 10_000_000:
                logger.warning(f"Skipping file too large: {filename}")
                continue
            
            # Upload file to memory and disk
            try:
                upload_result = await upload_file_to_memory(
                    filename=filename,
                    content=content_bytes,
                    ext=ext,
                    store=store,
                    user_id=request.user_id,
                )
                uploaded_files.append({
                    "frame_id": upload_result["frame_id"],
                    "frame_name": upload_result["frame_name"],
                    "file_name": upload_result["file_name"],
                    "file_ext": upload_result["file_ext"],
                })
            except Exception as e:
                logger.error(f"Failed to upload attached file {filename}: {e}")
                continue
            
            # Also keep the content for message context
            file_contents.append({
                "name": filename,
                "ext": ext,
                "preview": fc.get("preview", "")[:500],
                "text": text_content,
                "key_entities": fc.get("key_entities", []),
                "open_questions": fc.get("open_questions", []),
            })

    # Build the enhanced message with file context
    enhanced_message = request.message
    if file_contents:
        file_summaries = []
        for i, fc in enumerate(file_contents):
            entities_str = (
                ", ".join(fc.get("key_entities", [])[:3]) if fc.get("key_entities") else ""
            )
            questions_str = (
                " ".join(fc.get("open_questions", [])[:2]) if fc.get("open_questions") else ""
            )
            # Include frame info so agent can use read_file tool
            frame_info = ""
            if i < len(uploaded_files):
                uf = uploaded_files[i]
                frame_info = f" [frame_id: {uf['frame_id']}, frame_name: {uf['frame_name']}]"
            file_summaries.append(f"File: {fc['name']} ({fc['ext']}) - {fc['preview']}{frame_info}")
            if entities_str:
                file_summaries[-1] += f" [entities: {entities_str}]"
            if questions_str:
                file_summaries[-1] += f" [questions: {questions_str}]"
        if request.message:
            enhanced_message = request.message + "\n\n" + "\n".join(file_summaries)
        else:
            enhanced_message = "\n\n" + "\n".join(file_summaries)

    try:
        # Combine file_contents with uploaded frame info for the orchestrator
        orch_attached_files = []
        for i, fc in enumerate(file_contents):
            file_info = dict(fc)
            if i < len(uploaded_files):
                file_info["frame_id"] = uploaded_files[i]["frame_id"]
                file_info["frame_name"] = uploaded_files[i]["frame_name"]
            orch_attached_files.append(file_info)
        
        return await orch.chat(
            ChatRequest(
                user_id=request.user_id,
                message=enhanced_message,
                session_id=request.session_id,
                turn_id=request.turn_id,
                attached_files=orch_attached_files,
                search_consent=request.search_consent,
                max_intelligence=request.max_intelligence,
            ),
            progress=progress,
        )
    finally:
        entry = _turn_progress.get(turn_id)
        if entry is not None:
            entry["done"] = True


@app.post("/chat/stream")
async def chat_stream(
    request: ChatRequest,
    orch: Orchestrator = _Depends(get_orchestrator),
    store: MemoryStore = _Depends(get_store),
):
    """Send a message to the assistant with streaming response (SSE).

    Returns Server-Sent Events for real-time UI updates.
    """
    from fastapi.responses import StreamingResponse

    turn_id = request.turn_id
    if turn_id:
        _prune_turn_progress()
        _turn_progress[turn_id] = {
            "stage": "queued",
            "detail": "getting started",
            "started_at": time.time(),
            "done": False,
        }

    # Stage updates ride the same SSE stream (via merge_sse) so the UI gets
    # live pipeline progress without a separate status request. The queue is
    # drained by merge_sse; the /chat/status endpoint remains as a fallback.
    from assistant.backend.pipeline.streaming import StageEvent, merge_sse, serialize_event

    stage_queue: asyncio.Queue = asyncio.Queue()

    async def progress(stage: str, detail: str) -> None:
        entry = _turn_progress.get(turn_id)
        if entry is not None:
            entry["stage"] = stage
            entry["detail"] = detail
        stage_queue.put_nowait(serialize_event(StageEvent(stage, detail)))

    # Process attached files (same as regular chat)
    uploaded_files = []
    file_contents = []
    if request.attached_files:
        for fc in request.attached_files:
            filename = fc.get("name", "unknown")
            ext = fc.get("ext", "txt")
            text_content = fc.get("text", "")
            content_bytes = text_content.encode("utf-8")

            allowed_types = {"txt", "csv", "json", "xml", "html", "ics"}
            if ext not in allowed_types:
                logger.warning(f"Skipping unsupported file type: .{ext}")
                continue

            if len(content_bytes) > 10_000_000:
                logger.warning(f"Skipping file too large: {filename}")
                continue

            try:
                upload_result = await upload_file_to_memory(
                    filename=filename,
                    content=content_bytes,
                    ext=ext,
                    store=store,
                    user_id=request.user_id,
                )
                uploaded_files.append({
                    "frame_id": upload_result["frame_id"],
                    "frame_name": upload_result["frame_name"],
                    "file_name": upload_result["file_name"],
                    "file_ext": upload_result["file_ext"],
                })
            except Exception as e:
                logger.error(f"Failed to upload attached file {filename}: {e}")
                continue

            file_contents.append({
                "name": filename,
                "ext": ext,
                "preview": fc.get("preview", "")[:500],
                "text": text_content,
                "key_entities": fc.get("key_entities", []),
                "open_questions": fc.get("open_questions", []),
            })

    # Build enhanced message
    enhanced_message = request.message
    if file_contents:
        file_summaries = []
        for i, fc in enumerate(file_contents):
            entities_str = (
                ", ".join(fc.get("key_entities", [])[:3]) if fc.get("key_entities") else ""
            )
            questions_str = (
                " ".join(fc.get("open_questions", [])[:2]) if fc.get("open_questions") else ""
            )
            frame_info = ""
            if i < len(uploaded_files):
                uf = uploaded_files[i]
                frame_info = f" [frame_id: {uf['frame_id']}, frame_name: {uf['frame_name']}]"
            file_summaries.append(f"File: {fc['name']} ({fc['ext']}) - {fc['preview']}{frame_info}")
            if entities_str:
                file_summaries[-1] += f" [entities: {entities_str}]"
            if questions_str:
                file_summaries[-1] += f" [questions: {questions_str}]"
        if request.message:
            enhanced_message = request.message + "\n\n" + "\n".join(file_summaries)
        else:
            enhanced_message = "\n\n" + "\n".join(file_summaries)

    orch_attached_files = []
    for i, fc in enumerate(file_contents):
        file_info = dict(fc)
        if i < len(uploaded_files):
            file_info["frame_id"] = uploaded_files[i]["frame_id"]
            file_info["frame_name"] = uploaded_files[i]["frame_name"]
        orch_attached_files.append(file_info)

    async def event_generator():
        try:
            async for event in merge_sse(
                orch.chat_stream(
                    ChatRequest(
                        user_id=request.user_id,
                        message=enhanced_message,
                        session_id=request.session_id,
                        turn_id=request.turn_id,
                        attached_files=orch_attached_files,
                        search_consent=request.search_consent,
                        max_intelligence=request.max_intelligence,
                    ),
                    progress=progress,
                ),
                stage_queue,
            ):
                yield event
        finally:
            entry = _turn_progress.get(turn_id)
            if entry is not None:
                entry["done"] = True

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        }
    )


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
        # DEBUG, not INFO, and the length rather than the words: a transcript is
        # the user's voice, and INFO logs land in the container log by default.
        logger.debug("Transcription result: %d chars", len(text))
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


# Search thumbnails are small; anything larger is not worth streaming through
# the backend.
_MAX_IMAGE_BYTES = 5 * 1024 * 1024
_IMAGE_PROXY_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; AssistantBot/1.0)",
    "Accept": "image/*,*/*;q=0.8",
}


def _image_proxy_headers(url: str) -> dict[str, str]:
    """Proxy headers with a same-origin Referer; some hosts hotlink-block without one."""
    headers = dict(_IMAGE_PROXY_HEADERS)
    parts = urlsplit(url)
    if parts.scheme and parts.netloc:
        headers["Referer"] = f"{parts.scheme}://{parts.netloc}/"
    return headers


@app.get("/image-proxy")
async def image_proxy(url: str):
    """Fetch a remote image server-side and return it.

    Privacy: rendering search thumbnails directly would make the browser contact
    third-party hosts, leaking the user's IP. Routing through the backend keeps
    the browser talking only to localhost. SSRF-guarded by `safe_stream` (public
    hosts only, redirects revalidated) and restricted to image content-types.

    SearXNG's own image proxy lives on an internal host, so the configured
    SearXNG base URL is trusted through the guard.
    """
    trusted = [settings.search_base_url] if settings.search_base_url else None

    try:
        await assert_public_url(url, trusted_prefixes=trusted)
    except UnsafeURLError as e:
        raise HTTPException(status_code=400, detail=f"blocked url: {e}") from e

    client = httpx.AsyncClient(
        timeout=httpx.Timeout(10.0, connect=5.0),
        follow_redirects=False,
    )
    buffer = bytearray()
    try:
        async with safe_stream(
            client, url, headers=_image_proxy_headers(url), trusted_prefixes=trusted
        ) as response:
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            if not content_type.lower().startswith("image/"):
                raise HTTPException(status_code=415, detail="not an image")
            # Buffer up to cap+1 so an oversized image is a clean 413, never a
            # truncated body the browser cannot decode.
            async for chunk in response.aiter_bytes():
                buffer.extend(chunk)
                if len(buffer) > _MAX_IMAGE_BYTES:
                    raise HTTPException(status_code=413, detail="image too large")
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("image proxy failed for %s: %s", url, e)
        raise HTTPException(status_code=502, detail="image fetch failed") from e
    finally:
        await client.aclose()

    return Response(
        content=bytes(buffer),
        media_type=content_type,
        headers={"Cache-Control": "public, max-age=3600"},
    )


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
async def list_frames(
    type: str | None = None,
    user_id: int | None = None,
    store: MemoryStore = _Depends(get_store),
):
    """List all frames in memory. Optionally filter by type and owner.

    ``user_id`` narrows to frames owned by that user; the frontend sends it, and
    the param was previously ignored (the filter silently did nothing).
    """
    return await store.list_frames(type=type, owner_user_id=user_id)


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
    # LLM-generated summary of the matched memories (only when ?summary=true)
    summary: str = ""


def _build_memories_text(matches: list[TopicMatch], query: str) -> str:
    """Build a readable text representation of matched memories for summarization."""
    lines = [f"Query: {query}", f"Total matches: {len(matches)}", ""]
    for i, m in enumerate(matches, 1):
        sim_str = f" (similarity: {m.similarity:.2f})" if m.similarity else ""
        lines.append(f"--- Memory {i}{sim_str} ---")
        lines.append(f"Name: {m.frame.name}")
        lines.append(f"Type: {m.frame.type}")
        if m.slots:
            lines.append("Slots:")
            for s in m.slots:
                lines.append(f"  {s.key}: {s.value}")
        if m.associations:
            assoc_descriptions = []
            for a in m.associations:
                assoc_descriptions.append(
                    f"{a.relation_type} (confidence: {a.confidence:.2f})"
                )
            lines.append(f"Relations: {', '.join(assoc_descriptions)}")
        if m.episodes:
            lines.append("Episodes (recent conversation excerpts):")
            for ep in m.episodes[:3]:
                snippet = ep.content[:200].replace("\n", " ")
                lines.append(f"  [{ep.role}]: {snippet}...")
        if m.conflicts:
            lines.append(f"Conflicts: {len(m.conflicts)} pending")
        lines.append("")
    return "\n".join(lines)


async def _summarize_memories(
    memories_text: str,
    query: str,
    llm_client: OllamaClient | None,
) -> str:
    """Use the utility LLM to summarize what the brain knows about the query."""
    if not llm_client:
        return "Summary unavailable (LLM not configured)."

    system_prompt = (
        "You are a concise memory analyst. Based on the memories below, "
        "write a 2-3 sentence summary of what the brain knows about the topic. "
        "Focus on key facts, relationships, and notable details. "
        "Use plain language, not lists. Be specific."
    )

    user_prompt = f"Memories about '{query}':\n\n{memories_text}\n\nSummary:"

    messages = [
        ChatMessage(role="system", content=system_prompt),
        ChatMessage(role="user", content=user_prompt),
    ]

    model_to_use = llm_client.chat_model
    response = await llm_client.chat(
        messages,
        model=model_to_use,
        temperature=0.3,
        num_predict=256,
    )

    text = response.content.strip() if response.content.strip() else response.thinking.strip()
    return text


@app.get("/memory/search", response_model=TopicSearchResponse)
async def memory_search(
    q: str,
    limit: int = Query(default=8, ge=1, le=100),
    summary: bool = False,
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

    logger.debug(
        "Topic search (query_len=%d): %d matches after lexical blend",
        len(q), len(matches),
    )
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

    summary_text = ""
    if summary and ranked:
        try:
            memories_text = _build_memories_text(ranked, q)
            summary_text = await _summarize_memories(memories_text, q, _state.get("llm_client"))
        except Exception as e:
            logger.warning("Failed to generate memory summary: %s", e)

    return TopicSearchResponse(
        query=q, semantic_search=semantic_ok, backend_rev=2, matches=ranked,
        summary=summary_text
    )


# Episodes (for debugging / inspection)
@app.get("/users/{user_id}/episodes", response_model=list[Episode])
async def get_user_episodes(
    user_id: int,
    limit: int = Query(default=50, ge=1, le=500),
    store: MemoryStore = _Depends(get_store),
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
    limit: int = Query(default=50, ge=1, le=500),
    store: MemoryStore = _Depends(get_store),
):
    """Conversation turns for a session, oldest first — chat UI restore.

    user_id is required and scopes the read in SQL so one household member
    cannot replay another member's session by guessing the session id. `limit`
    selects the most recent N turns and is applied in SQL, so a long-running
    session no longer transfers its entire history to render the last screenful.
    """
    episodes = await store.get_episodes_for_session(
        session_id, user_id=user_id, limit=limit
    )
    return [
        SessionMessage(role=e.role, content=e.content, timestamp=e.timestamp)
        for e in episodes
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

    def _copy() -> None:
        src = connect(str(db_path))
        dst = connect(str(backup_path))
        try:
            with dst:
                src.backup(dst)
        finally:
            src.close()
            dst.close()

    # SQLite's backup API is blocking. A large brain would otherwise freeze the
    # event loop -- and every in-flight request with it -- for the whole copy.
    await asyncio.to_thread(_copy)

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

    # Copy through SQLite's backup API rather than overwriting the file. Replacing
    # the file leaves the write-ahead log of the replaced database behind, and
    # SQLite replays that log over the file that took its place — so the restore
    # silently undoes itself and the restored database will not even open. Going
    # through SQLite also gets the locking and the encryption context right, and
    # leaves open connections seeing the restored contents immediately.
    def _restore() -> None:
        src = connect(str(backup_path))
        dst = connect(str(db_path))
        try:
            with dst:
                src.backup(dst)
        finally:
            src.close()
            dst.close()

    try:
        await asyncio.to_thread(_restore)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Restore failed: {exc}") from exc

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


class FeedbackKind(str, Enum):
    positive = "positive"
    negative = "negative"
    correction = "correction"


class FeedbackRequest(BaseModel):
    episode_id: str | None = None
    message_id: str
    kind: FeedbackKind
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
    valid_kinds = {k.value for k in FeedbackKind}
    if request.kind.value not in valid_kinds:  # pragma: no cover - enum enforces it
        raise HTTPException(status_code=400, detail=f"kind must be one of {valid_kinds}")

    feedback = await store.create_feedback(
        episode_id=request.episode_id,
        message_id=request.message_id,
        kind=request.kind.value,
        comment=request.comment,
    )

    slots_updated = 0
    if request.kind is FeedbackKind.positive:
        slots_updated = await store.apply_positive_feedback(request.episode_id)
    elif request.kind is FeedbackKind.negative:
        slots_updated = await store.apply_negative_feedback(request.episode_id)

    # Be honest about what happened. This handler previously reported
    # {"status":"ok"} unconditionally, so a reaction that updated zero
    # confidences -- which is what every reaction did, because the UI sent a
    # null episode id -- was indistinguishable from a successful one. The
    # feedback row is still recorded either way (it is the audit trail), but the
    # caller can now tell that reinforcement did not happen.
    if request.kind is FeedbackKind.positive or request.kind is FeedbackKind.negative:
        if slots_updated == 0:
            logger.warning(
                "Feedback recorded but no confidences changed: kind=%s message_id=%s "
                "episode_id=%r. The session id may be missing, or no turn in it "
                "touched memory.",
                request.kind.value, request.message_id, request.episode_id,
            )
            return FeedbackResponse(
                status="recorded_no_memory_touched",
                feedback=feedback,
                slots_updated=0,
            )

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
        # `limit=1`: only the latest turn is ever used. This endpoint has no
        # user_id in its request shape, so it stays unscoped; the read is at
        # least bounded. Owner-scoping it needs an API change (tracked in the
        # plan) rather than a silent behaviour change here.
        episodes = await store.get_episodes_for_session(request.episode_id, limit=1)
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

        result = await apply_correction(
            correction,
            store,
            source_episode_id,
            embed_fn=llm_client.embed_one,
            embedding_model=llm_client.embedding_model,
        )
        slots_corrected = result.get("slots_corrected", 0)
        conflict = result.get("conflict", False)

        # Generate model-based response instead of hardcoded string
        if slots_corrected > 0:
            prompt = (
                f"User submitted a correction: '{correction_text}'. "
                f"The value for {correction.frame_name}.{correction.slot_key} "
                f"has been updated to '{correction.new_value}'. "
                "Generate a natural, concise acknowledgment response. "
                "Do not use templates like 'Got it — I've updated...'."
            )
        else:
            prompt = (
                "User submitted a correction that was applied but resulted in no changes. "
                "Generate a helpful, natural acknowledgment response."
            )
        
        # Use model to generate response
        try:
            resp = await llm_client.chat(
                [ChatMessage(role="user", content=prompt)],
                model=llm_client.chat_model,
                temperature=0.7,
                think=False,
            )
            frame_slot = f"{correction.frame_name}.{correction.slot_key}"
            base_msg = f"Updated {frame_slot} to '{correction.new_value}'."
            fallback_msg = "Correction applied but no slots were updated."
            conversational_status = resp.content or (
                base_msg if slots_corrected > 0 else fallback_msg
            )
        except Exception:
            # Fallback to simple natural-language prompt-based response
            try:
                fallback_prompt = (
                    "Generate a concise, helpful acknowledgment for a correction that was applied. "
                    "Do not use phrases like 'Got it — I've updated...' or any template patterns."
                )
                fallback_resp = await llm_client.chat(
                    [ChatMessage(role="user", content=fallback_prompt)],
                    model=llm_client.chat_model,
                    temperature=0.7,
                    think=False,
                )
                frame_slot = f"{correction.frame_name}.{correction.slot_key}"
                base_msg = f"Updated {frame_slot} to '{correction.new_value}'."
                fallback_msg = "Correction applied but no slots were updated."
                conversational_status = fallback_resp.content or (
                    base_msg if slots_corrected > 0 else fallback_msg
                )
            except Exception:
                # Final absolute fallback (this should be extremely rare)
                conversational_status = (
                    f"Got it — updated {correction.frame_name}.{correction.slot_key} "
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


# Alerts (Learning Monitor)

class AlertResponse(BaseModel):
    id: int
    user_id: int
    type: str
    title: str
    message: str
    source_frame_id: int | None = None
    source_episode_id: int | None = None
    severity: str
    is_read: bool
    created_at: str | None = None
    read_at: str | None = None


class AlertsListResponse(BaseModel):
    alerts: list[AlertResponse]
    unread_count: int


@app.get("/alerts", response_model=AlertsListResponse)
async def get_alerts(
    user_id: int = 1,
    unread_only: bool = False,
    limit: int = Query(default=50, ge=1, le=500),
    store: MemoryStore = _Depends(get_store),
):
    """Get alerts for a user (learning monitor)."""
    alerts = await store.get_alerts(user_id=user_id, unread_only=unread_only, limit=limit)
    unread_count = await store.get_unread_alert_count(user_id)
    return AlertsListResponse(
        alerts=[AlertResponse(**alert.model_dump()) for alert in alerts],
        unread_count=unread_count,
    )


@app.post("/alerts/{alert_id}/read")
async def mark_alert_read(
    alert_id: int,
    user_id: int = 1,
    store: MemoryStore = _Depends(get_store),
):
    """Resolve an alert.

    Kept under its old name and route because the UI uses them, but this now
    performs the real transition: `status = resolved`. There is no "seen but open"
    state — an alert the user looked at and did not answer is still a thing the
    agent is waiting on. See plans/2026-09-30-alerts-as-memory.md.
    """
    success = await store.mark_alert_read(alert_id, user_id)
    if not success:
        raise HTTPException(status_code=404, detail="Alert not found")
    return {"status": "ok", "alert_id": alert_id}


@app.post("/alerts/{alert_id}/open")
async def open_alert_conversation(
    alert_id: int,
    user_id: int = 1,
    session_id: str | None = None,
    store: MemoryStore = _Depends(get_store),
):
    """Attach an alert to a conversation so it can be resolved there.

    `session_id` is the selector's answer: resolve this in a conversation the user
    already has. Omit it and the alert falls back to its own thread, which exists
    only so the operation is idempotent — the default path is an existing
    conversation, because a thread per alert fills the list with one-off threads.

    On an empty session the alert is written as the opening assistant message; on one
    with history, no message is written and the alert is simply linked, so it closes
    when the user replies.
    """
    if session_id:
        try:
            opened_session, episode_id = await store.attach_alert_to_conversation(
                alert_id, user_id, session_id
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    else:
        try:
            opened_session, episode_id = await store.open_alert_conversation(
                alert_id, user_id
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "status": "ok",
        "alert_id": alert_id,
        "session_id": opened_session,
        "episode_id": episode_id,
        "seeded": episode_id is not None,
    }


@app.get("/alerts/{alert_id}/conversations")
async def alert_conversation_options(
    alert_id: int,
    user_id: int = 1,
    limit: int = Query(default=20, ge=1, le=100),
    store: MemoryStore = _Depends(get_store),
):
    """Conversations an alert could be resolved in, for the selector.

    Most-recent-first, because the conversation the user is most likely to mean is
    the one they were last in. Excludes sessions that are themselves alert threads —
    offering "resolve this alert in another alert's thread" is not a choice, it is a
    way to entangle two questions.
    """
    frame = await store.get_frame(alert_id)
    if frame is None or frame.type != "alert":
        raise HTTPException(status_code=404, detail="Alert not found")

    sessions = await store.get_sessions_for_user(user_id)
    options = [
        s for s in sessions
        if not str(s.get("id", "")).startswith("conv_alert_")
    ]
    options = options[:limit]
    return {
        "alert_id": alert_id,
        "conversations": [
            {
                "session_id": s.get("id"),
                "name": s.get("last_message") or "Untitled",
                "last_activity": s.get("last_activity"),
                "message_count": s.get("episode_count") or 0,
            }
            for s in options
        ],
    }


@app.post("/alerts/read-all")
async def mark_all_alerts_read(
    user_id: int = 1,
    store: MemoryStore = _Depends(get_store),
):
    """Mark all alerts as read for a user."""
    count = await store.mark_all_alerts_read(user_id)
    return {"status": "ok", "marked_read": count}


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


@app.post("/tasks/run-due")
async def run_due_tasks(
    store: MemoryStore = _Depends(get_store),
):
    """Trigger execution of all due scheduled tasks.

    Returns immediately. The scheduler loop does the work, so a full daily list
    -- eleven tasks through the LLM -- no longer runs inside the request and no
    longer outlives Caddy's 300s response_header_timeout. Before this the endpoint
    executed every task serially and returned 504 at 300s while the tasks
    completed anyway, which made the response misleading in both directions.

    This is the same execution path as the automatic tick: `_execute_task`
    records last_run/next_run, the daily-run frame, the output episode and any
    agent alert. There is no second implementation to drift.
    """
    from assistant.backend.scheduler.runner import wake_scheduler

    due = await store.get_due_scheduled_tasks()
    if not due:
        return {
            "status": "nothing_due",
            "tasks_queued": [],
            "message": "No due tasks found",
        }

    names = [t["name"] for t in due]
    if not wake_scheduler():
        # The loop is off (SCHEDULER_ENABLED=false) or not yet started. Say so
        # rather than reporting a hand-off that will not happen.
        return {
            "status": "scheduler_not_running",
            "tasks_queued": [],
            "tasks_due": names,
            "message": (
                f"{len(names)} task(s) are due but the scheduler loop is not "
                "running, so nothing was queued. Set SCHEDULER_ENABLED=true or "
                "use POST /tasks/run-now/{task_name} to run one directly."
            ),
        }

    logger.info("Manual run requested: %d due task(s) queued", len(names))
    return {
        "status": "queued",
        "tasks_queued": names,
        "message": (
            f"{len(names)} task(s) queued; the scheduler loop will run them "
            "shortly. Results land as episodes and alerts."
        ),
    }


@app.post("/tasks/run-now/{task_name}")
async def run_task_now(
    task_name: str,
    user_id: int = 1,
    orchestrator: Orchestrator = _Depends(get_orchestrator),
    store: MemoryStore = _Depends(get_store),
):
    """Run a specific scheduled task by name immediately (run_now).
    
    Creates alerts for task completion/failure.
    """
    tasks = await store.get_scheduled_tasks(owner_user_id=user_id)
    task = next((t for t in tasks if t["name"] == task_name), None)
    
    if not task:
        raise HTTPException(status_code=404, detail=f"Task '{task_name}' not found")
    
    task_prompt = task.get("prompt", "")
    frame_id = task["id"]
    owner_user_id = task.get("owner_user_id") or user_id
    
    if not task_prompt:
        return {
            "name": task_name,
            "success": False,
            "result_summary": "Task has no prompt",
        }
    
    from assistant.backend.scheduler import execute_and_record_task
    success, result = await execute_and_record_task(
        store=store,
        orchestrator=orchestrator,
        task_frame_id=frame_id,
        task_name=task_name,
        task_prompt=task_prompt,
        owner_user_id=owner_user_id,
    )
    
    summary = result[:2000] if result else ""
    return {
        "name": task_name,
        "success": success,
        "result_summary": summary,
    }


@app.get("/users/{user_id}/sessions", response_model=list[dict])
async def list_user_sessions(
    user_id: int,
    store: MemoryStore = _Depends(get_store),
):
    """List all conversation sessions for a user.

    Each session has a name (first user message or a given title),
    a creation timestamp, and episode count.
    """
    sessions = await store.get_sessions_for_user(user_id)
    return Response(
        content=json.dumps(sessions),
        media_type="application/json",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"}
    )


@app.post("/conversations/new", response_model=dict)
async def new_conversation(
    user_id: int,
    store: MemoryStore = _Depends(get_store),
):
    """Create a new conversation session for a user.

    Returns the new session_id. The user can then send messages with
    this session_id, and episodes will be stored under this session.
    """
    import uuid
    session_id = f"conv_{uuid.uuid4().hex[:12]}"
    # Verify user exists
    user = await store.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    # Create session record
    await store.create_session(session_id, user_id)
    return {"session_id": session_id, "message": "New conversation created"}


# Serve root-level assets (trash.svg, favicon.ico, favicon.svg) for direct access

@app.get("/trash.svg")
async def trash_svg():
    return FileResponse(str(Path(__file__).parent / "static" / "trash.svg"))

@app.get("/favicon.ico")
async def favicon_ico():
    return FileResponse(str(Path(__file__).parent / "static" / "favicon.svg"))

@app.get("/favicon.svg")
async def favicon_svg():
    return FileResponse(str(Path(__file__).parent / "static" / "favicon.svg"))
@app.get("/conversations/{session_id}/details", response_model=dict)
async def conversation_details(
    session_id: str,
    user_id: int,
    store: MemoryStore = _Depends(get_store),
):
    """Get details for a conversation session.

    Includes episode count, first message, and whether the session
    has any stored facts/frames.
    """
    episodes = await store.get_episodes_for_session(session_id, user_id=user_id)
    user_episodes = episodes
    
    # Get first user message
    first_msg = None
    for ep in user_episodes:
        if ep.role == "user" and ep.content:
            first_msg = ep.content[:80]
            break
    
    # Check if session has contributed any frames/slots
    # by looking at frames that were touched by this session
    # (we check if any frame has this session's episodes in frame_ids)
    frame_count = 0  # Placeholder - would need more complex query
    
    return {
        "session_id": session_id,
        "episode_count": len(user_episodes),
        "first_message": first_msg,
        "frame_contributions": frame_count,
    }


class SummarizeRequest(BaseModel):
    session_id: str


class SummarizeResponse(BaseModel):
    status: str
    session_id: str
    summary: str | None = None
    key_entities: list[str] | None = None
    open_questions: list[str] | None = None
    turn_count: int | None = None
    created: bool | None = None
    detail: str | None = None


class ConversationTitleUpdate(BaseModel):
    user_id: int
    title: str


@app.post("/summarize", response_model=SummarizeResponse)
async def summarize_session(
    request: SummarizeRequest,
    store: MemoryStore = _Depends(get_store),
):
    """User-initiated summarization of a conversation session.

    Triggers the summarizer to compress the conversation episodes
    into a structured summary frame. Useful for manually triggering
    summarization or after significant conversation changes.

    Returns the summary result including entities and open questions.
    """
    from assistant.backend.scheduler.summarizer import Summarizer

    llm_client: OllamaClient = _state["llm_client"]

    # Use user_id=1 as default (the first/primary user)
    # The summarizer will filter episodes by this user_id
    summarizer = Summarizer(store=store, llm_client=llm_client)

    result = await summarizer.summarize_session(
        session_id=request.session_id,
        user_id=1,
    )

    if result is None:
        return SummarizeResponse(
            status="skipped",
            session_id=request.session_id,
            detail="Session has insufficient turns or doesn't exist",
        )

    return SummarizeResponse(
        status="ok",
        session_id=request.session_id,
        summary=result.summary,
        key_entities=result.key_entities,
        open_questions=result.open_questions,
        turn_count=result.turn_count,
        created=result.created,
    )


@app.patch("/conversations/{session_id}/title")
async def update_conversation_title(
    session_id: str,
    body: ConversationTitleUpdate,
    store: MemoryStore = _Depends(get_store),
):
    """Update conversation title."""
    await store.update_session_title(session_id, body.user_id, body.title)
    return {"session_id": session_id, "title": body.title}


@app.delete("/conversations/{session_id}")
async def delete_conversation(
    session_id: str,
    user_id: int,
    store: MemoryStore = _Depends(get_store),
):
    """Delete a conversation session and its episodes (soft delete)."""
    deleted = await store.delete_session(session_id, user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"status": "ok", "session_id": session_id}


@app.get("/conversations/trash", response_model=list[dict])
async def list_deleted_conversations(
    user_id: int,
    store: MemoryStore = _Depends(get_store),
):
    """List all soft-deleted conversations for a user (trash can view)."""
    sessions = await store.get_deleted_sessions_for_user(user_id)
    return sessions


@app.post("/conversations/{session_id}/restore")
async def restore_conversation(
    session_id: str,
    user_id: int,
    store: MemoryStore = _Depends(get_store),
):
    """Restore a soft-deleted conversation."""
    restored = await store.restore_session(session_id, user_id)
    if not restored:
        raise HTTPException(status_code=404, detail="Conversation not found or not deleted")
    return {"status": "ok", "session_id": session_id}


async def upload_file_to_memory(
    filename: str,
    content: bytes,
    ext: str,
    store: MemoryStore,
    user_id: int = 1,
) -> dict:
    """Upload a file to memory and disk. Returns file metadata and frame info.
    
    This is the core file upload logic shared by /files/upload endpoint and chat attached files.
    """
    import re
    from pathlib import Path

    from assistant.backend.pipeline.files import extract_file_content

    # Store file in data directory
    data_dir = Path("/app/data")
    data_dir.mkdir(exist_ok=True)
    
    # Preserve the uploaded file's own name on disk — no timestamp rewrite.
    # Sanitize only filesystem-unsafe characters; the copy keeps the name the
    # user gave it. Same-name re-uploads overwrite the same single copy (and
    # merge at the frame level), so no orphaned timestamped duplicates.
    original_base = filename.rsplit(".", 1)[0] if "." in filename else filename
    sanitized_base = re.sub(r'[^a-zA-Z0-9_.-]', '_', original_base)[:100]
    safe_filename = f"{sanitized_base}.{ext}"
    file_path = data_dir / safe_filename
    
    # Save file
    with open(file_path, "wb") as f:
        f.write(content)
    
    # Extract content based on type
    extraction_result = await extract_file_content(file_path, ext, content)
    
    # Create a frame for this file — named from the file's own name so it stays
    # readable in the Files page and brain graph. The on-disk copy keeps the
    # same name (file_safe_name slot); nothing is renamed on upload.
    frame_name = f"file_{sanitized_base}.{ext}"
    existing_frame = await store.get_frame_by_name(frame_name)
    
    if not existing_frame:
        frame = await store.create_frame(
            frame_name,
            "entity",
            source_type="file_upload",
            owner_user_id=user_id,
            source_reliability=0.7,
        )
    else:
        frame = existing_frame
        # Same-name re-upload merges into the existing frame (frame names are
        # UNIQUE); rebuild its CSV row frames so rows don't accumulate.
        stale_rows = [
            a.to_frame_id
            for a in await store.get_all_associations_for_frame(frame.id)
            if a.relation_type == "part_of"
        ]
        if stale_rows:
            await store.prune_frames(stale_rows)
    
    # Memory records what the file *is*; the content stays on disk and is read
    # verbatim. The preview used to be stored here and excluded at render time,
    # which left a stale copy that could be served in place of the real file when
    # a disk read failed. See plans/2026-10-01-file-support-diagnosis.md.
    #
    # This preview is for the upload *response* only — so the UI can show what was
    # received — and is never written to a slot.
    content_text = extraction_result.text
    content_preview = content_text[:200] + ("..." if len(content_text) > 200 else "")

    await store.upsert_slot(
        frame_id=frame.id,
        key="file_name",
        value=filename,
        essential=0,
        priority=0.5,
        source_type="file_upload",
        source_reliability=0.8,
    )
    
    await store.upsert_slot(
        frame_id=frame.id,
        key="file_size",
        value=str(len(content)),
        essential=0,
        priority=0.5,
        source_type="file_upload",
        source_reliability=0.8,
    )
    
    await store.upsert_slot(
        frame_id=frame.id,
        key="file_ext",
        value=ext,
        essential=0,
        priority=0.5,
        source_type="file_upload",
        source_reliability=0.8,
    )
    
    # Store the safe filename for file lookup
    await store.upsert_slot(
        frame_id=frame.id,
        key="file_safe_name",
        value=safe_filename,
        essential=0,
        priority=0.5,
        source_type="file_upload",
        source_reliability=0.7,
    )
    
    # Store extracted facts/slots if any — capped so large CSVs don't dump one
    # entity_* slot per unique cell onto the file frame (FILE_MAX_ENTITY_SLOTS).
    entity_cap = settings.file_max_entity_slots
    if extraction_result.key_entities:
        if len(extraction_result.key_entities) > entity_cap:
            logger.info(
                "File %s extracted %d entities; storing first %d only "
                "(FILE_MAX_ENTITY_SLOTS=%d)",
                filename, len(extraction_result.key_entities), entity_cap, entity_cap,
            )
        for entity in extraction_result.key_entities[:entity_cap]:
            await store.upsert_slot(
                frame_id=frame.id,
                key=f"entity_{entity}",
                value=entity,
                essential=0,
                priority=0.5,
                source_type="file_upload",
                source_reliability=0.8,
            )
    
    # For CSV files, create row frames
    row_frame_ids = []
    if ext == "csv" and extraction_result.row_data:
        row_count = len(extraction_result.row_data)
        await store.upsert_slot(
            frame_id=frame.id,
            key="row_count",
            value=str(row_count),
            essential=0,
            priority=0.5,
            source_type="file_upload",
            source_reliability=0.8,
        )
        
        # Store columns slot
        if extraction_result.row_data:
            columns = list(extraction_result.row_data[0].keys())
            import json
            await store.upsert_slot(
                frame_id=frame.id,
                key="columns",
                value=json.dumps(columns),
                essential=0,
                priority=0.5,
                source_type="file_upload",
                source_reliability=0.8,
            )
        
        # Create row frames (capped: past CSV_MAX_ROW_FRAMES the file frame
        # keeps row_count/columns metadata only — row data lives on disk and is
        # read via read_file, so memory never explodes per-row).
        row_frame_cap = settings.csv_max_row_frames
        for i, row in enumerate(extraction_result.row_data[:row_frame_cap]):
            row_frame_name = f"file_{sanitized_base}_row_{i+1}"
            row_frame = await store.create_frame(
                row_frame_name,
                "record",
                source_type="csv_row",
                owner_user_id=user_id,
            )
            row_frame_ids.append(row_frame.id)
            
            # Store each column as a slot
            for col, val in row.items():
                slot_key = re.sub(r'[^a-zA-Z0-9_]', '_', col.lower().strip())
                slot_key = re.sub(r'_+', '_', slot_key).strip('_')
                if not slot_key:
                    slot_key = f"col_{i}"
                await store.upsert_slot(
                    frame_id=row_frame.id,
                    key=slot_key,
                    value=str(val),
                    essential=0,
                    priority=0.5,
                    source_type="csv_row",
                    source_reliability=0.8,
                )
            
            # Link parent -> row
            await store.create_association(frame.id, row_frame.id, "part_of")

        if row_count > row_frame_cap:
            logger.info(
                "CSV %s has %d rows; created row frames for first %d only "
                "(CSV_MAX_ROW_FRAMES=%d)",
                filename, row_count, row_frame_cap, row_frame_cap,
            )
    else:
        row_count = 0
    
    return {
        "status": "ok",
        "file_name": filename,
        "file_size": len(content),
        "file_ext": ext,
        "content_preview": content_preview,
        "key_entities": extraction_result.key_entities[: settings.file_max_entity_slots],
        "open_questions": extraction_result.open_questions,
        "frame_name": frame_name,
        "frame_id": frame.id,
        "parent_frame_id": frame.id,
        "row_count": row_count,
        "row_frame_ids": row_frame_ids,
    }


@app.post("/files/upload", response_model=dict)
async def upload_file(
    file: UploadFile = File(...),
    store: MemoryStore = _Depends(get_store),
):
    """Upload and process a file.

    Supported formats: .txt, .csv, .json, .xml, .html
    Returns file metadata and extracted content.
    """
    # Validate file type
    filename = file.filename or "unknown"
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    allowed_types = {"txt", "csv", "json", "xml", "html", "ics"}
    
    if ext not in allowed_types:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: .{ext}. Allowed: .txt, .csv, .json, .xml, .html",
        )
    
    # Size limit (10MB)
    content = await file.read()
    if len(content) > 10_000_000:
        raise HTTPException(
            status_code=400,
            detail="File too large. Maximum size: 10MB",
        )
    
    return await upload_file_to_memory(filename, content, ext, store)


# --- File API Endpoints ---


class FileFrameResponse(BaseModel):
    id: int
    name: str
    file_name: str | None = None
    type: str
    confidence: float
    essential: int
    priority: float
    source_type: str | None
    source_url: str | None
    source_reliability: float | None
    created_at: datetime
    updated_at: datetime


class FileSearchResponse(BaseModel):
    frames: list[FileFrameResponse]
    query: str


class FileContentResponse(BaseModel):
    frame_id: int
    frame_name: str
    content: str
    file_name: str | None
    file_ext: str | None
    file_size: int | None


# List all file frames for the user
@app.get("/files/list", response_model=list[FileFrameResponse])
async def list_files(
    user_id: int = 1,
    store: MemoryStore = _Depends(get_store),
):
    """List all file frames for a user."""
    frames = await store.list_frames(owner_user_id=user_id)
    # Filter to only file upload frames that are not forgotten (priority > 0)
    file_frames = [f for f in frames if f.source_type == "file_upload" and f.priority > 0]

    # Load the real uploaded filename (file_name slot) for display — the frame
    # name is the internal handle, while file_name is what the user actually
    # named the file.
    file_names: dict[int, str] = {}
    if file_frames:
        ids = [f.id for f in file_frames]
        placeholders = ",".join("?" * len(ids))
        async with store._connect() as db:
            rows = await db.execute_fetchall(
                f"SELECT frame_id, value FROM slots "
                f"WHERE frame_id IN ({placeholders}) AND key = 'file_name'",
                ids,
            )
        file_names = {row[0]: row[1] for row in rows}

    return [
        FileFrameResponse(
            id=frame.id,
            name=frame.name,
            file_name=file_names.get(frame.id),
            type=frame.type,
            confidence=frame.confidence,
            essential=frame.essential,
            priority=frame.priority,
            source_type=frame.source_type,
            source_url=frame.source_url,
            source_reliability=frame.source_reliability,
            created_at=frame.created_at,
            updated_at=frame.updated_at,
        )
        for frame in file_frames
    ]


# Search file content by query and optional type
@app.get("/files/search", response_model=FileSearchResponse)
async def search_files(
    query: str,
    file_type: str | None = None,
    user_id: int = 1,
    store: MemoryStore = _Depends(get_store),
    orch: Orchestrator = _Depends(get_orchestrator),
):
    """Search uploaded files by query, optionally filtered to one file type."""
    query = query.strip()
    if not query:
        raise HTTPException(status_code=400, detail="Query cannot be empty")

    # The app-wide client, not a fresh one per request: it carries the embedding
    # cache and a warm HTTP pool. (This endpoint used to build its own client
    # with keyword arguments OllamaClient does not take, and call a method that
    # does not exist — every request was a 500.)
    embedding = await orch.llm_client.embed_one(query)

    results = await store.search_similar_frames(
        embedding=embedding,
        user_id=user_id if user_id else None,
        embedding_model=settings.embedding_model,
        limit=20,
        min_distance=0.3,
    )

    # Accept "txt" and ".txt" alike; uploads store the extension undotted.
    wanted_ext = file_type.lstrip(".").lower() if file_type else None

    frames = []
    for frame, slots, _similarity in results:
        # search_similar_frames searches every frame. A "search my files" result
        # that can return a person frame is wrong, and a frame with no file_ext
        # used to slip through the type filter — so membership is decided by the
        # slot, not assumed.
        by_key = {s.key: s.value for s in slots}
        ext = by_key.get("file_ext")
        if not ext:
            continue
        if wanted_ext and ext.lower() != wanted_ext:
            continue

        frames.append(
            FileFrameResponse(
                id=frame.id,
                name=frame.name,
                file_name=by_key.get("file_name"),
                type=frame.type,
                confidence=frame.confidence,
                essential=frame.essential,
                priority=frame.priority,
                source_type=frame.source_type,
                source_url=frame.source_url,
                source_reliability=frame.source_reliability,
                created_at=frame.created_at,
                updated_at=frame.updated_at,
            )
        )

    return FileSearchResponse(frames=frames, query=query)


async def _file_slots_for_frame(store: MemoryStore, frame_id: int) -> dict[str, str]:
    """All slot key/value pairs for a file frame, as a plain dict."""
    async with store._connect() as db:
        rows = await db.execute_fetchall(
            "SELECT key, value FROM slots WHERE frame_id = ?", (frame_id,)
        )
    return {row[0]: row[1] for row in rows}


def _contained_file_path(file_safe_name: str):
    """Resolve a `file_safe_name` slot to a path inside the data dir.

    `file_safe_name` is a DB string, and the model can write slots: the
    `upsert_slot` tool and the extraction pipeline both accept an arbitrary
    `slot_key` with no reserved-key denylist. Joining it by hand therefore gave
    arbitrary file read (via the two GET handlers) and arbitrary file delete
    (via DELETE). `Path("/app/data") / "/app/.env"` needs no `..` at all, because
    an absolute right-hand operand discards the left.

    This delegates to the same `resolve_sandbox_path` the file tools already use
    (tool_executor reads this very slot through it), so the API surface and the
    tool surface now agree on what a legal path is.

    Raises PathTraversalError if the value escapes the data directory.
    """
    from assistant.backend.pipeline.filesystem import resolve_sandbox_path

    return resolve_sandbox_path(file_safe_name)


# Get file content by frame ID (download as text)
@app.get("/files/{frame_id}/content", response_model=FileContentResponse)
async def get_file_content(
    frame_id: int,
    store: MemoryStore = _Depends(get_store),
):
    """Get file content by frame ID for download."""
    return await _file_response(store, frame_id)


# Get file details and content by frame ID
@app.get("/files/{frame_id}", response_model=FileContentResponse)
async def get_file(
    frame_id: int,
    store: MemoryStore = _Depends(get_store),
):
    """Get file details and content by frame ID."""
    return await _file_response(store, frame_id)


async def _file_response(store: MemoryStore, frame_id: int) -> "FileContentResponse":
    """Shared body for both GET /files/{frame_id} routes.

    These two handlers were byte-identical duplicates. Because Starlette matches
    routes in registration order, `/files/{id}/content` was permanently shadowed
    by `/files/{id}`, so the first copy could never serve a request -- which meant
    a containment fix applied to only one of them would have been no fix at all.
    """
    frame = await store.get_frame(frame_id)
    if not frame:
        raise HTTPException(status_code=404, detail="File not found")

    slots_dict = await _file_slots_for_frame(store, frame_id)
    file_name = slots_dict.get("file_name")
    file_ext = slots_dict.get("file_ext")
    file_size = slots_dict.get("file_size")
    file_safe_name = slots_dict.get("file_safe_name")

    content = ""
    if file_safe_name:
        from assistant.backend.pipeline.filesystem import PathTraversalError

        try:
            file_path = _contained_file_path(file_safe_name)
        except (PathTraversalError, ValueError):
            # A slot pointing outside the data dir is not a readable file.
            # Return metadata with empty content rather than leaking the path.
            file_path = None
        if file_path is not None and file_path.exists():
            try:
                content = file_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                content = ""

    return FileContentResponse(
        frame_id=frame.id,
        frame_name=frame.name,
        content=content,
        file_name=file_name,
        file_ext=file_ext,
        file_size=int(file_size) if file_size else None,
    )


# Delete a file frame and its associated file
@app.delete("/files/{frame_id}")
async def delete_file(
    frame_id: int,
    store: MemoryStore = _Depends(get_store),
):
    """Delete a file frame and its associated file."""
    frame = await store.get_frame(frame_id)
    if not frame:
        raise HTTPException(status_code=404, detail="File not found")

    # Get file safe name from slots before deleting
    file_slots = await _file_slots_for_frame(store, frame_id)
    file_safe_name = file_slots.get("file_safe_name")

    # Hard-delete the frame and its CSV row frames — soft-delete (forget_frame)
    # leaves them visible in list_frames()/the brain graph, dangling after the
    # file is gone. Deleting a file must clean up its memory.
    await store.prune_file_frame(frame_id)

    # Remove the physical file, but only if it is inside the data dir. The
    # `file_safe_name` slot is model-writable, so an unvalidated join here would
    # let a crafted slot unlink arbitrary files on the host.
    if file_safe_name:
        from assistant.backend.pipeline.filesystem import PathTraversalError

        try:
            file_path = _contained_file_path(file_safe_name)
        except (PathTraversalError, ValueError):
            file_path = None
        if file_path is not None and file_path.exists():
            try:
                file_path.unlink()
            except OSError:
                pass

    return {"status": "ok", "message": "File deleted successfully"}



# --- SolidJS SPA catch-all (must be last) ---
# Serves the SPA index.html for any path that doesn't match an API route or static file.
# This enables client-side routing: /chat, /brain, /files, /settings
# all render the same SPA, and SolidJS handles navigation in the browser.
@app.get("/{path:path}")
async def spa_catch_all(path: str):
    """Serve the SolidJS SPA for any unmatched path."""
    from pathlib import Path

    from fastapi.responses import FileResponse, PlainTextResponse

    # Don't serve SPA for asset file types, /assets/, or API routes
    asset_extensions = [".js", ".css", ".svg", ".png", ".jpg", ".ico", ".wasm", ".json"]
    api_prefixes = [
        "/api/",
        "/memory/",
        "/brain/",
        "/chat/",
        "/files/",
        "/settings/",
        "/tasks/",
        "/db/",
        "/feedback/",
        "/correction/",
        "/conversations/",
        "/users/",
        "/assistant/name",
        "/search/",
        "/health",
        "/og-preview",
        "/transcribe/",
        "/uploads/",
        "/embeddings/",
        "/show/",
        "/tags/",
        "/assets/",  # Vite build output
    ]
    if any(path.endswith(ext) for ext in asset_extensions):
        raise HTTPException(status_code=404, detail="Asset not found - use /static/path")
    if any(path.startswith(prefix.lstrip("/")) for prefix in api_prefixes):
        raise HTTPException(status_code=404, detail="API route not found")
    index_path = Path(__file__).parent / "static" / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    # Fallback to a simple 200 response for any other assets
    return PlainTextResponse("Assistant frontend loaded", status_code=200)

# Log that app is loaded
logger = logging.getLogger(__name__)
logger.info("Assistant backend initialized")
