# AGENTS.md — /assistant/

## Architecture
FastAPI backend + CLI client. Ollama for LLM inference (role-based fleet: chat, utility,
embedding; coder reserved). SQLite + sqlite-vec for local memory storage.
Web search for retrieval-only; learned facts stored locally in memory frames/slots.

## Network Security
- Backend binds to `127.0.0.1:8000` (never exposed directly)
- HTTPS termination via Caddy reverse proxy on `127.0.0.1:8443`
- All services on internal Docker network (`appnet`)
- SearXNG binds to `127.0.0.1:8080` (localhost only)

## Web Search
- Uses SearXNG (privacy-friendly meta-search engine) for external retrieval.
- Search results are **retrieval-only** unless explicitly worth learning.
- The LLM decides which facts to retain from search results.
- Configurable: `SEARCH_ENABLED=true` in `.env` to enable web search.
- Default: `SEARCH_ENABLED=true` for useful assistant functionality.
- All search queries go to local SearXNG instance (not cloud APIs).

## The cognitive loop
1. Task Router classifies input: functional (goal-directed) vs introspective (reflective).
   Heuristic first; LLM fallback (utility model) for ambiguous cases. The same LLM pass
   also returns `wants_search` — false for storage-style turns and general-knowledge
   questions, so personal facts are never forwarded to external search.
2. For functional queries requiring external info: fetch via search engine (SearXNG).
3. Retrieve: embed query → sqlite-vec similarity → graph-walk associations → memory context.
4. LLM call (chat model): system prompt injects structured memory context + task-type guidance.
5. Response to user + extraction summary (`ChatResponse.extraction_summary`).
6. Synchronous extraction (utility model): extract frames/slots/associations as JSON → upsert with confidence → auto-resolve conflicts → log to slot_history.

Actual ordering inside `orchestrator.chat()`:
- Router (with wants_search) → retrieval → reasoner plan (search vetoed if
  `wants_search is False` and task_type != search) → correction branch if needed.
- Conversational extraction runs BEFORE generation; just-stored facts are injected
  into the system prompt ("Facts you just stored this turn") so the model can
  acknowledge them truthfully in its own words. No templated acknowledgments.
- Search extraction runs only when a search actually happened, and is deduped
  against conversational slots by (frame_name, value) — cross-key duplicates like
  "strings"/"number_of_strings" are dropped in favor of the earlier channel's key.



## Memory model
- Frames: entities/concepts/events with confidence.
- Slots: key/value pairs on a frame, each with confidence + source episode.
- Associations: typed relations between frames (graph), each with confidence.
- Episodes: per-user conversation turns, linked to touched frames.
- Conflicts: when a new slot value contradicts existing — auto-resolve by recency+confidence,
  old value preserved in slot_history, flagged for user review via CLI.
- Embeddings: frame name+slot summary, via nomic-embed-text, stored in sqlite-vec.

## Correction pipeline
1. User flags a response → `POST /correction` with `correction_text`.
2. `extract_correction`: LLM parses intent (frame, slot, new value).
3. `validate_correction`: checks corroboration (existing memory) and contradiction (active conflicts).
4. `apply_correction`: updates slot value + reliability; creates frame if missing.
5. Belief revision: `revise/expand/contract` per AGM postulates.
6. Orchestrator returns `CorrectionResponse` with slots_corrected, conflict, validation_summary.
7. System prompt acknowledges auto-resolved conflicts (surfaced via `search_extraction_summary.conflicts_created`).

## Feedback pipeline
- `POST /feedback` with `kind=positive|negative|correction`.
- Positive: `bump_confidence` (up to MAX_CONFIDENCE).
- Negative: `lower_confidence` (down to initial_confidence=0.5).
- Correction: triggers correction pipeline above.

## Brain portability
- `POST /brain/export`: serializes all frames/slots/associations/conflicts to JSON.
- `POST /brain/import`: loads JSON, supports merge (accumulate) and overwrite (replace) modes.
- Overwrite mode auto-backs up existing DB via `shutil.copy2`.

## Confidence rules (assistant/backend/memory/confidence.py)
- New slot value: confidence 0.5.
- Repeated same value: confidence increases: `conf = 1 - (1-conf)*0.7` (bounded).
- Conflicting value: auto-resolve. If new value has higher confidence OR is more recent
  (within same session), it wins; old value → slot_history. Both logged in conflicts table.
- Positive feedback: `bump_confidence(current)` = `min(current * 0.3 + current, MAX_CONFIDENCE)`.
- Negative feedback: `lower_confidence(current)` = `max(current - 0.15, INITIAL_CONFIDENCE)`.
- Association confidence: increases with co-occurrence in episodes.

## Model fleet config
Role-based model selection (Phase 6). Configurable in `.env`: `CHAT_MODEL`, `UTILITY_MODEL`,
`EMBEDDING_MODEL`, `CODER_MODEL` (reserved, empty = chat model), `OLLAMA_URL`.
- Chat model (default `qwen2.5:7b`): user-facing responses. Thinking-capable models accept
  per-request `think=True/False` (`OllamaClient.chat`); inline `<think>` tags are parsed
  out into `ChatResponse.thinking` automatically.
- Utility model (default `qwen2.5:3b`): extraction, task-routing fallback, cron generation.
- Embedding model (default `nomic-embed-text`): frame/query embeddings.
- Coder model: reserved for tool codegen (Phase 6 M5); falls back to chat model.
- No separate router/reasoning models: routing reuses utility; reasoning is a thinking-mode
  escalation on the chat model (Phase 6 plan §6).
- `/health` reports the full fleet in a `models` dict plus `thinking_supported`
  (probed via `/api/show`). Target fleet + migration gates: see
  `/plans/PHASE_6_MODEL_TIERING.md`.

## Scheduled tasks
- `POST /chat` with a scheduling intent → `TaskType.SCHEDULED` → orchestrator handles via `_handle_scheduled_task`.
- Task definitions stored as `scheduled_task` frames with `schedule_cron`, `prompt`, `enabled`, `last_run`, `next_run`, `last_result_summary`.
- Natural language → cron via `scheduler/cron.py` (`parse_schedule`). Supports: "every 30 minutes", "daily at 9am", "weekly on Monday", etc. Minimum interval: 5 minutes (normalized to 30 if shorter).
- Scheduler runs as a background asyncio task inside the backend (enabled via `SCHEDULER_ENABLED=true`). Loop: sleep until nearest `next_run`, execute due tasks, update `last_run`/`next_run`.
- Built-in system tasks: `system_memory_gc` (weekly, decay stale slots) and `system_scheduler_heartbeat` (every 30 min).
- Memory garbage collection (`store.gc()`): soft-deletes forgotten frames (priority < 0.2, unaccessed > 30 days) and decays stale slots (priority < 0.3, accessed > 60 days ago).
- User tasks run through the full orchestrator (search + LLM + extraction) so results become memory normally.
- API: `GET /tasks`, `DELETE /tasks/{id}`, `GET /tasks/{id}/result`.

## Key files
- `backend/memory/store.py` — MemoryStore CRUD over SQLite; `export_brain`/`import_brain`; `gc()`.
- `backend/memory/confidence.py` — confidence + conflict math. Single source of truth.
- `backend/memory/retrieval.py` — embed + sqlite-vec + graph-walk.
- `backend/pipeline/task_router.py` — functional/introspective/scheduled classification.
- `backend/pipeline/extractor.py` — fact extraction + correction pipeline + scheduled task extraction.
- `backend/pipeline/reasoner.py` — planning + self-correction (`Action.CORRECT`).
- `backend/pipeline/orchestrator.py` — coordinates full cognitive loop; `execute_task()`.
- `backend/pipeline/llm_client.py` — Ollama client (chat + embeddings).
- `backend/scheduler/cron.py` — natural language → cron parsing (`parse_schedule`).
- `backend/scheduler/runner.py` — scheduler loop (system tasks + user tasks).
- `backend/main.py` — FastAPI app with all endpoints.
- `eval/dataset.json` — evaluation cases (5 sample cases).
- `eval/runner.py` — evaluation engine with keyword-matching grader.
- `cli/app.py` — CLI entry point.

## Testing
- Unit tests per module under `assistant/tests/`.
- `tests/test_learning_loop.py` — learn-then-recall end-to-end.
- `tests/test_conflict_resolution.py` — contradiction-then-auto-resolve.
- `tests/test_user_isolation.py` — multi-user episodic privacy.
- Run full suite (plain + encrypted): `./run_ci.sh`
- Run single test mode: `docker compose -f docker-compose.test.yml run --rm test-plain`

## Don't
- Don't make a second blocking LLM call for task routing when heuristics suffice.
- Don't expose backend directly — always route through Caddy HTTPS proxy.
- Don't add cloud LLM APIs (OpenAI, Anthropic, Google, etc.) — all inference via local Ollama.
- Don't skip SearXNG localhost binding when using search.

## UI / UX Standards
- **Browser support:** Latest Firefox and Chromium (Chrome/Edge). Voice mode audio uses progressive MIME type detection (`audio/ogg` → `audio/wav` → `audio/webm` → `audio/mp4`) so every browser gets its best-supported format.
- **Icons:** Use SVG icons only (no emoji). Preferred source: [SVGrepo](https://www.svgrepo.com/).
  All SVG icons should be inlined as `<svg>` elements in HTML — no external icon font dependencies.
- **Links:** All links must open in a new tab (`target="_blank"`) so users don't lose their session.
- **Interactions:** Show feedback on user actions (copied toast, loading states, confirmation messages).
