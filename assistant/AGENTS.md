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

### Latency: what to measure, and what is already measured

**Measure time-to-first-token, not total turn time.** Streaming already exists, so
the user sees a progress event and then text word by word. Total turn time is
close to irrelevant to perceived responsiveness; the wait before the first word
is the whole of it.

**The numbers are already logged, at INFO, one greppable line per turn:**

```
turn_pregen: pregen_ms=... routing_ms=... recall_ms=... plan_ms=... extraction_ms=... search_ms=...
turn_timings: turn_total_ms=... episode_ms=... ... ttft_ms=...
```

`turn_pregen` fires just before the generation call and is the total of the dead
time in front of the first token. On the streaming path only, `ttft_ms` is the
measured wait until the first delta. Both lines come from
`Orchestrator._log_turn_timings`; `assistant/tests/test_turn_timings.py` pins them
so the logging cannot be quietly demoted back to DEBUG.

Read these before changing the hot path. The phases are LLM calls that already run
concurrently in two groups — routing‖recall, then plan‖extraction — so each group
costs roughly its slowest member, and the phases are sequential with respect to
each other. Adding a new sequential LLM call to either group adds its full cost to
every turn.

**Reverse proxy timeouts are not a constraint.** `Caddyfile` sets
`response_header_timeout 300s`, `flush_interval -1` and `stream_timeout 0` on the
API route. An earlier version of this file described a "Caddy default 30s" tool
calling timeout; that was wrong on two counts, and the phantom was designed around
before it was measured. `response_header_timeout` bounds the wait for *response
headers*, and the SSE stream emits a progress event before any LLM work begins, so
headers arrive in tens of milliseconds regardless of how long generation takes.
`stream_timeout 0` means no ceiling on the stream at all. Do not treat proxy
timeouts as a cause of user-visible latency without measuring headers-vs-body
first.

### The tool loop's context window is the whole turn's budget

The tool loop sends the system prompt **plus every tool schema** (~11.5k chars /
~3k tokens for the builtin set) **plus history** in one prompt, so it is the
largest prompt in the system and the one that overflows first. At
`CHAT_NUM_CTX=8192` a live turn reached 8169 prompt tokens, generated 23, and was
cut off — `llama-server` logged `n_tokens = 8191, truncated = 1`, Ollama returned
`done_reason="length"`, and the loop finalized empty (the old
`"I'm not sure how to respond."`). `CHAT_NUM_CTX` is now 16384.

Because `tools_model` defaults to `chat_model`, **one loaded runner serves both
roles, so they share one context window** — the tool loop gets the chat window,
not the smaller `tools_num_ctx`. `tools_num_ctx` applies only to a *distinct*
tools model. `_num_ctx_for()` in `llm_client.py` is the single place this is
decided; do not reintroduce per-call-site `num_ctx` branches.

### Known characteristic: the answer arrives whole, not token by token

`/chat/stream` streams *events* — `stage` progress, then `finalize` with the
complete answer, then `meta` — but with `TOOLS_ENABLED=true` (the default) it does
not stream the answer *text*. Every turn goes through `stream_tool_loop`, which
uses blocking `chat()` calls internally and yields a single whole-answer
`FinalizeEvent`. `TextDeltaEvent` exists in `backend/pipeline/streaming.py` and is
never constructed; the no-tools branch does emit deltas, but it is unreachable in
the default configuration.

Measured on five live turns: `ttft_ms` tracked `turn_total_ms` to within tens of
milliseconds (13.0s/13.1s, 13.7s/13.8s, 16.0s/16.1s, 17.8s/17.9s, 39.8s/39.9s).
So today **total turn time is the user-visible latency** — there is no streaming to
hide behind, and the `stage` events are what make the wait tolerable.

The frontend already renders `text_delta` incrementally (`frontend/src/state/chat.ts`)
and has tests for it, so this is a backend gap, not a UI one. Closing it would cut
perceived wait from ~13-18s to roughly the ~2-3.6s pre-generation span. The open
design question is what happens when a round turns out to be a tool call after its
prose has already been shown: either flash chatter that `finalize` then replaces,
or buffer the round (which keeps most of the wait, because the slow round is the
first one). Deliberately not done yet.

Note that the speed that is *already* in the answer is the prompt cache: sections
are ordered stable-first so the ~11k-char stable prefix reuses its KV across
consecutive turns, and the same model (not a different size) serves tool calls and
the final answer so both share the hot cache.

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
   instructions → **current date/time** → memory) so Ollama's prompt cache reuses the
   stable prefix across consecutive turns. The date/time line is in the user's zone
   (`timeutil`), injected rather than exposed as a tool, so "what's today's date?"
   costs no round-trip. Recent episodes appear in memory context as 240-char digests;
   the last 6 turns still arrive verbatim as message history.
6. Response to user (`ChatResponse` with `extraction_summary` + `search_extraction_summary`).
7. Search extraction (utility model): runs only when a search actually happened; deduped
   against conversational slots by (frame_name, value).

Actual ordering inside `Orchestrator._run_turn()` — **the single cognitive loop**:
- Router (with wants_search) → retrieval → reasoner plan (search vetoed if
  `wants_search is False` and task_type != search) → correction branch if needed.
- Conversational extraction runs BEFORE generation; just-stored facts are injected
  into the system prompt ("Facts you just stored this turn") so the model can
  acknowledge them truthfully in its own words. No templated acknowledgments.
- Search extraction runs only when a search actually happened, and is deduped
  against conversational slots by (frame_name, value) — cross-key duplicates like
  "strings"/"number_of_strings" are dropped in favor of the earlier channel's key.

### One loop, three consumers (do not re-duplicate)

`Orchestrator._run_turn()` is the only implementation of the loop. It emits the
SSE event stream. `chat_stream()` is a one-line pass-through to the UI, `chat()`
is an adapter that drains the same stream and rebuilds a `ChatResponse`, and
`run_scheduled_task()` is a thin wrapper that runs a task's script through the
same loop (see below). There is no second copy.

This replaced three ~600-line copies that drifted repeatedly (see
`test_stream_parity.py` and the scheduled-task history), each a user-visible bug:
a stalled relevance gate that hung the stream, a swallowed generation failure, a
dropped `compute` result, a missing sources footer, a missing learning summary, a
double-logged correction turn, and — in the scheduled copy — a task that searched
with its own instruction as the query and returned tool documentation instead of
results. A new feature added to the loop reaches every path by construction; do
not add a second loop.

`MetaEvent` carries the full `ChatResponse` shape — including `citations` and
`memory_context` — so the adapter loses no field. Every terminal branch (normal,
scheduled, correction, consent, generation-failure) emits exactly one `meta`.

**Scheduled tasks run the loop too.** `run_scheduled_task(prompt, …)` builds a
`ChatRequest` with `force_search=True` (a standing task always checks for new
information; the router's storage-style veto must not suppress it) and
`user_turn_override=build_scheduled_task_directive(prompt)` (the script plus the
`ALERT:` contract, delivered as the user turn while `message` drives routing and
retrieval) and `search_consent=True` — **scheduling the task is the user's
consent to search for it**: nobody is present at the daily tick to answer a
Brave consent prompt, and the user asked for the search when they created the
task. The loop must **not** re-enter the scheduled-task management branch
while executing a task's script — guard it with `not request.force_search`, or
`run_scheduled_task → chat → _handle_scheduled_task → run_scheduled_task`
recurses forever.

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

### Resilience: retry idempotent hops, not side effects

The assistant is multi-model and tool-heavy, so a blip on any hop is normal, not
exceptional. `assistant/backend/retry.py` is the one place that decides what gets
retried:

- **Retry only idempotent work** — Ollama `chat`/`embeddings`, web search,
  `fetch_url`, the DB connection open, and the read-only tools (`list_files`,
  `read_file`, `glob`, `recall`, `search_episodes`).
- **Never retry a side effect.** `write_file`, `edit_file`, `delete_file`,
  `upsert_slot`, `upsert_association`, `mark_essential`, and `compute` get a
  single attempt. Repeating a write/delete can double-apply it; `compute`
  executes arbitrary Python. The tool loop already hands a failure to the model,
  which can see the result and decide whether to repeat — that judgment is better
  than a blind retry.
- **Retry the transient class only**: transport errors and `429/502/503/504`,
  never `4xx`. Per-retry logging is at WARNING so a flapping dependency stays
  visible instead of being silently absorbed.
- **Do not nest retries.** `web_search`/`fetch_url` retry inside their HTTP layer
  and are excluded from the tool-level retry.

The same argument applies to the encrypted DB. A fresh SQLCipher connection
derives its key lazily from page 1; under I/O contention that read can
transiently fail (`hmac check failed for pgno=1` → `disk I/O error`) and succeed
on the next connection. `aiosqlite_connect_checked` forces the derivation at open
and retries on a *fresh* connection. This is a retry, not corruption handling —
`PRAGMA cipher_integrity_check` was clean throughout, and a persistent failure
still raises.

### Stability: no regressions while adding features
Every non-trivial change should be accompanied by a regression test — a test that would
have caught the bug before the fix ships. This builds the suite in the direction of
real bugs rather than abstract coverage.
Critical paths that need regression tests (in priority order):
1. **Voice recording flow**: silence detection → stopRecording → transcription → sendMessage.
   Covered by `frontend/src/hooks/useConversationVoiceRecording.test.tsx`,
   `useVoiceRecording.test.tsx`, and `state/voiceOutputGate.test.ts`. The mic stays
   open through a `/transcribe` round trip — captures are queued and sent serially so
   speech between utterances is not lost — and shuts only while the agent is speaking.
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
- Embeddings: frame type + name + slot key=value lines, via qwen3-embedding:0.6b, stored
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
- **Portable brains — implemented, not wired.** `export_portable_brain` /
  `import_portable_brain` exist in `backend/memory/backup.py` and are covered by
  `tests/test_brain_portable.py` (12 tests). They are **not reachable by a user**: there
  is no `/brain/export-portable` or `/brain/import-portable` API endpoint in `main.py`,
  and no `assistant db export-portable` CLI command in `cli/app.py`. Earlier revisions
  of this file described those endpoints and commands as if they existed; they do not.
  Wiring them is small, and until then the working paths are the full-DB ones below.
- **Full-DB backup/restore**: `assistant db backup` and `assistant db restore` CLI commands
  create and restore encrypted JSON bundles. Still useful for point-in-time snapshots.
  `POST /db/backup` also exists and is what the scheduler calls on its 12-hour clock.

## Confidence rules (assistant/backend/memory/confidence.py)
- New slot value: confidence 0.5.
- Repeated same value: confidence increases: `conf = 1 - (1-conf)*0.7` (bounded).
- Conflicting value: auto-resolve by a fixed ladder — source_reliability →
  confidence → priority → recency tiebreak; loser value → slot_history.
  Both logged in conflicts table.
- **The assistant's own name is the one deliberate exception.** A user-stated
  `identity_name.full_name` is written at `source_reliability` 1.0 (the
  `manual_override` ceiling), so a rename supersedes the stored name instead of
  losing to an earlier correction that left it at 0.99. This is a reliability
  floor on that single slot, not a bypass: the value must still trace to the
  user's message (the self-description guard runs first), and the write still
  goes through `revise` / `slot_history` / `conflicts`. Only the conversational
  path sets it; other identity slots keep the normal ladder.
- **The user's own name has its own frame.** First-person self-identification
  ("my name is X", "I go by X", "call me X") extracts to
  `user_identity.full_name`, never `identity_name`; the two frames are reserved
  against fuzzy canonicalization so they cannot absorb each other, and a
  same-turn cross-check drops an `identity_name.full_name` the user also claimed
  as their own. The `user_identity` frame is what a "what's my name?" query
  retrieves.
- **Corrections route by subject, not by the model's frame/key.** The correction
  schema carries a `subject` (`user` | `assistant` | `topic`) that the model
  fills; the code maps it to the reserved frame and normalizes any name key to
  `full_name`. The correction path was the one identity writer with no
  extraction-side guard, and it used the model's frame/key verbatim — which is
  how the assistant's name reached `user_identity.name` while the user's name sat
  on `identity_name`. There is no list of model-invented frame names to maintain;
  the model decides *who*, the code decides *where*.
- Positive feedback: `bump_confidence(current)` = `min(1 - (1-current)*0.7, 0.99)`
  (same repeat-discount curve as reinforcement).
- Negative feedback: `lower_confidence(current)` = `max(current - 0.15, INITIAL_CONFIDENCE)`.
- Association confidence: increases with co-occurrence in episodes (batch process
  during consolidation, not real-time).

### The ladder is measured, not assumed

Instrumentation and audit confirmed the ladder works, and the measurement is worth
recording because the opposite was long believed:

- **It discriminates.** A user-stated fact (`source_reliability` 0.99) against a search
  attempt (0.5) resolves to the user's value. The first rung fires.
- **A 100% "new wins" figure is ties, not a bypass.** `revise()` passes
  `new_source_reliability=None` → 0.5; when the existing side is also 0.5, rungs 1–3
  tie and recency decides, which is what a tiebreak is for.
- **`EXISTING_WINS` is recorded as decided.** It sets `status='auto_resolved'` with
  `resolved_value` = the existing value. It previously wrote `status='pending'`, which
  made a decision indistinguishable from a deferral and inflated the apparent review
  queue roughly tenfold. The slot is deliberately **not** updated: existing standing is
  the outcome, not an omission.
- **Every conflict records its decision inputs** — `existing_/new_source_reliability`,
  `_confidence`, `_priority` on the `conflicts` row. `slot_history` keeps old and new
  *values* but no provenance, so before these columns a past decision could not be
  audited at all.

See `assistant/experiments/conflict_ladder_value/`. Plan D (a model that reasons over
conflicts) was **closed** on this evidence: it would duplicate a working comparator.

## Model fleet config
Role-based model selection. Configurable in `.env`: `CHAT_MODEL`, `UTILITY_MODEL`,
`EMBEDDING_MODEL`, `TOOLS_MODEL`, `MAX_MODEL`, `MATH_MODEL`, `CODER_MODEL`
(reserved, empty = chat model), `OLLAMA_URL`. See `docs/MODEL_SELECTION.md` for
the assessment criteria, memory budget, and re-evaluation process.
- Chat model (default `qwen3.5:9b`): user-facing responses. Thinking-capable models accept
  per-request `think=True/False` (`OllamaClient.chat`); inline `<think>` tags are parsed
  out by the LLM client into a separate `thinking` field on the internal response.
- Utility model (default `qwen3.5:4b`): extraction, task-routing fallback.
- Tools model (default `qwen3.5:9b`): drives the tool loop; same brain as chat so
  tool calls + final answer share the hot KV cache.
- Embedding model (default `qwen3-embedding:0.6b`, 1024-dim): frame/query embeddings.
- Max model (opt-in; empty by default — set `MAX_MODEL`, e.g. `qwen3.8:27b`):
  on-demand escalation tier (auto reasoner or "Max" UI toggle); think on,
  `keep_alive=10m`, never resident next to the warm set. Empty means escalation
  falls back to thinking-mode on the chat model.
- Math model (opt-in; empty by default — set `MATH_MODEL`, e.g. `qwen3.8:27b`,
  which shares weights with the max model): on-demand `compute` tool — writes
  Python for sandboxed execution. Empty disables the `compute` tool entirely.
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
- **Execution uses the full cognitive loop — the same `_run_turn` as chat.**
  When a task fires, the scheduler calls `orchestrator.run_scheduled_task(prompt, …)`,
  which runs the task's script through the one loop: the script drives routing and
  retrieval, is delivered as the explicit user turn (the directive, carrying the
  `ALERT:` contract), and search is **forced** (a monitor task must not answer
  purely from yesterday's frames). Because it is the full loop, the model has its
  **tools** during a task — it can `recall` the user's criteria, `web_search` a
  targeted query, and read/write files. It previously ran a partial copy with no
  router and no tools, and searched with the instruction itself as the query,
  which returned tool documentation instead of results.
- **Agent-raised alerts.** While executing a task the model may flag something
  genuinely important by ending its report with `ALERT: <short title>` plus a
  one-sentence reason. The runner parses that footer into a high-visibility
  `task_alert` (severity `important`) in the user's alert bell, and strips the
  footer from the stored task summary so it does not pollute the output memory.
  Because a task now runs the loop, its report can also carry the loop's own
  footers (a `**Sources:**` list on a search-classified task, or the "answered
  from memory" marker). Those land *after* the `ALERT:` line, so the runner
  strips them before parsing — otherwise they become the alert body and are lost
  from the summary.
- **Run-now has a generous ceiling.** The `run_scheduled_task` tool wraps a full
  loop, so its timeout is `SCHEDULED_TASK_TIMEOUT_SECONDS` (default 900s), not
  the 60s default that cut big tasks off mid-run. The scheduler's own firing path
  has no cap.

## Alerts — the agent's channel to the user

**In the UI the bell is labelled "For You"** (mint, not red). Under the hood these
are `alert` frames — the label is the user-facing name, chosen because these are
things to talk about, not errors, and a red badge read as alarm.

**An alert is memory of a type**, not a row in a notifications table: a frame of type
`alert` with slots `title`, `message`, `status` (`new` | `resolved`), `severity`,
`kind`, `about`, and `session_id`. The bell is a view over it. Because an alert is
memory it participates in retrieval, so the agent can raise one *when it is
contextually relevant* rather than only when the user opens a bell.

**The presence rule decides when an alert is warranted:**

> An alert is warranted when the agent learned something and the user was **not there
> to hear it**.

| Situation | Behaviour |
|---|---|
| The agent learns during a live conversation | Say it in the conversation. **No alert.** |
| The agent learns during scheduled work, user absent | **Alert.** |
| The agent finds something it cannot settle | **Alert** — a request, not a report. |
| A task completes | **Not an alert.** The output is already an episode. |
| A task **fails** | **Alert** (`task_failure`). The user asked for a recurring task and it is silently broken. |

Measured before this was enforced, the bell was dominated by mechanism: task-completion
notices, search notices fired during the conversation, and auto-resolved conflicts
announced to the user watching them resolve. Removing those writers from both
orchestrator paths left only the alerts the presence rule allows.

**An alert closes by being answered, not by being read.** There is no read flag — a
status that changes nothing is why notification rows accumulate unread.
`mark_alert_read` performs the real transition (`status = resolved`) and is named for
the API route that calls it.

**There is deliberately no dismiss action** ("ignore", "I know", "Not important").
Two lighter actions were designed and dropped, because of what each path *stores*:
resolving in a conversation writes an **episode** — the alert's question, the user's
reply — which is embedded and retrievable by meaning, while a dismiss button would
write only a `status` value via `set_derived_slot` (no `slot_history`, no episode, no
embedding). The conversation path is not merely simpler; it produces the richer
memory, and more communication improves the knowledge model over time. Do not
re-propose a dismiss without that context.

**Resolution happens in a conversation.** `POST /alerts/{id}/open` attaches an alert to
a session (or seeds one, if empty), and the alert closes when the user replies there —
by the agent's instruction in the alert's own opening message, *and* by a
deterministic backstop (`resolve_alerts_for_session`), because relying on a small model
to remember a housekeeping step is a failure this project has already been bitten by.
The default is an existing conversation, not a new thread: a thread per alert fills the
conversation list with one-off threads.

- **Daily-run event frames.** Each morning the scheduler creates/updates an `event`
  frame named `daily_run_YYYY_MM_DD`. It records `date`, `tasks_run`, and `status`,
  and associations link each task frame to the run and to its output episode.
  `tasks_run` ACCUMULATES as tasks fire (never overwritten with the last task's
  name), so the frame answers "what did my morning run cover?".
- **Outputs are queryable.** The assistant's response from a task run is a normal
  assistant episode. The user can later ask "What did my morning briefing find?"
  and retrieval will surface it. Because the run is the full loop, the script is
  also logged as the **user** turn — the user authored it, so "user" is honest,
  and the run is still distinguishable in memory by its session id
  (`scheduled-<task>-<date>`), so a reader can tell a scheduled run from a live
  conversation.
- **Task kinds:** `daily` (runs every tick until stopped) and `once` (next tick, then
  disabled). No cron expressions — one shared daily tick.
- **Extraction:** utility model returns `{intent, name, description, prompt, repeat}`
  (`repeat: false` for one-shots). The old NL→cron parser and `croniter` dependency
  were removed.
- **Runner (`scheduler/runner.py`):** 20s poll loop; fires due tasks, creates the daily-run
  event frame, links associations, and reschedules.

### Housekeeping timers (and the difference between "checked" and "applied")

| Timer | Interval | What it does |
|---|---|---|
| Heartbeat | 30 min | sets the `scheduler_heartbeat` slot |
| Embedding top-up | 6h | re-indexes turns/frames with missing or stale embeddings. Non-destructive, no backup. |
| Consolidation | 6h | **checks** for near-duplicate frames and, if any are found, merges them |
| Brain snapshot | 12h | a plain DB copy for point-in-time recovery |
| Summarization | 6h | compresses eligible sessions (≥10 turns) into summary frames |

**Merge is ad hoc; only the *check* is periodic.** Nothing emits an event when a
duplicate frame appears, so the scheduler has to *look*. Every `CONSOLIDATION_INTERVAL_HOURS`
it computes a read-only plan (`dry_run=True`) and, only if the plan found merges,
applies them (capped at `CONSOLIDATION_MAX_MERGES_PER_RUN`). So the interval is a
**look cadence**, not a merge cadence — a household with no duplicates merges nothing,
forever, no matter how often it checks. Truly event-driven merge would need the
duplicate check on the write path, which costs an embedding + clustering pass per
write on the chat hot path; the periodic look keeps that work batched and off the
hot path.

**Backups are on their own clock, deliberately.** `BACKUP_INTERVAL_HOURS=12` drives
`_run_backup_snapshot`, independent of merges. The two meet in exactly one place: a
merge pass that runs **without a fresh snapshot** takes one first, so a merge is never
applied unprotected; a merge that coincides with the 12h snapshot takes no extra copy.
This is why snapshot count does not scale with merge frequency — before, every merge
cycle snapshotted unconditionally, which wrote ~12 snapshots/day for work that often
did nothing.

- **Nothing is forgotten on a timer.** There is no decay or age-based garbage collection:
  memory only leaves through an explicit `forget` or a deliberate frame/file deletion.
  (Merges tombstone duplicate losers, but their content is unioned onto the survivor first.)
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
3. **Model-driven**: Uses the utility model (`UTILITY_MODEL`) — cheap, fast, already loaded
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
- `backend/memory/consolidate.py` — merges near-duplicate frames onto a survivor (unions
  slots, redirects associations, tombstones losers); runs via the scheduler and
  `assistant db consolidate`. This is the only maintenance that changes frame identity.
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
- `backend/pipeline/transcript.py` — plain-text transcript of a conversation (episodes → downloadable `.txt`); also owns the loop's response footers (`strip_response_footers`), shared with the scheduler.
- `backend/pipeline/extractor.py` — fact extraction + correction pipeline + scheduled task extraction.
- `backend/pipeline/reasoner.py` — planning + self-correction (`Action.CORRECT`).
- `backend/pipeline/orchestrator.py` — coordinates full cognitive loop; scheduled tasks
  execute via `chat()`.
- `backend/pipeline/llm_client.py` — Ollama client (chat + embeddings); `build_system_prompt` injects the current date/time (see `timeutil`).
- `backend/timeutil.py` — the one clock. `local_tz()` resolves `DAILY_TASKS_TZ` > `TZ` env > host local; `current_datetime_str()` formats the line the system prompt carries. The scheduler, the prompt, and the `.ics` writer all use it so "today" means the same thing everywhere. The container runs UTC; the user does not.
- `backend/scheduler/schedule.py` — the daily clock (tick computation; delegates its timezone to `timeutil`).
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

While iterating on a change, run only the test file(s) that exercise the feature
you touched (plus the regression test you added) — that keeps the loop fast. The
full suite below is the pre-commit gate, not the inner loop.

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
