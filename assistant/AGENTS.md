# AGENTS.md — /assistant/

## Architecture
FastAPI backend + CLI client. Ollama for LLM inference (role-based fleet: chat, utility,
embedding; coder reserved). SQLite + sqlite-vec for local memory storage.
Web search for retrieval-only; learned facts stored locally in memory frames/slots.

## Web Search
- Uses SearXNG (privacy-friendly meta-search engine) for external retrieval.
- Search results are **retrieval-only** unless explicitly worth learning.
- The LLM decides which facts to retain from search results.
- Always on by design — no feature flag; point `SEARCH_BASE_URL` at your local SearXNG.
- Engine roster is curated in `searxng/settings.yml`. Engines that serve
  CAPTCHAs on every request are removed outright (they retry-and-fail on
  each query); rate-limited engines that self-heal (e.g. Brave 429s) stay.

## The cognitive loop
1. Task Router classifies input: functional (goal-directed) vs introspective (reflective).
   Heuristic first; LLM fallback (utility model) for ambiguous cases. The same LLM pass
   also returns `wants_search` — false for storage-style turns and general-knowledge
   questions, so personal facts are never forwarded to external search.
2. For functional queries requiring external info: fetch via search engine (SearXNG).
3. Retrieve: embed query → sqlite-vec similarity → graph-walk associations →
   memory context. Past conversations are also searched semantically
   (`episode_embeddings`): turns from other sessions matching the query land in
   a "Related past conversations" section (owner-scoped, current session excluded).
4. Conversational extraction (utility model): extract frames/slots/associations as JSON →
   upsert with confidence → auto-resolve conflicts → log to slot_history. Runs BEFORE
   generation so just-stored facts can be injected into the system prompt.
5. LLM call (chat model): system prompt injects structured memory context + task-type guidance.
   Sections are ordered stable-first, volatile-last (persona → task guidance → plan
   instructions → memory) so Ollama's prompt cache reuses the stable prefix across
   consecutive turns. Recent episodes appear in memory context as 240-char digests;
   the last 6 turns still arrive verbatim as message history.
6. Response to user (`ChatResponse` with `extraction_summary` + `search_extraction_summary`).
7. Search extraction (utility model): runs only when a search actually happened; deduped
   against conversational slots by (frame_name, value).

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
- Conflicts: when a new slot value contradicts existing — auto-resolve by a fixed ladder
  (source_reliability → confidence → priority → recency tiebreak). Old value preserved
  in slot_history, flagged for user review via CLI or web UI.
- Embeddings: frame type + name + slot key=value lines, via nomic-embed-text, stored
  in sqlite-vec. Conversation turns get their own vectors (`episode_embeddings`) at
  write time (best-effort; consolidation tops up misses).

## Correction pipeline
1. User flags a response → `POST /correction` with `correction_text`.
2. `extract_correction`: LLM parses intent (frame, slot, new value).
3. `validate_correction`: checks corroboration via web search; if contradicted, rejects without applying.
4. `apply_correction`: updates slot value + reliability via `upsert_slot`, which internally
   runs `revise()` for AGM belief revision. Creates frame if missing. `expand` and `contract`
   are standalone AGM operators (not part of the correction pipeline).
5. Orchestrator returns `CorrectionResponse` with slots_corrected, conflict, validation_summary.
6. System prompt acknowledges auto-resolved conflicts (surfaced via search extraction summary).

## Feedback pipeline
- `POST /feedback` with `kind=positive|negative|correction`.
- Positive: `bump_confidence` (up to MAX_CONFIDENCE).
- Negative: `lower_confidence` (down to initial_confidence=0.5).
- Correction: records the text for audit only — applying it goes through
  `POST /correction` (the LLM correction pipeline above).

## Brain portability
- `POST /brain/export`: serializes all frames, slots, associations, conflicts, episodes,
  and feedbacks to JSON.
- `POST /brain/import`: loads JSON, supports merge (accumulate) and overwrite (replace) modes.
- Overwrite mode auto-backs up existing DB via `shutil.copy2`.

## Confidence rules (assistant/backend/memory/confidence.py)
- New slot value: confidence 0.5.
- Repeated same value: confidence increases: `conf = 1 - (1-conf)*0.7` (bounded).
- Conflicting value: auto-resolve by a fixed ladder — source_reliability →
  confidence → priority → recency tiebreak; loser value → slot_history.
  Both logged in conflicts table.
- Positive feedback: `bump_confidence(current)` = `min(1 - (1-current)*0.7, 0.99)`
  (same repeat-discount curve as reinforcement).
- Negative feedback: `lower_confidence(current)` = `max(current - 0.15, INITIAL_CONFIDENCE)`.
- Association confidence: increases with co-occurrence in episodes (batch process
  during consolidation, not real-time).

## Model fleet config
Role-based model selection. Configurable in `.env`: `CHAT_MODEL`, `UTILITY_MODEL`,
`EMBEDDING_MODEL`, `CODER_MODEL` (reserved, empty = chat model), `OLLAMA_URL`.
- Chat model (default `qwen2.5:7b`): user-facing responses. Thinking-capable models accept
  per-request `think=True/False` (`OllamaClient.chat`); inline `<think>` tags are parsed
  out by the LLM client into a separate `thinking` field on the internal response.
- Utility model (default `qwen2.5:3b`): extraction, task-routing fallback.
- Embedding model (default `nomic-embed-text`): frame/query embeddings.
- Coder model: reserved for tool codegen; falls back to chat model.
- No separate router/reasoning models: routing reuses utility; reasoning is a thinking-mode
  escalation on the chat model.
- `/health` reports the full fleet in a `models` dict plus `thinking_supported`
  (probed via `/api/show`). Fleet details live in `backend/config.py`.

## Scheduled tasks — the daily list
- One clock: the agent wakes once a day at `DAILY_TASKS_TIME` (default `09:00`, 24h)
  in `DAILY_TASKS_TZ` (default: `TZ` env or host-local zone).
- Chat is the only interface. "Add an AI briefing to my mornings" → task stored;
  "stop doing X" → removed; "run my briefing now" → immediate execution.
- Task kinds: `daily` (runs every tick until the user asks to stop) and `once`
  (next tick, then auto-disabled). Stored on scheduled_task frames in the
  `schedule_cron` column (legacy column name; now holds the frequency tag).
- Extraction: utility model returns `{intent, name, description, prompt, repeat}`
  (`repeat: false` for one-shots like "remind me tomorrow"). No cron generation —
  the old NL→cron parser and croniter dependency were removed.
- Runner (`scheduler/runner.py`): 20s poll loop; fires due tasks through the full
  orchestrator (search + thinking + extraction) so results become memory.
  Housekeeping timers: heartbeat every 30 min, memory GC weekly (ISO-week change
  detection), embedding consolidation every 12h (tops up frame + episode embedding
  coverage, calls the embedding model). No chat/reasoning LLM calls in housekeeping.
- Missed ticks (backend down at 09:00) fire once late on restart, then reschedule.
- API: `GET /tasks`, `DELETE /tasks/{id}`, `GET /tasks/{id}/result`.
- Tests: `assistant/tests/test_daily_schedule.py`.

## Key files
- `backend/memory/store.py` — MemoryStore CRUD over SQLite; `export_brain`/`import_brain`.
- `backend/memory/gc.py` — all memory GC: slot priority decay + stale-frame soft-delete
  (`run_gc`); runs weekly in the scheduler and on-demand via `assistant db gc`.
- `backend/static/brain.html` — Brain Observatory: force-graph of frames/associations,
  conflict resolution, and topic memory search (`GET /memory/search` unions semantic
  frame matches with keyword hits, returning slots, associations, source episodes,
  and pending conflicts per match).
- `backend/static/chat.html` — chat UI; mic button is one-shot dictation into the
  input (continuous conversation mode is the separate toggle); conversation history
  restores from `GET /chat/session/{id}/messages` on reload.
- `backend/memory/confidence.py` — confidence + conflict math. Single source of truth.
- `backend/memory/retrieval.py` — embed + sqlite-vec + graph-walk.
- `backend/pipeline/task_router.py` — functional/introspective/scheduled classification.
- `backend/pipeline/extractor.py` — fact extraction + correction pipeline + scheduled task extraction.
- `backend/pipeline/reasoner.py` — planning + self-correction (`Action.CORRECT`).
- `backend/pipeline/orchestrator.py` — coordinates full cognitive loop; `execute_task()`.
- `backend/pipeline/llm_client.py` — Ollama client (chat + embeddings).
- `backend/scheduler/schedule.py` — the daily clock (tick computation, tz handling).
- `backend/scheduler/runner.py` — scheduler loop (daily-list firing + housekeeping timers).
- `backend/main.py` — FastAPI app with all endpoints.
- `cli/app.py` — CLI entry point.

## UI / UX Standards
- **Browser support:** Latest Firefox and Chromium (Chrome/Edge). Voice mode audio uses progressive MIME type detection (`audio/ogg` → `audio/wav` → `audio/webm` → `audio/mp4`) so every browser gets its best-supported format.
- **Icons:** Use SVG icons only (no emoji). Preferred source: [SVGrepo](https://www.svgrepo.com/).
  All SVG icons should be inlined as `<svg>` elements in HTML — no external icon font dependencies.
- **Links:** All links must open in a new tab (`target="_blank"`) so users don't lose their session.
- **Interactions:** Show feedback on user actions (copied toast, loading states, confirmation messages).
