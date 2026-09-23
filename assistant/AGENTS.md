# AGENTS.md — /assistant/

## Architecture
FastAPI backend + CLI client. Ollama for LLM inference (role-based fleet: chat, utility,
embedding; coder reserved). SQLite + sqlite-vec for local memory storage.
Web search for retrieval-only; learned facts stored locally in memory frames/slots.

## Performance / latency
- Response latency is a first-class user-experience goal. Keep chat response time as low
  as possible; a slow assistant feels broken even when the answer is correct.
- Avoid synchronous model switching on the hot path. Loading/unloading models in Ollama
  adds noticeable overhead; prefer batching, caching, and design choices that stay within
  the already-loaded model set.
- Parallelize external work (e.g. fetching multiple result bodies) and cap timeouts so a
  slow source cannot block the user.
- Measure before adding new models, new network calls, or new LLM invocations to the
  synchronous response path.

### Known Issue: Tool Calling Timeout
**Current behavior:** Tool calling runs on `utility_model` (default qwen2.5:3b / qwen3.5:4b) 
via `run_tool_loop()`. The tool loop makes multiple sequential LLM calls (up to `MAX_TOOL_ROUNDS=3`),
each with full system prompt + tools schema + conversation history.

**Symptoms:**
- First tool call: 5-15s (cold model + large prefill)
- Subsequent turns: 3-10s each
- Total turn time can exceed 30-60s, causing HTTP timeouts at reverse proxy (Caddy default 30s)

**Root causes:**
1. System prompt + tools schema = ~4000-6000 tokens prefill per call
2. Sequential calls (no parallelization within tool loop)
3. 4B model still slow on tool-calling workloads with many tools (17+)

**Mitigations (planned):**
1. **Streaming responses** — return first token immediately, reduce perceived latency
2. **Reduce tool count** for tool model — only expose tools it actually needs
3. **Smaller context** for tool calls — strip memory context from tool loop iterations
4. **Dedicated fast tools model** — 1.5B model fine-tuned for function calling
5. **Async tool execution** — parallelize independent tool calls (currently sequential)

**Workaround:** Increase Caddy `response_header_timeout` and client timeouts for tool-heavy sessions.

## Web Search
- **Default backend:** local SearXNG instance at `SEARCH_BASE_URL`. No query leaves
  the machine in the default configuration.
- **Optional backend:** Brave Search API. The only permitted non-local search
  provider because it does not profile users or sell query data. Disabled by
  default; requires both `BRAVE_ENABLED=true` and `BRAVE_API_KEY` to activate.
  When enabled, sanitized query text and the user's IP address are sent to
  Brave's servers.
- Search results are **retrieval-only** unless they pass extraction as
  high-signal, corroborated facts.
- Always on by design — no feature flag for search itself; only the backend is
  configurable.
- SearXNG engine roster is curated in `searxng/settings.yml`. Engines that serve
  CAPTCHAs on every request are removed outright; rate-limited engines that
  self-heal (e.g. Brave 429s) stay.

## Search Learning
Search-derived facts enter memory only when they are accurate and useful:

1. **Extract from snippets first.** The utility model extracts slots/associations
   from result titles and snippets.
2. **Fetch top result bodies when quality supports it.** For Brave results,
   fetch the top 2–3 pages in parallel and extract additional facts from full
   content. Degrade gracefully on fetch failure or timeout.
3. **Corroborate.** A fact mentioned by multiple independent sources gets a
   source-reliability bonus.
4. **Backend-aware reliability.** Brave facts start with higher source reliability
   than SearXNG/Bing facts because Brave's index is measurably cleaner.
5. **Deduplicate across channels.** Search-extracted facts are deduped against
   conversational slots by `(frame_name, value)` so the same fact does not
   inflate memory under two different keys.
6. **Conflict and audit.** Conflicting search facts are auto-resolved by the
   standard confidence ladder and preserved in `slot_history`. The UI surfaces
   auto-resolved conflicts in the trace panel and “What I learned” indicator.
7. **Per-slot source traceability.** Every extracted slot carries `source_urls`
   (list of URLs where the fact appeared) and `source_domains` (unique domains).
   This enables UI transparency and powers the corroboration gate: high-stakes
   facts (financial, medical, legal, safety) require >=2 unique domains or are
   flagged with reduced confidence.

## The cognitive loop
1. Task Router classifies input: functional (goal-directed) vs introspective (reflective).
   Heuristic first; LLM fallback (utility model) for ambiguous cases. The same LLM pass
   also returns `wants_search` — false for storage-style turns and general-knowledge
   questions, so personal facts are never forwarded to external search.
2. For functional queries requiring external info: fetch via the configured search
   backend (SearXNG by default, optional Brave Search API).
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

## Design principles

### Model-first correction
When the utility model cannot parse a user correction (or any ambiguous input), pass the
user's message to the chat model rather than generating a scripted fallback. The model's own
reasoning should determine the response — no hardcoded text branches keyed on what the user
might have typed. This applies everywhere: corrections, clarifications, reframing, and any
other case where parsing fails.

### No templated responses
The model speaks for itself. Avoid hardcoded acknowledgment templates ("Got it, I've updated...")
in favor of letting the model generate responses from the facts it has access to.

### Lean on the model's flexibility
Everywhere the design allows a choice between "scripted logic" and "model reasoning," prefer
the model. For example: extract facts from search via the model rather than keyword rules;
route ambiguous inputs via the model rather than heuristic classifiers; respond to corrections
via the model rather than fixed response templates.

### Scheduled tasks are memory
Scheduled tasks, their outputs, and the fact that a daily run occurred are all first-class
memory objects — frames, slots, associations, episodes. There are no scheduler-only hidden
columns. A task is a `scheduled_task` frame whose `prompt`, `frequency`, `enabled`, `next_run`,
and `last_run` are slots. Each morning's run is an `event` frame associated with the tasks that
ran and their output episodes. The user can ask "What did my briefing find?" because the
answer is ordinary memory.

### Clean ship
Dead code is a liability. Unused imports, dead helper functions, and commented-out
snippets make the codebase harder to reason about and increase the risk of breaking
something that still matters. Before shipping a change:
- Run `ruff check --select=F401,F811` to surface unused imports and variables.
- Before removing any function or class, verify it has zero callers across the entire
  codebase — including tests. If in doubt, add a test proving it can be removed rather
  than deleting blind.
- A periodic "dead code audit" is fine; a blanket "delete unused code" without tooling
  verification is not. The goal is a codebase where nothing exists without purpose.

### Stability: no regressions while adding features
Every non-trivial change should be accompanied by a regression test — a test that would
have caught the bug before the fix ships. This builds the suite in the direction of
real bugs rather than abstract coverage.
Critical paths that need regression tests (in priority order):
1. **Voice recording flow**: silence detection → stopRecording → transcription → sendMessage.
   *Note: No automated tests exist for this path. Voice/audio I/O with silence detection
   and transcription is inherently flaky in CI. Manual verification recommended.*
2. **run_now**: scheduled task found by name → executed → last_run updated → once disabled.
3. **Correction pipeline**: parse → validate → apply → response.
4. **Search extraction + merge**: snippet extract → deduplication → document extract → merge.

### Safety & Privacy: user data protection
The system is designed to support and protect the user and their data. 
The agent must confirm before performing actions that could expose sensitive information:

- **Search confirmation**: Before sending queries to external search APIs (Brave), confirm the user wants to send that data. Local SearXNG searches stay on-device.
- **File upload awareness**: When uploading files to chat, show what content will be sent to the LLM (preview, entities, questions).
- **Web fetch transparency**: The `fetch_url` tool shows the target URL before fetching and extracting facts.
- **Data locality**: All LLM inference runs locally via Ollama (127.0.0.1). No data leaves the machine unless the user explicitly opts into Brave Search.
- **No telemetry**: Zero analytics, tracking, or phone-home code. The agent's memory stays in the local SQLite database.
- **User consent for external calls**: Any network request beyond localhost (Ollama, SearXNG, Brave) requires explicit user action or configuration.

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
- **Portable brains**: `POST /brain/export-portable` and `POST /brain/import-portable` API
  endpoints; `assistant db export-portable` and `assistant db import-portable` CLI commands.
  Exports the live SQLCipher database as an encrypted, version-3 `.assistant-brain` file
  that can be renamed, copied, and restored on any instance sharing the same `DB_KEY`.
  A pre-restore backup of the current brain is created automatically before import.
- **Full-DB backup/restore**: `assistant db backup` and `assistant db restore` CLI commands
  create and restore encrypted JSON bundles. Still useful for point-in-time snapshots.

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
- **Enabled by default.** The scheduler starts with the backend unless `SCHEDULER_ENABLED=false`.
- **One clock:** the agent wakes once a day at `DAILY_TASKS_TIME` (default `09:00`, 24h)
  in `DAILY_TASKS_TZ` (default: `TZ` env or host-local zone).
- **Chat is the only interface.** "Add an AI briefing to my mornings" → task stored;
  "stop doing X" → removed; "run my briefing now" → immediate execution.
- **Tasks are memory.** Each task is a `scheduled_task` frame with slots for `prompt`,
  `frequency` (`daily`/`once`), `enabled`, `next_run`, `last_run`, and `description`.
  No hidden scheduler-only columns.
- **Execution uses the full chat loop.** When a task fires, the scheduler calls
  `orchestrator.chat()` with the task prompt as the user message. The task gets the
  same retrieval, search, reasoning, extraction, conflict resolution, and episode
  logging as any chat turn.
- **Daily-run event frames.** Each morning the scheduler creates/updates an `event`
  frame named `daily_run_YYYY_MM_DD`. It records `date`, `tasks_run`, and `status`,
  and associations link each task frame to the run and to its output episode.
- **Outputs are queryable.** The assistant's response from a task run is a normal
  assistant episode. The user can later ask "What did my morning briefing find?"
  and retrieval will surface it.
- **Task kinds:** `daily` (runs every tick until stopped) and `once` (next tick, then
  disabled). No cron expressions — one shared daily tick.
- **Extraction:** utility model returns `{intent, name, description, prompt, repeat}`
  (`repeat: false` for one-shots). The old NL→cron parser and `croniter` dependency
  were removed.
- **Runner (`scheduler/runner.py`):** 20s poll loop; fires due tasks, creates the daily-run
  event frame, links associations, and reschedules. Housekeeping timers: heartbeat every
  30 min, memory GC weekly, embedding consolidation every 12h.
- **Missed ticks** (backend down at 09:00) fire once late on restart, then reschedule.
- **API:** `GET /tasks`, `DELETE /tasks/{id}`, `GET /tasks/{id}/result`.
- **Tests:** `assistant/tests/test_daily_schedule.py` plus a new integration test for
  end-to-end task execution.

## Background Conversation Summarization

The agent runs a daily background summarization job that compresses conversation episodes into structured frame summaries. This enables "learning slowly but effectively" — compressing raw episodic memory into structured semantic frames, reducing prompt pressure while preserving long-term knowledge.

### Architecture

**New Files:**
```
assistant/backend/scheduler/summarizer.py      # Core summarization logic
assistant/backend/scheduler/__init__.py        # Export public API
```

**Modified Files:**
```
assistant/backend/config.py                    # New Settings fields
assistant/backend/scheduler/runner.py          # Register summarization task
assistant/backend/memory/store.py              # Helper: upsert_summary_frame
assistant/backend/memory/retrieval.py          # Include summary frames in retrieval
```

### Database Schema (no migration needed)
- Uses existing `frames` table: `name="conversation_summary_{session_id}"`
- Slots: `summary`, `key_entities`, `open_questions`, `session_id`, `turn_count`, `date_range`, `created_at`, `updated_at`
- Associations: `summarizes` relation from summary frame → episode frames

### Functional Requirements
1. **Periodic summarization**: Runs as a scheduled task (configurable interval, default daily via `DAILY_TASKS_TIME`)
2. **Session-scoped**: Summarizes episodes per session; produces one summary frame per session
3. **Model-driven**: Uses utility model (qwen2.5:3b) — cheap, fast, already loaded
4. **Structured output**: Generates frames with slots: `summary`, `key_entities`, `open_questions`, `session_id`, `turn_count`, `date_range`
5. **Frame integration**: Summaries stored as frames (`conversation_summary_{session_id}`) with embeddings for retrieval
6. **Episodic linkage**: Link summary frame to source episodes via associations (`summarizes` relation)
7. **Idempotent**: Re-running on same session updates existing summary frame (upsert)

### Non-Functional Requirements
1. **Off-hot-path**: Runs in scheduler background; never blocks chat response
2. **Timeout-safe**: Utility model call has 60s timeout; max 3 retries
3. **Configurable**: Interval, max sessions per run, min turns to trigger via `.env`
4. **Observable**: Logs `summary_created` / `summary_updated` with turn count, char count
5. **Testable**: Unit + integration tests for summarization pipeline

### Configuration (`.env`)
```bash
SUMMARIZATION_ENABLED=true
SUMMARIZATION_INTERVAL_HOURS=24          # daily
SUMMARIZATION_MIN_TURNS=10               # minimum turns before summarizing
SUMMARIZATION_MAX_SESSIONS_PER_RUN=5     # limit per scheduler tick
SUMMARIZATION_MAX_CHARS=4000             # max input chars to utility model
SUMMARIZATION_TIMEOUT_SECONDS=60
```

### Design Principle Alignment
| Principle | Application |
|-----------|-------------|
| **Model-first correction** | Utility model for summarization (not scripted rules) |
| **No templated responses** | Summaries are model-generated narratives, not templates |
| **Lean on model flexibility** | Utility model decides what's salient, not hardcoded rules |
| **Scheduled tasks are memory** | Summarization runs as a scheduled task; output is frames/slots |
| **Clean ship** | New module with tests; no dead code |
| **Stability: no regressions** | Unit tests for summarizer; integration tests for pipeline |

### Retrieval Integration
Summary frames are included in memory context retrieval with a dedicated "## Conversation summaries" section, separate from regular frames. This keeps the prompt organized and lets the model reference past conversation summaries when relevant.

### User-Initiated Summarization
Endpoint: `POST /summarize` with body `{"session_id": "..."}`
- Triggers immediate summarization of a session
- Returns summary, key_entities, open_questions, turn_count, created status
- Useful for manually triggering summarization after significant conversation changes

### Tests
- `assistant/tests/test_summarizer.py`: unit tests (create, update, skip)
- `assistant/tests/test_api.py`: `test_correction_endpoint_basic` regression test for correction pipeline

### Risk Mitigation
| Risk | Mitigation |
|------|------------|
| Utility model timeout | 60s timeout + 3 retries; skip session on failure |
| Prompt overflow | Truncate input to `SUMMARIZATION_MAX_CHARS` (4000) |
| Duplicate summaries | Upsert by session_id; idempotent |
| Prompt cache pollution | Summaries retrieved separately, not in hot prompt path |

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
- `backend/pipeline/search.py` — `SearchBackend` ABC + `SearXNGBackend` + optional
  `BraveBackend`; `WebSearchTool` wraps backend selection and exposes `SearchInfo`.
- `backend/pipeline/tools.py` — `builtin_tools()` registry; `_make_fetch_url_handler()` for `fetch_url`.
- `backend/pipeline/extractor.py` — fact extraction + correction pipeline + scheduled task extraction.
- `backend/pipeline/reasoner.py` — planning + self-correction (`Action.CORRECT`).
- `backend/pipeline/orchestrator.py` — coordinates full cognitive loop; scheduled tasks
  execute via `chat()`.
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

## CSS Design Principles
- **No inline styles.** Inline styles (`style={{...}}`) are prohibited. They break cohesion, are hard to maintain, bypass the design system (variables), and prevent reuse.
- **CSS Modules for component-scoped styles.** Each component gets its own `.module.css` file co-located with the component.
- **Use design tokens from `variables.css`.** Colors, spacing, typography, radii, shadows, transitions — all defined once in `src/styles/variables.css` and referenced via `var(--token-name)`.
- **Shared base classes in `components.css`.** Reusable patterns (buttons, inputs, modals, etc.) live in `src/styles/components.css`. Extend or compose these rather than redefining.
- **Layout in `layout.css`, not components.** Structural positioning (flex/grid containers, header/sidebar/main) belongs in layout, not component files.
- **States via classes, not inline.** Hover, active, disabled, open/closed, loading — use pseudo-classes (`:hover`, `:focus`) or toggle class names (`.is-active`, `.is-open`) driven by signals.
- **Animations in CSS.** Transitions and keyframes live in CSS. JS only toggles classes.

## Clean Code & Modularity
Dead code is a liability. Unused functions, duplicate logic, and monolithic files make the codebase harder to reason about and increase the risk of breaking something that still matters. Before shipping a change:

- **Extract shared utilities** before duplicating logic across files (see `static/shared/utils.js`, `static/shared/components.css`).
- **Delete unused code** — run `grep -r "functionName" .` to verify zero callers across the entire codebase before removing.
- **Prefer composition over monoliths** — split files by responsibility (see `static/shared/`, `static/chat/`, `static/brain/`).
- **Shared utilities first** — before writing similar logic twice, check `shared/` first.
- **Modular CSS** — component-scoped styles with shared base classes (see `static/shared/components.css`).
- **Testable modules** — pure functions over side-effect-heavy IIFEs.
- **ES Modules** — use `<script type="module">` for native browser modules (no build step required).

## Pre-Commit Flow (Required)
**Before every commit, run both lint and tests:**

```bash
# Backend (from repo root)
docker run -it --rm -v $(pwd):/app -w /app assistant ruff check .
docker run -it --rm -v $(pwd):/app -w /app assistant pytest assistant/tests/

# Frontend
cd frontend && npm run lint && npm run test
```

## SolidJS Frontend Development Notes

### 1. Refs (`ref` attribute)

**Official docs:** https://docs.solidjs.com/reference/jsx-attributes/ref

**Key behaviors:**
- **Variable ref**: `let myEl !: HTMLDivElement; <div ref={myEl} />` — assigned during render, before DOM connection
- **Callback ref**: `<div ref={(el) => { myEl = el }} />` — called with element; use when you need the element before it's added to DOM
- **TypeScript**: Must use definite assignment assertion (`!`) since Solid assigns at render time
- **Signals as refs**: `const [el, setEl] = createSignal<HTMLDivElement>(); <div ref={setEl} />` — useful when element may not exist initially or gets removed
- **Forwarding refs**: Pass `props.ref` to child element: `<Child ref={props.ref} />` — child must accept `ref` prop and apply it to its element
- **Ref arrays**: `<input ref={[(el) => input = el, autofocus, listen("input", onInput)]} />` — compose element access + directives

**Common pitfalls:**
- ❌ Don't use `useRef` (React pattern) — Solid uses plain variables + callback refs
- ❌ Don't access ref in render body — it's assigned *during* render, not before
- ✅ Use `onMount` or `onSettled` (or `setTimeout` in callback) for DOM measurements after mount
- ⚠️ Ref callbacks run untracked (no reactive context) — wrap in `createEffect` if needed

### 2. `innerHTML`

**Official docs:** https://docs.solidjs.com/reference/jsx-attributes/innerhtml

**Syntax:** `<div innerHTML={htmlString} />`

**Critical security warning:**
- **XSS risk**: Unlike JSX expressions (which auto-escape), `innerHTML` inserts raw markup — **never use with unsanitized user input**
- **Sanitization required**: Use DOMPurify or similar if content comes from untrusted sources
- **SSR**: HTML string emitted as child content without escaping

**Alternatives (prefer these):**
- **`textContent`**: `<div textContent={plainText} />` — inserts as plain text, safe from XSS
- **Solid's built-in escaping**: `{userContent}` in JSX — auto-escapes by default
- **`@solidjs/html`**: Tagged template literal for trusted HTML: `html`<div>${trusted}</div>` (compile-time trusted only)

**Common pitfalls:**
- ❌ `innerHTML={userInput}` — XSS vulnerability
- ❌ `innerHTML={apiResponse.html}` without sanitization
- ✅ Sanitize first: `innerHTML={DOMPurify.sanitize(userHtml)}`
- ✅ Prefer `textContent` or JSX interpolation for user content
