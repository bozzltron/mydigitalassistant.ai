# Architecture

This document describes the architecture of the cognitive digital assistant as it exists today. It is written for both humans and agents who need to understand, extend, or maintain the system. For agent-specific build/test/security rules, see the linked `AGENTS.md` files.

- [Project root AGENTS.md](./AGENTS.md) — security hard rules, build commands, commit style
- [`/assistant/AGENTS.md`](./assistant/AGENTS.md) — backend cognitive architecture and memory model

---

## 1. Design principles

1. **Privacy-first, local-only.** All LLM inference runs through Ollama on `127.0.0.1:11434`. No cloud LLM APIs are permitted. Web search uses a local SearXNG instance only.
2. **Frozen LLM weights, learned memory.** The system does not fine-tune models. Learning happens in the structured memory layer (frames, slots, associations, episodes).
3. **Async, non-blocking hot path.** LLM calls and DB writes in the chat loop are async. Conversation fact extraction is fire-and-forget.
4. **Transparent confidence.** Every memory element has confidence and priority. Conflicts are logged and resolved according to explicit math in `confidence.py`.
5. **Multi-user safe.** Shared household frames have `owner_user_id=NULL`; private frames are scoped to a user. Retrieval filters accordingly.

---

## 2. High-level architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                          User interfaces                         │
│   CLI (`assistant/cli/app.py`)  ←──→  FastAPI (`backend/`)     │
│   Web UI (`/chat-ui`)              ←──→  Static files           │
└───────────────────────────┬─────────────────────────────────────┘
                            │ HTTP / function call
┌───────────────────────────▼─────────────────────────────────────┐
│                        Orchestrator                              │
│   (`assistant/backend/pipeline/orchestrator.py`)                │
│   - Session management                                           │
│   - Task routing                                                 │
│   - Memory retrieval                                             │
│   - Search orchestration                                         │
│   - LLM chat call                                                │
│   - Fire-and-forget extraction                                   │
└──────┬────────────────────┬──────────────────────┬──────────────┘
       │                    │                      │
┌──────▼──────┐  ┌──────────▼──────────┐  ┌───────▼────────┐
│   Memory    │  │   Web search        │  │  Local LLMs    │
│   layer     │  │   (SearXNG)         │  │  (Ollama)      │
│  (SQLite +  │  │  (`pipeline/search`)│  │ (`llm_client`) │
│  sqlite-vec)│  │                     │  │                │
└─────────────┘  └─────────────────────┘  └───────┬────────┘
                                                  │
                    ┌──────────────────────────────▼────────┐
                    │   Voice transcription (faster-whisper) │
                    │   (`pipeline/whisper.py`)               │
                    └─────────────────────────────────────────┘
```

---

## 3. Core components

### 3.1 API and CLI

- **`assistant/backend/main.py`** — FastAPI app with all endpoints (health, chat, transcribe, memory, users).
- **`assistant/cli/app.py`** — Rich-based CLI. Talks to the backend over HTTP.

The CLI and API are thin layers; all business logic lives in the orchestrator and memory modules.

### 3.2 Orchestrator (`backend/pipeline/orchestrator.py`)

The `Orchestrator.chat()` method runs the cognitive loop:

1. **Session** — get or create `session_id`.
2. **Log user episode** — store the user turn.
3. **Classify** — heuristic first, LLM utility-model fallback (`task_router.py`).
4. **Retrieve memory** — embed query, vector search, graph walk, session-aware episodes (`retrieval.py`).
5. **Reason** — decide whether search is needed (`reasoner.py`).
6. **Search (if needed)** — fetch via SearXNG.
7. **Extract facts from search** — structured JSON extraction, upsert into memory.
8. **Build system prompt** — memory context + plan instructions.
9. **Call chat LLM** — with up to 6 prior session turns as message history.
10. **Log assistant episode**.
11. **Fire-and-forget extraction** — extract facts from the conversation turn.
12. **Return response** — with appended `Sources:` block when citations exist.

### 3.3 Memory layer

Implemented in `assistant/backend/memory/`.

- **`store.py`** — `MemoryStore`: async CRUD over SQLite + sqlite-vec.
- **`models.py`** — Pydantic models: `Frame`, `Slot`, `Association`, `Episode`, `Conflict`, `User`.
- **`retrieval.py`** — `Retriever`: embed query, cosine similarity search, graph walk, format context.
- **`confidence.py`** — single source of truth for confidence math and conflict resolution.

### 3.4 Web search

- **`backend/pipeline/search.py`** — search abstraction and SearXNG backend.
- Search is retrieval-only by default.
- Facts from search are extracted by the reasoning model and stored with `source_type="search"`, per-slot URLs, and reliability scores.
- SearXNG runs a curated engine allow-list (`searxng/settings.yml`: bing, duckduckgo,
  brave, mojeek, qwant, wikipedia) instead of the full default fan-out. Every enabled
  engine fires in parallel per query and upstream providers rate-limit aggressive
  automated traffic, so fewer engines keeps the household IP off blocklists. Engines
  degrade independently; results merge from whichever are healthy. `SEARXNG_SECRET`
  must be pinned in `.env` — the compose default regenerates it on every `up`,
  recreating the container each deploy.

### 3.5 LLM client

- **`backend/pipeline/llm_client.py`** — `OllamaClient`.
- Requests carry `keep_alive` (default `30m`, `OLLAMA_KEEP_ALIVE`) so models stay
  resident between turns; reloading a 27B model costs tens of seconds.
- Supports six model roles (see `docs/MODEL_SELECTION.md` for the fleet and re-assessment process):
  - `chat_model` — user-facing responses (default `qwen3.5:9b`)
  - `utility_model` — cheap classification/routing/extraction (default `qwen3.5:4b`)
  - `tools_model` — tool loop (same brain as chat by default, `qwen3.5:9b`)
  - `embedding_model` — frame/query vectors (default `qwen3-embedding:0.6b`, 1024-dim)
  - `max_model` — on-demand escalation tier (`qwen3.8:27b`; think on, `keep_alive=10m`)
  - `math_model` — on-demand `compute` tool (shares the max model by default)

### 3.6 Reasoner and task router

- **`backend/pipeline/task_router.py`** — classify intent as `functional` or `introspective`.
- **`backend/pipeline/reasoner.py`** — decide whether to search, correct memory, or answer from memory.

### 3.7 Web UI

A SolidJS single-page app, built with Vite. In development Caddy routes to the
Vite dev server; in production the built bundle in `assistant/backend/static/`
is served by Caddy (`/assets/*`) with the SPA shell for other paths.

- **`frontend/src/`** — the SPA source (chat, Brain Observatory, files, alerts).
- **`frontend/src/services/api.ts`** — typed API client (Zod-validated responses).

Features:
- Session persistence and conversation restore via `GET /chat/session/{id}/messages`.
- Conversation export: `GET /chat/session/{id}/download` returns the whole thread as a
  plain-text transcript (`pipeline/transcript.py`), owner-scoped.
- Markdown-rendered responses with a sources block.
- Trace panel showing `task_type`, `memory_context`, `citations`, and search info.
- "What I learned" indicator showing the slots stored this turn, including
  auto-resolved conflicts and the search backend when a search ran.
- Brain Observatory: force-graph of frames/associations with conflict resolution.
- Voice conversation mode: hands-free loop using browser `MediaRecorder` + `POST /transcribe`.

### 3.8 Voice transcription

- **`assistant/backend/pipeline/whisper.py`** — local Whisper transcription via `faster-whisper`.
- Lazy-loads the model on first use; keeps cached for subsequent calls.
- `POST /transcribe` endpoint accepts audio blobs and returns transcribed text.
- Model downloads automatically on first use to `~/.cache/whisper/`.
- All audio stays local; no cloud STT services used.

### 3.9 Scheduler and background maintenance

**`assistant/backend/scheduler/`** — a single background loop started by the
FastAPI lifespan (unless `SCHEDULER_ENABLED=false`). It is plain timer logic, not
an LLM agent: each wake checks elapsed intervals, runs whatever is due, and
sleeps. Tasks execute through the same `Orchestrator` as chat, so their results
become ordinary memory.

**The daily list.** The agent wakes once a day at `DAILY_TASKS_TIME` and runs
every enabled `daily` task; `once` tasks run at the next tick and disable
themselves. There are no cron expressions — one shared daily tick. A missed tick
(backend down) fires once late on restart, then reschedules.

**Housekeeping timers.** All are offset from the same loop and are independent of
each other:

| Timer | Interval | Nature |
|---|---|---|
| Heartbeat | 30 min | writes `scheduler_heartbeat` (liveness) |
| Embedding top-up | 6h | re-indexes turns/frames with missing or stale embeddings. Non-destructive, no backup. |
| Consolidation | 6h | **checks** for near-duplicate frames and merges any it finds |
| Brain snapshot | 12h | a plain DB copy for point-in-time recovery |
| Summarization | 6h | compresses eligible sessions (≥10 turns) into summary frames |

**Merge vs. backup — the one subtlety worth stating.** Merging is *ad hoc*: the
6h consolidation timer is a **look cadence**, not a merge cadence. The loop
computes a read-only plan; only if it finds duplicates does it apply them (capped
per run). A household with no duplicates merges nothing regardless of how often it
checks. Backups are deliberately *decoupled* from that: a separate 12h timer takes
the snapshot, and the only place the two meet is that a merge pass running without
a fresh snapshot takes one first — so a merge is never applied unprotected, and
snapshot count does not scale with merge frequency.

**Nothing is forgotten on a timer.** There is no decay or age-based garbage
collection. Memory leaves only through an explicit `forget` or a deliberate
frame/file deletion. (Merges tombstone duplicate losers, but each loser's slots
are unioned onto the survivor before it is tombstoned, so no content is lost.)

---

## 4. Data model

### 4.1 Frames

A frame represents an entity, concept, event, or household item.

```python
class Frame:
    id: int | None
    name: str
    type: str  # entity | concept | event | household
    confidence: float
    essential: int  # 0 or 1; essential items ignore forget/priority=0
    priority: float
    owner_user_id: int | None  # NULL = shared household frame
    source_type: str | None
    source_url: str | None
    source_reliability: float | None
```

### 4.2 Slots

Slots are key/value pairs attached to a frame.

```python
class Slot:
    id: int | None
    frame_id: int
    key: str
    value: str
    confidence: float
    essential: int
    priority: float
    source_type: str | None  # conversation | search | user_correction
    source_url: str | None
    source_reliability: float | None
    source_episode_id: int | None
```

### 4.3 Associations

Typed graph edges between frames.

### 4.4 Episodes

Per-user conversation turns. Each episode belongs to a `session_id`.

### 4.5 Conflicts

When a new slot value contradicts an existing one, a conflict is logged. Auto-resolution picks the winner based on confidence, recency, and source reliability. Manual override is available via the API/CLI.

---

## 5. Cognitive loop details

### 5.1 Retrieval pipeline

1. Embed user query with `qwen3-embedding:0.6b`.
2. Cosine similarity search over `frame_embeddings` via sqlite-vec.
3. Score by `similarity × confidence × priority`.
4. Graph-walk 1-2 hops over associations, decaying relevance per hop.
5. Fetch session episodes first; if fewer than 2 turns, supplement with user-wide episodes.
6. Format everything into a structured text block for the system prompt.
   Recent episodes are included as short digests (240 chars each) rather than
   full transcripts — the most recent turns still arrive verbatim as chat
   history, and full episode text in the system prompt was inflating every
   prompt's prefill by thousands of tokens (tens of seconds on a 27B model).

### 5.2 Confidence math

Defined in `backend/memory/confidence.py`:

- New fact confidence: `0.5`
- Repeated same value: `conf = 1 - (1 - conf) * 0.7` (bounded)
- Conflicting value: auto-resolve based on confidence, source reliability, and recency
- Priority is independent of confidence; explicit commands can bump priority

### 5.3 Search-learn flow

1. Reasoner decides search is needed.
2. SearXNG returns results.
3. Reasoning model extracts structured facts.
4. `apply_search_extraction()`:
   - Maps facts to corroborating URLs
   - Bumps reliability for facts found in 2+ independent sources
   - Picks the best URL per slot (`.edu`, Wikipedia, major news preferred)
   - Upserts slots and associations
5. Citations are collected and appended to the assistant response.

### 5.4 Conversation history

The orchestrator builds the LLM message list as:

```
[system]
[user turn 1]
[assistant turn 1]
...
[user turn N]      # up to 6 prior turns
[current user turn]
```

History is scoped to the current `session_id`.

---

## 6. Security model

See [root AGENTS.md](./AGENTS.md) for hard rules. In short:

- Ollama: `127.0.0.1:11434`
- FastAPI: `127.0.0.1`
- SearXNG: `127.0.0.1`
- No cloud LLM APIs, no telemetry, no analytics.
- Secrets live in `.env` (gitignored); `.env.example` is committed.

---

## 7. Configuration

Environment variables (via Pydantic Settings / `.env`):

| Variable | Purpose | Default |
|----------|---------|---------|
| `OLLAMA_URL` | Ollama base URL | `http://127.0.0.1:11434` |
| `OLLAMA_KEEP_ALIVE` | How long Ollama keeps models resident between requests | `-1` (never unload) |
| `CHAT_MODEL` | User-facing chat model | `qwen3.5:9b` |
| `UTILITY_MODEL` | Routing / cheap extraction | `qwen3.5:4b` |
| `TOOLS_MODEL` | Tool loop (default = chat model) | `qwen3.5:9b` |
| `EMBEDDING_MODEL` | Embedding model (1024-dim) | `qwen3-embedding:0.6b` |
| `MAX_MODEL` | On-demand escalation tier | `qwen3.8:27b` |
| `MATH_MODEL` | On-demand `compute` tool | `qwen3.8:27b` |
| `BACKEND_HOST` | FastAPI bind host | `127.0.0.1` |
| `BACKEND_PORT` | FastAPI port | `8000` |
| `DATABASE_PATH` | SQLite path | `./assistant.db` |
| `SEARCH_BASE_URL` | SearXNG URL | `http://127.0.0.1:8080` |
| `SEARCH_TIMEOUT` | Search timeout | `90.0` |
| `WHISPER_MODEL` | Voice transcription model | `base` |
| `WHISPER_DEVICE` | Transcription device | `cpu` |
| `SCHEDULER_ENABLED` | Run the background scheduler | `true` |
| `DAILY_TASKS_TIME` | Daily task tick (HH:MM) | `09:00` |
| `EMBEDDING_TOPUP_INTERVAL_HOURS` | Embedding re-index cadence (0 = off) | `6` |
| `CONSOLIDATION_INTERVAL_HOURS` | Duplicate-frame check cadence (0 = off) | `6` |
| `BACKUP_INTERVAL_HOURS` | Periodic brain snapshot cadence (0 = off) | `12` |
| `SUMMARIZATION_ENABLED` | Compress sessions into summary frames | `true` |
| `SUMMARIZATION_INTERVAL_HOURS` | Summarization cadence | `6` |

---

## 8. Testing strategy

Detailed in [`docs/TESTING.md`](./docs/TESTING.md). Summary:

- **Tier 1** — Core algorithms (confidence, conflict resolution)
- **Tier 2** — Pipeline logic (extractor, retrieval, reasoner, task router)
- **Tier 3** — End-to-end flows (learn-then-recall, contradiction-then-resolve, search-learn-recall)
- **Tier 4** — Operational smoke (security, Docker, CLI)

Run tests with:

```bash
docker run --rm -v $(pwd):/app -w /app assistant pytest assistant/tests/
```

Lint with:

```bash
docker run --rm -v $(pwd):/app -w /app assistant ruff check .
```

---

## 9. Key file map

| File | Responsibility |
|------|----------------|
| `assistant/backend/config.py` | Pydantic settings |
| `assistant/backend/main.py` | FastAPI app + routes |
| `assistant/backend/pipeline/orchestrator.py` | Cognitive loop |
| `assistant/backend/pipeline/llm_client.py` | Ollama client |
| `assistant/backend/pipeline/task_router.py` | Intent classification |
| `assistant/backend/pipeline/reasoner.py` | Action planning |
| `assistant/backend/pipeline/extractor.py` | Fact extraction + correction validation |
| `assistant/backend/pipeline/search.py` | SearXNG search backend (+ optional Brave) |
| `assistant/backend/pipeline/whisper.py` | Local voice transcription |
| `assistant/backend/memory/store.py` | SQLite CRUD |
| `assistant/backend/memory/models.py` | Pydantic data models |
| `assistant/backend/memory/retrieval.py` | Memory retrieval + context formatting |
| `assistant/backend/memory/confidence.py` | Confidence and conflict math |
| `assistant/backend/memory/belief_revision.py` | AGM expand/revise/contract operators |
| `assistant/backend/memory/consolidate.py` | Near-duplicate frame merge |
| `assistant/backend/scheduler/runner.py` | Background loop: daily ticks + housekeeping timers |
| `assistant/backend/scheduler/summarizer.py` | Session → summary frame compression |
| `frontend/src/` | SolidJS SPA (the live UI) |
| `assistant/backend/static/` | Built SPA bundle (committed, served by Caddy) |
| `assistant/cli/app.py` | CLI client |

---

## 10. How to extend

- **New memory source type** — add a value to `source_type`, ensure `upsert_slot` preserves it, update `format_memory_context` and CLI display.
- **New model role** — add field to `OllamaClient` and `Settings`; use it in the relevant pipeline module.
- **New task type** — update `task_router.py` heuristics and `reasoner.py` action mapping.
- **New CLI command** — add a subparser in `cli/app.py` and a corresponding API endpoint if needed.
- **New web UI feature** — edit `frontend/src/`; the SPA is built with Vite (`npm run build` in `frontend/`).

---

## 11. See also

- [`/AGENTS.md`](./AGENTS.md) — agent instructions, security constraints, build commands
- [`/assistant/AGENTS.md`](./assistant/AGENTS.md) — backend cognitive architecture and memory model
- [`docs/TESTING.md`](./docs/TESTING.md) — testing strategy
- [`docs/MODEL_SELECTION.md`](./docs/MODEL_SELECTION.md) — model fleet rationale
- [`docs/SECURITY.md`](./docs/SECURITY.md) — security model and verification
- [`README.md`](./README.md) — first-run setup and configuration

---

*Last updated: 2026-09-29*
