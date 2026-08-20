# AGENTS.md — /assistant/

## Architecture
FastAPI backend + CLI client. Ollama for LLM inference (two models: 7b for chat,
3b for extraction/routing). SQLite + sqlite-vec for local memory storage.
Web search for retrieval-only; learned facts stored locally in memory frames/slots.

## Web Search
- Uses SearXNG (privacy-friendly meta-search engine) for external retrieval.
- Search results are **retrieval-only** unless explicitly worth learning.
- The LLM decides which facts to retain from search results.
- Configurable: `SEARCH_ENABLED=true` in `.env` to enable web search.
- Default: `SEARCH_ENABLED=true` for useful assistant functionality.
- All search queries go to local SearXNG instance (not cloud APIs).

## The cognitive loop
1. Task Router classifies input: functional (goal-directed) vs introspective (reflective).
   Heuristic first; LLM fallback (3b model) only for ambiguous cases.
2. For functional queries requiring external info: fetch via search engine (SearXNG).
3. Retrieve: embed query → sqlite-vec similarity → graph-walk associations → memory context.
4. LLM call (7b): system prompt injects structured memory context + task-type guidance.
5. Response to user.
6. Async extraction (3b model, fire-and-forget): extract frames/slots/associations as JSON → upsert with confidence → auto-resolve conflicts → log to slot_history.



## Memory model
- Frames: entities/concepts/events with confidence.
- Slots: key/value pairs on a frame, each with confidence + source episode.
- Associations: typed relations between frames (graph), each with confidence.
- Episodes: per-user conversation turns, linked to touched frames.
- Conflicts: when a new slot value contradicts existing — auto-resolve by recency+confidence,
  old value preserved in slot_history, flagged for user review via CLI.
- Embeddings: frame name+slot summary, via nomic-embed-text, stored in sqlite-vec.

## Confidence rules (assistant/backend/memory/confidence.py)
- New slot value: confidence 0.5.
- Repeated same value: confidence increases: `conf = 1 - (1-conf)*0.7` (bounded).
- Conflicting value: auto-resolve. If new value has higher confidence OR is more recent
  (within same session), it wins; old value → slot_history. Both logged in conflicts table.
- Association confidence: increases with co-occurrence in episodes.

## Two-model config
- Chat model (default `qwen2.5:7b`): handles user-facing responses.
- Utility model (default `qwen2.5:3b`): handles extraction + task-routing fallback.
- Both via Ollama. Configurable in `.env`: `CHAT_MODEL`, `UTILITY_MODEL`, `OLLAMA_URL`.

## Key files
- `backend/memory/store.py` — MemoryStore CRUD over SQLite.
- `backend/memory/confidence.py` — confidence + conflict math. Single source of truth.
- `backend/memory/retrieval.py` — embed + sqlite-vec + graph-walk.
- `backend/pipeline/task_router.py` — functional/introspective classification.
- `backend/pipeline/extractor.py` — async fact extraction (JSON output from LLM).
- `backend/pipeline/llm_client.py` — Ollama client (chat + embeddings).
- `backend/main.py` — FastAPI app with all endpoints.
- `cli/app.py` — CLI entry point.

## Testing
- Unit tests per module under `assistant/tests/`.
- `tests/test_learning_loop.py` — learn-then-recall end-to-end.
- `tests/test_conflict_resolution.py` — contradiction-then-auto-resolve.
- `tests/test_user_isolation.py` — multi-user episodic privacy.
- Run full suite (plain + encrypted): `./run_ci.sh`
- Run single test mode: `docker compose -f docker-compose.test.yml run --rm test-plain`

## Don't
- Don't run extraction synchronously in the chat request — it's async, fire-and-forget.
- Don't make a second blocking LLM call for task routing when heuristics suffice.
- Don't bind FastAPI to anything but 127.0.0.1.
- Don't add cloud LLM APIs (OpenAI, Anthropic, Google, etc.) — all inference via local Ollama.
- Don't skip SearXNG localhost binding when using search.
