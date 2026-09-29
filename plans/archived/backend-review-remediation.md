---
date: 2026-09-28
status: done
archived: 2026-09-29
---

# Backend review remediation plan

Shipped in `v0.1.0-alpha`. Every P0–P3 item was resolved except the three
deliberately-tracked items called out inline (P2 `_embed_batch` round trips, P2
N+1 batching, and the P3 `(frame_name, value)` dedup key, which is a documented
product decision). Retained for reference; the code is the source of truth.

Written 2026-09-28 after a principal-level review of the Python backend
(`orchestrator`, `store`, `main`, `extractor`, `tool_executor`, `llm_client`,
`search`, `retrieval`). Supersedes `streaming-tools-remaining.md`, which is
deleted: it referenced a model fleet (`nomic-embed-text`, `qwen2.5:7b`,
`qwen3-coder:30b`) that no longer exists and its one open bug is long fixed.

Baseline: `ruff check .` clean, `pytest assistant/tests/` **752 passed, 22
skipped**. Every item below was verified by reading the code and, where noted,
by executing it. Nothing here is a style preference.

## How to read this

- **Verified** = reproduced by running it, or read at the exact cited line with
  the failure mode confirmed by inspection of the schema/config that makes it real.
- **Needs verification** = the code pattern is confirmed but the exploit path or
  trigger has not been executed end to end.
- Severity reflects user impact, not code aesthetics.

Two constraints shape every fix: all inference is local Ollama (no cloud APIs,
no new LLM calls on the hot path), and the LLM's output is untrusted input.

---

## P0 — Correctness and security

### 1. `skip_route` turns 500 on the search path
**`orchestrator.py:923`** and **`orchestrator.py:1946`** (`chat_stream`)

`chat(skip_route=True)` sets `classification = None` (`:615`/`:1803`), but the
search branch dereferences `classification.search_query` unconditionally. The
router's `wants_search` veto (`:679-685`) explicitly requires
`classification is not None`, so it cannot protect this line.

**Verified.** All four `_handle_scheduled_task` fallbacks
(`:1400, :1433, :1436, :1448`) rewrite `request.message` to an agent-authored
string and re-enter `chat(skip_route=True)`. Running the real `classify_intent`
against empty memory returns `search_needed=True` for all four, so the branch is
always entered. Reachable from "delete my briefing", a typo'd `run_now`, and any
unparseable task phrasing — AGENTS.md critical path #2. There was **zero** test
coverage (`grep skip_route assistant/tests/` was empty).

Fix: `query = (classification.search_query if classification else None) or sanitize_query(request.message)`
at both sites. Regression test already written and **verified failing** against
the current code: `assistant/tests/test_skip_route_crash.py`.

### 2. `_execute_python_sandboxed` sandboxes nothing
**`llm_client.py:248-295`**

The docstring claims "no network, limited imports". The implementation is a plain
`subprocess.run` with `env={**os.environ, "PYTHONPATH": ""}`. `docker-compose.yml`
puts `DB_KEY` and `BRAVE_API_KEY` in that environment. `allowed_imports` is a
*string prepended* to the file — it adds conveniences and removes nothing. There is
no netns, no filesystem restriction, no memory cap, no uid drop. It also blocks
the event loop for up to 30s, and `subprocess.run`'s timeout kills only the direct
child, not grandchildren.

Reachable with the raw user message at `orchestrator.py:863`/`:1920`, and with
model-chosen code via the `compute` tool.

Dormant only because `MATH_MODEL` defaults to empty. **Verified** that
`DB_KEY`/`BRAVE_API_KEY` are in the composed environment.

Requirements:
- Pass a minimal environment (`PATH` only) so the brain key is not readable.
- Run off the event loop (`asyncio.create_subprocess_exec`).
- Kill the process group on timeout (`start_new_session=True` + `os.killpg`).
- Rename to `_execute_python_subprocess` and **make the docstring state what is
  actually enforced**. A function whose name asserts a security property it does
  not have is the real defect.
- Add a test asserting the env is empty and that a grandchild does not survive.

### 3. Model-writable `file_safe_name` → arbitrary file read/delete
**`tool_executor.py:232`** → **`main.py:2333, 2386, 2432`**

`execute_upsert_slot` writes `args["slot_key"]` with no reserved-key guard, and
`apply_extraction` does the same. `main.py` then builds
`Path("/app/data") / file_safe_name` with no containment check, and
`/files/{frame_id}` performs **no owner check**.

**Verified** by execution: `Path("/app/data") / "/app/.env"` resolves outside the
data dir (absolute right operand discards the left), as does `"../../etc/passwd"`.

The contrast proves this is an oversight, not a design choice: `main.py:1196-1201`
does the `relative_to` containment check for restores, and `tool_executor.py:695`
reads the same slot back through `resolve_sandbox_path`.

Requirements:
- Reject `file_*` slot keys arriving from **model output** in both
  `execute_upsert_slot` and `apply_extraction`/`apply_search_extraction`.
- Contain all three `/files` paths via `resolve_sandbox_path` (or an equivalent
  `resolve()` + `relative_to`), returning 400 on escape.
- Add the owner check that `execute_read_file` already has.

### 4. `merge_frames` raises `IntegrityError` on the most common merge shape
**`store.py:570`**

The two `UPDATE`s repoint the loser's edges *before* the duplicate/self-referential
cleanup at `:581`, so two frames sharing a neighbour **with the same relation
type** violates `UNIQUE(from_frame_id, to_frame_id, relation_type)`.

**Verified by execution** — reproduced the exact `IntegrityError` and rollback.
`test_memory_store.py:410` dodges this by using different relation types.

Fix: delete colliding rows before repointing, inside the same transaction.
Regression test using the *same* relation type.

### 5. Frame resurrection discards every argument but `name`
**`store.py:312`**

The tombstone path sets only `deleted_at = NULL`; the insert path at `:320-358`
writes all nine columns.

**Verified by execution**: a tombstoned `mystery`/`person`/0.2 re-created as
`book`/0.95 returns `person`/0.2 with stale slots attached. `type` is the field
retrieval keys off. (Note: `forget_frame` only zeroes `priority` and never
tombstones — the bug needs a real `deleted_at`, which is what GC and
consolidation produce.)

Fix: bind the same tuple the INSERT uses.

### 6. Three registered tools can never succeed
**`tool_executor.py:502, 467, 1138`**

`assistant.backend.pipeline.fetch` does not exist; `consolidate.search_episodes`
is undefined; `scheduler.run_now` is not exported. All three are advertised to the
model in `builtin_tools()` and all three are swallowed by `except Exception` into
a success-shaped `ToolResult`.

**Verified by execution** — all three imports raise.
`run_scheduled_task` is AGENTS.md critical path #2 and has never worked.

Fix: point each at the real implementation (`fetch_url` → the existing
`_make_fetch_url_handler`; `run_scheduled_task` → `orchestrator.run_scheduled_task`),
delete `search_episodes` until episode search exists, and add one test per tool
asserting `result.success is True`.

### 7. `upsert_association` passes names where ids are required
**`tool_executor.py:273`**

`create_association(from_frame_id: int, to_frame_id: int, ...)` is called with
TEXT names. `schema.py:101` declares FKs with `PRAGMA foreign_keys = ON`.

**Verified by execution** — `IntegrityError: FOREIGN KEY constraint failed` for
name-based calls; the id-based path succeeds. Every call fails, and the failure is
reported to the model as a recoverable tool error.

Fix: resolve names via `get_frame_by_name`, then call by keyword.

### 8. `source_episode_id` ternary is inverted
**`tool_executor.py:236`**

**Verified by execution**: supplying `None` stores `hash(session_id) % 2**31`;
supplying a real id stores `None`. `hash()` on `str` is salted per process
(confirmed: two subprocesses returned different values), so the fake id drifts
across restarts and can collide with a real episode id.

Fix: pass the real id when given, `None` otherwise, coercing `str` → `int`.

### 9. `BraveBackend.search` returns bare `[]` where callers unpack a 2-tuple
**`search.py:511, 572`** vs **`:669`**

**Verified by execution** — `[]` unpacked as a 2-tuple raises. Every empty Brave
result and every Brave error becomes an exception. The defect is hidden by
annotations that contradict both the ABC (`:325`, which correctly declares a
tuple) and the implementations (`:369`, `:496`, which say `-> list[SearchResult]`).

Fix: `return [], []`; correct both annotations to the ABC's contract.

### 10. `/og-preview` overstates its SSRF protection
**`og_preview.py:143` vs `:151`**; **`tools.py:106-126`**

The host is checked once on the original URL, then `follow_redirects=True` with no
per-hop revalidation. `_PRIVATE_HOST_PATTERNS` (`:30-38`) omits `169.254.0.0/16`
(link-local — the cloud-metadata target), `100.64.0.0/10`, `fc00::/7`, and
integer/hex host forms. `tools.py` has **no** host validation at all, and its
`MAX_FETCH_BYTES` cap is cosmetic (`r.content` has already buffered the body).
Extracted content is then persisted into memory frames, so an internal read is
laundered into the user's brain.

Fix: resolve the host, reject private/loopback/link-local/reserved via
`ipaddress`, re-check on every `response.next_request.url`, and stream with a real
byte cap. Make the docstring match.

### 11. Feedback pipeline is a no-op that reports success
**`main.py:1284`**, **`store.py:1959-1960`**, **`MessageList.tsx:11`**

**Verified.** `apply_positive_feedback` returns 0 immediately for a falsy id, and
the frontend hardcodes `postFeedback(null, ...)`. Every reaction writes a
`feedback` row, updates zero confidences, and returns
`{"status":"ok","slots_updated":0}`. The confidence-reinforcement loop described
in AGENTS.md has never run from the web UI.

Fix: send the assistant turn's `session_id` (the param is already documented as a
session id at `store.py:1961-1963`); surface a non-zero result.

---

## P1 — Duplication that has already caused divergence

### 12. `chat()` and `chat_stream()` have drifted in six ways
**`orchestrator.py`**

`/chat/stream` is the path the frontend actually uses. Divergences **verified**:

| Behaviour | `chat()` | `chat_stream()` |
|---|---|---|
| `filter_relevant` timeout | `asyncio.wait_for` (`:977`) | **none** (`:2001`) |
| Generation failure | graceful message (`:1213`) | unhandled (`:2222`) |
| Math result → memory | stored (`:876`) | **dropped** (`:1927`) |
| Learning/conflict alerts | created (`:1310`) | **absent** |
| `**Sources:**` block | appended (`:1291`) | **absent** |
| Streaming correction re-runs `chat()` | — | double episode log + 2 redundant LLM calls (`:1889`) |

Fix: extract the shared blocks into helpers and call them from both paths. Where
behaviour differs deliberately, say so in a comment.

**Done, including the sixth row.** The correction branch is now the shared
`Orchestrator._run_correction(...)`, which receives the already-resolved session id,
user-episode id, and memory context, so `chat_stream()` no longer re-enters `chat()`.
Before: the user turn was logged twice and routing/extraction ran twice.
`test_streamed_correction_logs_the_user_turn_once` pins it (verified failing pre-fix:
"user turn logged 2x").

### 13. `run_now` returns a brand-new session id
**`orchestrator.py:1424`** — mints `uuid4()` instead of using the `session_id`
argument it was handed, so the next message starts an empty session.

### 14. Session label uses `MAX()` over TEXT
**`store.py:1614`** and **`:1706`** — **verified by execution**: a session whose
turns are `"aaa first"` then `"zzz last"` is labelled `"zzz last"`. `MAX` over TEXT
is the lexicographic maximum. Fix with a correlated subquery on `MIN(id)`.

### 15. `get_episodes_for_session` is unbounded and unscoped
**`store.py:1741-1748`** — no SQL `LIMIT`, and no `user_id` filter, while every
other read in `retrieve()` is owner-scoped. The handler's `limit=0` is also a
no-op (`episodes[-0:]` returns the whole list — **verified**).

Fix: add `AND user_id = ?`, add a `LIMIT`, and fix the `limit=0` edge.

### 16. Blocking sync I/O on the event loop
`main.py:1168-1173` (`db_backup`), `:1212-1221` (`db_restore`),
`main.py:769` (whisper — minutes of CPU-bound work), `llm_client.py:274`
(`subprocess.run`). AGENTS.md requires async on the hot path. Fix with
`asyncio.to_thread`.

### 17. `upsert_slot` splits one transaction into two commits
**`store.py:1224` and `:1235`** — if the history insert fails, a slot exists with
no audit row, and `slot_history` is the belief-revision audit trail. Also two
fsyncs where one suffices on the hottest write path. Same pattern at `:1335`/`:1350`.

---

## P2 — Latency and cost

Status: the two correctness-adjacent items are fixed; the two "big win / batching"
items remain open and are tracked here deliberately.

- **`_embed_batch` is N sequential round trips** (`llm_client.py:610-620`) and runs
  *before generation* on every search turn (5-8 serial Ollama calls). **Open** — a
  real latency item, but it changes the Ollama call shape and needs measurement on
  a live turn before shipping; not bundled into this remediation.
- **Negative capability caching** (`llm_client.py:227-235`). **Fixed** — only a
  successful `/api/show` probe is cached; a probe failure returns `[]` without being
  remembered (`test_llm_client_capabilities.py`, target cases verified failing
  pre-fix).
- **`chat_stream` never sends `think=False`** (`llm_client.py:485-486`).
  **Fixed** — `chat_stream` sets `think` whenever it is not `None`, matching `chat()`.
- **Answer arrives whole, not token by token** — documented at
  `assistant/AGENTS.md:56`. **Open, product decision.** `TextDeltaEvent` is never
  constructed in the default config, so `ttft_ms ≈ turn_total_ms`. Biggest single UX
  win available; the open question (what happens when a round turns out to be a tool
  call after its prose has been shown) is a product decision, not a bug.
- **N+1 queries where batching helpers already exist in the same file**:
  `search_similar_frames` (`store.py:1164`), `embed_frames` (`store.py:787`),
  `execute_recall` (`tool_executor.py:363`), `get_due_scheduled_tasks`
  (`store.py:2417`). **Open** — correctness-neutral; fold into the next performance
  pass rather than this remediation.

---

## P3 — Correctness and hygiene

Status: fixed, with a regression test that fails against the pre-fix code, except
where noted.

- **Empty slot key makes every snippet "corroborate" the fact**
  (`extractor.py:657`): `"" in snippet_lower` is always true. **Fixed** — match on
  value only; `test_empty_slot_key_does_not_corroborate_every_snippet` (verified
  failing pre-fix: 3 domains → 0).
- **`_categorize_fact` substring matching** (`extractor.py:671-688`): `"stock"` in
  `"stockholm"`, `"law"` in `"lawrence_fountain"` — falsely high-stakes, clamped to
  0.3 reliability. **Fixed** — token-boundary matching;
  `test_substring_lookalikes_are_not_high_stakes` (verified failing pre-fix).
- **Dedup keyed on `(frame_name, value)`** (`extractor.py:176-208`) drops genuinely
  distinct facts (`strings=6` vs `tuning_standard=6`). **Not changed — product
  decision.** AGENTS.md documents cross-key dedup as intentional ("cross-key
  duplicates like `strings`/`number_of_strings` are dropped in favor of the earlier
  channel's key"). The plan's 3-tuple claim conflicts with that rule, so this is
  tracked as a deliberate product trade-off, not a bug.
- **`validate_args` returns the unvalidated dict on failure**
  (`tool_executor.py:185-190`). **Fixed** — returns `None`; `execute_tool` rejects
  with "Invalid arguments" (`test_validate_args_returns_none_on_failure`,
  `test_execute_tool_rejects_invalid_arguments`, verified failing pre-fix).
- **`extract_scheduled_task_fields` returns raw `json.loads`** (`extractor.py:1175`).
  **Fixed** — validates the parsed value is a dict before returning;
  `test_scheduled_task_non_object_json_is_rejected` (verified failing pre-fix).
- **Unbounded extraction output** (`extractor.py:45-46`). **Fixed** — `max_length`
  on slot/association fields and `max_length` on the slots/associations lists;
  `test_extraction_result_rejects_oversized_output` (verified failing pre-fix).
- **Inverted `content_domains`/`source_domains` type** (`extractor.py:35`, `:508`,
  `:744`). **Fixed** — assignments now produce lists, matching the declared type.
- **Ragged CSV aborts the row loop** (`tool_executor.py:897`, `zip(strict=True)`).
  **Fixed** — indexed loop pads short rows / ignores extra cells;
  `TestRaggedCsvIngestion` (verified failing pre-fix). Fixing it also surfaced a
  hardcoded `/app/data` in `execute_write_file`'s return path, now routed through
  `get_sandbox_root()`.
- **Sensitive data in logs at INFO** (`main.py:770` voice transcript, `main.py:1080`
  and `search.py:652-659` queries, `tool_executor.py:1273` raw args). **Fixed** —
  demoted to DEBUG with lengths / redacted arg keys.
- **`?limit=` unbounded** (`main.py:382` and four more). **Fixed** — `Query(ge=1,
  le=…)` bounds on `/search`, `/memory/search`, `/users/{id}/episodes`,
  `/chat/session/{id}/messages`, `/alerts`.
- **`app.mount("/static")` registered twice** (`main.py:296`, `:1742`). **Fixed** —
  the second (dead) mount removed.
- **API contract drift vs `frontend/src/services/api.ts`**. **Fixed:**
  `list_frames` now accepts and applies `user_id` (`test_list_frames_filters_by_owner`);
  `/search` accepts both `min_relevance` and `min_similarity`
  (`test_search_accepts_the_frontend_min_relevance_param`,
  `test_search_still_accepts_min_similarity`); feedback `kind` is validated as an
  enum (`test_feedback_kind_is_validated_as_an_enum`), and the frontend `FeedbackKind`
  type now matches. All four verified failing pre-fix. The frontend Zod schemas were
  dead code (`api()` cast without parsing); a `validate()` step now parses the
  `/memory/frames` and `/search` responses, and the `FrameSchema` was corrected to
  the real contract (`essential` is `0|1`, timestamps are nullable).
- **Dead code** (each verified zero-caller by repo-wide grep including tests):
  `store._create_backup` (plus its only `shutil`/`Path` imports),
  `orchestrator._generate_fallback_response`, `tools._get_tools_for_model` +
  `TOOLS_FOR_TOOL_MODEL`, `extractor.py` trailing unreachable raise, the duplicate
  `read_file` tool definition, `retrieval.EPISODE_DIGEST_CHARS` (dead and stale).
  **Removed. Correction:** `store.embed_frames_batch` is *not* dead — it is exercised
  by `test_memory_store.py` and `test_embedding_batching.py`, so it was kept. The
  original "zero-caller" claim did not hold across tests.
- **`upsert_scheduler_heartbeat` uses `INSERT OR REPLACE`** (`store.py:2562`).
  **Fixed** — upsert keeps the frame and slot ids stable;
  `test_scheduler_heartbeat_does_not_churn_the_frame_id` (verified failing pre-fix).

---

## Sequencing

1. **P0 #1** — live 500, zero coverage, one-line fix, test already verified
   failing. Ship alone.
2. **P0 #3, #2** — security. Containment first (cheap, no behaviour change), then
   the sandbox honesty fix.
3. **P0 #4-#9** — each with a regression test that fails before the fix.
4. **P1 #12** — the structural one. Deleting the duplication is cheaper than
   patching both copies, and it prevents the next divergence.
5. **P2, P3** — opportunistic, batched by area.

Per AGENTS.md, every non-trivial change ships with a regression test that fails
against the pre-fix code. Pre-commit: `ruff check .` + `pytest assistant/tests/`
in Docker, plus frontend lint/test if the frontend changes.
