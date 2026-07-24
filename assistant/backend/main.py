import logging
import shutil
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import Depends as _Depends
from fastapi import FastAPI, HTTPException

from assistant.backend.config import settings
from assistant.backend.db.schema import init_db
from assistant.backend.memory.models import Conflict, Episode, Frame, Slot, User
from assistant.backend.memory.retrieval import Retriever
from assistant.backend.memory.store import MemoryStore
from assistant.backend.pipeline.llm_client import OllamaClient
from assistant.backend.pipeline.orchestrator import ChatRequest, ChatResponse, Orchestrator
from assistant.backend.pipeline.orchestrator import OrchestratorDeps as _OrchestratorDeps

logger = logging.getLogger(__name__)


# Global state for the app (initialized in lifespan)
_state: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize DB, store, retriever, llm client on startup."""
    db_path = settings.database_path
    # Ensure parent dir exists
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    await init_db(db_path)

    store = MemoryStore(db_path)
    llm_client = OllamaClient(
        base_url=settings.ollama_url,
        chat_model=settings.chat_model,
        utility_model=settings.utility_model,
        embedding_model=settings.embedding_model,
    )
    retriever = Retriever(store=store, llm_client=llm_client)
    orchestrator = Orchestrator(
        deps=_OrchestratorDeps(
            store=store,
            retriever=retriever,
            llm_client=llm_client,
        )
    )

    _state["store"] = store
    _state["llm_client"] = llm_client
    _state["retriever"] = retriever
    _state["orchestrator"] = orchestrator

    logger.info("Assistant started. DB: %s, Ollama: %s", db_path, settings.ollama_url)

    yield

    await llm_client.close()
    _state.clear()


app = FastAPI(
    title="Cognitive Digital Assistant",
    description=(
        "Privacy-first assistant with frame/slot memory, learns from every conversation."
    ),
    version="0.1.0",
    lifespan=lifespan,
)


def get_store() -> MemoryStore:
    return _state["store"]


def get_orchestrator() -> Orchestrator:
    return _state["orchestrator"]


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
     }


# Search
@app.get("/search")
async def search_frames(
    q: str,
    limit: int = 10,
    min_similarity: float = 0.3,
    store: MemoryStore = _Depends(get_store),
    llm_client: OllamaClient = _Depends(get_orchestrator.llm_client),
):
    """Search frames by similarity.

    Returns frames whose embeddings are similar to the query.
    Results include frame details, slots, and similarity score.
    """
    if not q.strip():
        raise HTTPException(status_code=400, detail="Query cannot be empty")

    # Embed the query
    query_response = await llm_client.embed(q)
    
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
                "slots": [{"key": s.key, "value": s.value, "confidence": s.confidence} for s in slots],
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

    src = sqlite3.connect(str(db_path))
    dst = sqlite3.connect(str(backup_path))
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
