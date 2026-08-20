import logging
import shutil
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import Depends as _Depends
from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.staticfiles import StaticFiles

from assistant.backend.config import settings
from assistant.backend.db.schema import init_db
from assistant.backend.db.sqlcipher import connect
from assistant.backend.memory.metadata import (
    METADATA_KEY_EMBEDDING_MODEL,
    get_metadata,
    set_metadata,
)
from assistant.backend.memory.models import Association, Conflict, Episode, Frame, Slot, User
from assistant.backend.memory.retrieval import Retriever
from assistant.backend.memory.store import MemoryStore
from assistant.backend.memory.working_memory import WorkingMemory
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
        reasoning_model=settings.reasoning_model,
        embedding_model=settings.embedding_model,
        verify_tls=settings.ollama_tls_cert if settings.ollama_tls_cert else True,
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

    logger.info("Assistant started. DB: %s, Ollama: %s, Search: enabled")

    yield

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
    name_frame = await store.get_frame_by_name("identity_name")
    if name_frame:
        slot = await store.get_slot(name_frame.id, "full_name")
        if slot:
            return {"name": slot.value}
    return {"name": "Cognitive Assistant"}


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
    """Health check. Verifies Ollama is reachable."""
    llm = _state.get("llm_client")
    ollama_ok = await llm.health_check() if llm else False
    return {
        "status": "ok",
        "ollama_reachable": ollama_ok,
        "chat_model": settings.chat_model,
        "utility_model": settings.utility_model,
        "reasoning_model": settings.reasoning_model,
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
@app.post("/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest, orch: Orchestrator = _Depends(get_orchestrator)
):
    """Send a message to the assistant. Returns the response with trace info."""
    return await orch.chat(request)


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
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = Path(tmp.name)

    try:
        text = await transcribe_audio(tmp_path)
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


# Episodes (for debugging / inspection)
@app.get("/users/{user_id}/episodes", response_model=list[Episode])
async def get_user_episodes(
    user_id: int, limit: int = 50, store: MemoryStore = _Depends(get_store)
):
    return await store.get_episodes_for_user(user_id, limit=limit)


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
