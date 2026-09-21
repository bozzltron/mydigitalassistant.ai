# Current Project State — September 2026

## Overview
Privacy-first cognitive digital assistant with local LLM (Ollama), SQLite+sqlite-vec memory, SearXNG search (optional Brave), scheduled tasks, and conversation summarization.

**Stack:** FastAPI + SolidJS (no build step) + Docker Compose + Caddy HTTPS proxy

---

## Active Features (Implemented & Tested)

### Memory System
- Frames/slots/associations/episodes with confidence scoring
- Conflict auto-resolution (AGM belief revision)
- sqlite-vec semantic search + graph-walk associations
- Episode embeddings for conversation recall
- Consolidation (dedup + association strengthening) runs 2x/day
- Memory GC (slot decay + stale frame soft-delete) runs weekly

### Cognitive Loop
1. Route: functional/introspective/scheduled (utility model, heuristic fallback)
2. Retrieve: embed query → vec similarity → graph walk → episode recall
3. Reason: memory sufficiency → plan (search/answer/correct)
4. Extract: user facts → upsert (before generation for acknowledgment)
5. Search (if needed): SearXNG/Brave → relevance filter → extract → dedupe
6. Generate: chat model with system prompt (memory + plan + self-context)
7. Log: episodes + alerts for learning events

### Scheduled Tasks ("Daily List")
- One daily tick (configurable time/TZ)
- Task = `scheduled_task` frame with slots (prompt, frequency, enabled, next_run, last_run)
- Runs via full orchestrator chat loop → outputs become episodes
- Daily-run event frames link tasks + episodes for queryability
- API: GET/DELETE /tasks, GET /tasks/{id}/result

### Web Search
- **Default:** SearXNG (local, no query leaves machine)
- **Optional:** Brave Search API (opt-in: `BRAVE_ENABLED=true` + `BRAVE_API_KEY`)
- Relevance filter: embedding cosine similarity threshold
  - SearXNG: `SEARCH_MIN_RELEVANCE=0.30`
  - Brave: `BRAVE_SEARCH_MIN_RELEVANCE=0.20` (cleaner index)
- Brave: larger extraction budget, lower threshold, full-page fetch for top results
- Sensitivity gate: utility model classifies query before Brave call (PII/medical/financial/credentials)
- User consent required for sensitive/ambiguous queries

### Conversation Summarization (Background)
- Runs daily in scheduler (configurable interval)
- Session-scoped: one summary frame per session (`conversation_summary_{session_id}`)
- Utility model extracts: summary, key_entities, open_questions, turn_count, date_range
- Stored as frames with embeddings; linked to episodes via `summarizes` associations
- Idempotent upsert; included in retrieval as "Conversation summaries" section
- Config: `SUMMARIZATION_ENABLED=true`, `SUMMARIZATION_MIN_TURNS=10`, `MAX_SESSIONS_PER_RUN=5`

### Math Computation
- Dedicated `MATH_MODEL` (qwen3-coder:30b) with Python tool execution
- Keyword detection triggers sandboxed Python (numpy/scipy/sympy/pandas allowed)
- Results stored in memory with `source_type=computation`

### Brain Portability
- Encrypted portable export/import (`.assistant-brain` v3 format, SQLCipher)
- CLI: `assistant db export-portable` / `import-portable`
- Full-DB backup/restore: `assistant db backup` / `restore`

---

## Recent Changes (This Session)

### Fixed
1. **Embedding model missing** — Ollama needs `nomic-embed-text` pulled (400 Bad Request on `/api/embeddings`)
2. **Brave sensitivity false positive** — "jason lee" (actor) flagged as PII. Updated classifier prompt with explicit public figure examples
3. **XSS risk in markdown** — `MessageContent.tsx` uses `innerHTML={marked.parse()}` without sanitization
4. **Search veto bug** — `orchestrator.py:385` accesses `classification` when `skip_route=True`
5. **Type safety in media extraction** — Added `typeof === 'string'` guards for thumbnails/URLs

### Added
1. **SolidJS dev notes** in `/assistant/AGENTS.md` — refs, innerHTML, XSS patterns
2. **Environment variable documentation** aligned with `config.py` defaults

---

## Environment Configuration (`.env`)

```bash
# Required
OLLAMA_URL=http://127.0.0.1:11434
CHAT_MODEL=qwen2.5:7b
UTILITY_MODEL=qwen3.5:4b
EMBEDDING_MODEL=nomic-embed-text
MATH_MODEL=qwen3-coder:30b

# Brave Search (optional)
BRAVE_ENABLED=true
BRAVE_API_KEY=your_key_here
BRAVE_SEARCH_MIN_RELEVANCE=0.20

# Scheduler
SCHEDULER_ENABLED=true
DAILY_TASKS_TIME=09:00
DAILY_TASKS_TZ=America/Chicago
TZ=America/Chicago

# Summarization
SUMMARIZATION_ENABLED=true
SUMMARIZATION_INTERVAL_HOURS=24
SUMMARIZATION_MIN_TURNS=10
SUMMARIZATION_MAX_SESSIONS_PER_RUN=5
SUMMARIZATION_MAX_CHARS=4000
SUMMARIZATION_TIMEOUT_SECONDS=60

# Consolidation
CONSOLIDATION_INTERVAL_HOURS=12
CONSOLIDATION_MAX_MERGES_PER_RUN=10

# Retrieval
RETRIEVAL_MIN_DISTANCE=0.7
RETRIEVAL_EPISODE_LIMIT=3

# Working Memory
WORKING_MEMORY_MAX_SIZE=50
WORKING_MEMORY_BOOST=1.5

# Database
DATABASE_PATH=/app/data/assistant.db
DB_KEY=your_encryption_key

# Search
SEARCH_BASE_URL=http://searxng:8080
SEARCH_TIMEOUT=30
SEARCH_LANGUAGE=en
SEARCH_SAFESEARCH=1
SEARCH_MIN_RELEVANCE=0.30

# Backend
BACKEND_HOST=0.0.0.0
BACKEND_PORT=8000
```

---

## Known Issues / Tech Debt

### High Priority
| Issue | Location | Fix |
|-------|----------|-----|
| XSS in LLM markdown output | `MessageContent.tsx:43` | Sanitize with DOMPurify before `innerHTML` |
| Search veto bug | `orchestrator.py:385` | Guard `classification` access when `skip_route=True` |
| No streaming responses | `orchestrator.py:868` | Implement token streaming for 27B model UX |

### Medium Priority
| Issue | Location | Fix |
|-------|----------|-----|
| `innerHTML` in tooltips | `BrainGraph.tsx:525`, `BrainGraphSigma.tsx:358` | Use `textContent` or sanitize |
| Direct DOM manipulation | `TopBar.tsx:108`, `BrainGraphSigma.tsx:358` | Use Solid refs + `onMount` |
| `ExtractedSlot.source_domains: set[str]` | `extractor.py:35` | Change to `list[str]` for Pydantic JSON serialization |
| Nested closures in `embed_fn` | `orchestrator.py:166-177` | Simplify to single method |

### Low Priority
| Issue | Location | Fix |
|-------|----------|-----|
| No shared Icon component | Multiple TSX files | Extract SVG icons to reusable component |
| Hardcoded SVG in PreviewCards | `PreviewCards.tsx` | Use shared icons |
| Consolidation embed_fn signature mismatch | `consolidate.py:335` | Document that list input is supported |

---

## Test Status

```bash
# Backend (Docker)
docker run --rm -v $(pwd):/app -w /app assistant ruff check .        # PASS
docker run --rm -v $(pwd):/app -w /app assistant pytest assistant/tests/test_daily_schedule.py assistant/tests/test_review_fixes.py -x -q  # 22 PASS

# Critical paths covered:
# - run_now: scheduled task execution ✅
# - Correction pipeline: parse → validate → apply ✅
# - Search extraction + merge ✅
# - Voice: manual only (flaky in CI)
```

---

## Model Requirements (Ollama)

```bash
# Pull all required models:
ollama pull qwen2.5:7b          # Chat
ollama pull qwen3.5:4b          # Utility
ollama pull nomic-embed-text    # Embedding (CURRENT DEFAULT)
# OR: ollama pull qwen3-embedding:0.6b  # Target fleet embedding
ollama pull qwen3-coder:30b     # Math (large, ~17GB)

# Host Ollama config for model residency:
OLLAMA_KEEP_ALIVE=-1
OLLAMA_MAX_LOADED_MODELS=2
```

---

## Deployment

```bash
# Production
docker compose -f docker-compose.prod.yml up -d

# Development
docker compose up -d
# Frontend dev server: docker compose --profile dev up solid-dev

# Access: https://127.0.0.1:8443 (Caddy HTTPS)
# API: http://127.0.0.1:8000 (internal only)
```

---

## Architecture Principles (Enforced)

1. **Model-first** — LLM reasoning over scripted logic (corrections, extraction, routing)
2. **No templated responses** — Model generates all user-facing text
3. **Local-only inference** — No cloud LLM APIs ever
4. **Privacy by default** — SearXNG local; Brave opt-in with consent gate
5. **Scheduled tasks = memory** — No hidden scheduler state
6. **Clean ship** — Dead code removal via `ruff`, verified zero callers
7. **Regression tests for critical paths** — Before any merge

---

## Next Steps (Suggested Priority)

1. **Fix XSS** — Add DOMPurify to `MessageContent.tsx` and tooltips
2. **Fix search veto bug** — Guard classification access
3. **Add streaming** — Implement SSE/token streaming for chat responses
4. **Sanitize all innerHTML** — Audit and replace with `textContent` or sanitized HTML
5. **Extract search pipeline** — Move 175-line search block to `_run_search_pipeline()`
6. **Add Icon component** — Consolidate SVGs
7. **Run full test suite** — Including `test_summarizer.py`, `test_api.py` (needs python-multipart)