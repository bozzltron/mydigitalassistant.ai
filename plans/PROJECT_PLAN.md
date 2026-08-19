# Project Plan — Memory Rework, Encryption & Coding

**Status:** active
**Last updated:** 2026-08-19

---

## 1. Motivation

The current memory system works for MVP but has three structural gaps:

1. **No working memory** — the agent cannot distinguish what's active in the current session from what's in long-term storage. All context comes from retrieval, which mixes recent and stale facts with equal weight.
2. **Shallow conflict resolution** — the current "confidence + recency" heuristic is ad hoc. It handles simple contradictions but fails when multiple sources disagree in complex ways.
3. **No encryption at rest** — `assistant.db` is plaintext. A stolen laptop means a full memory dump with no protection.

Separately, the agent's ability to **pair-program** is limited because it has no persistent model of the codebase: it doesn't remember architectural decisions, refactorings done, or the structure of the project beyond what the current context window provides.

---

## 2. Three Thrusts

### 2A — Memory Rework

#### 2A.1 — Working Memory Module

**Goal:** Separate short-term working memory (current session, active frames) from long-term semantic memory.

- Add a `working_memory` table: `id`, `frame_id`, `entered_at`, `access_count`, `last_accessed_at`.
- `access_count` bumps on each retrieval; `last_accessed_at` enables LRU eviction.
- Working memory frames are promoted when accessed frequently in one session; long-term frames are demoted if untouched for N days.
- Size cap: configurable max working memory entries (default 50). Evict least-recently-used when full.
- The retrieval pipeline biases toward working memory entries before hitting sqlite-vec.

**Files:** `assistant/backend/memory/working_memory.py`, `assistant/backend/memory/store.py`

#### 2A.2 — AGM Belief Revision

**Goal:** Replace the ad hoc conflict resolver with formal AGM belief revision postulates.

- Keep `memory/confidence.py` for confidence math (repeat bumps, initial values).
- Add `memory/belief_revision.py` with:
  - `expand(frame, slot)` — add new belief without removing old
  - `revise(frame, slot, new_value)` — add new belief, contract conflicting
  - `contract(frame, slot)` — remove a belief, preserve logical consistency
  - `update(frame, slot, new_value)` — fast path: same as revise when no conflict
- The current auto-resolve logic becomes a special case of `revise` with source reliability weighting.
- Slot history records the full belief-change operation type (expand/revise/contract).
- User corrections trigger `contract` on the wrong slot, then `expand` on the correct one.

**Files:** `assistant/backend/memory/belief_revision.py`, extend `assistant/backend/memory/confidence.py`

#### 2A.3 — Priority Decay (Garbage Collection)

**Goal:** Low-priority facts that are never re-accessed should decay toward forgotten.

- Add `last_strengthened_at` and `访问计数` (access_count) columns to `slots`.
- After N days (configurable, default 30) with no access and priority < 0.5, begin decay: `priority *= 0.95` per week.
- Priority 1.0 (essential) slots are exempt.
- Add `assistant db gc` CLI command: runs decay math, soft-deletes slots where priority < 0.1.
- Decay is **not** automatic on every query — run gc as a scheduled task or on-demand.

**Files:** `assistant/backend/memory/gc.py`, `assistant/backend/cli/commands.py`

#### 2A.4 — Source Reliability Tracking

**Goal:** Facts from search are less reliable than facts the user confirmed. Track this formally.

- Add `source_reliability` column to `slots` (0.0–1.0).
- Default reliability by source type: `user=0.95`, `search=0.5`, `inference=0.6`, `manual_override=1.0`.
- Reliability bumps (+0.05) when corroborated by another independent source within 7 days.
- Belief revision uses reliability as a weighting factor alongside confidence.

**Files:** `assistant/backend/memory/store.py`, `assistant/backend/memory/confidence.py`

#### 2A.5 — Embedding Model Migration Path

**Goal:** Support swapping the embedding model without rebuilding the database from scratch.

- Add `embedding_model` column to `frames` and `associations`.
- Add `embedding_dimension` to a new `metadata` table (key/value store for schema versioning).
- On startup, check `metadata.embedding_model` against `EMBEDDING_MODEL` env var. If mismatch, flag for re-embedding.
- Add `assistant db reembed --model <model>` CLI command: re-embed all frames in batches of 100, update `embedding_model` column, store new dimension.
- sqlite-vec stores embeddings keyed by `(frame_id, embedding_model)` — old embeddings are retained until re-embed completes successfully.

**Files:** `assistant/backend/memory/store.py`, `assistant/backend/memory/retrieval.py`, `assistant/backend/cli/commands.py`

---

### 2B — Encryption

#### 2B.1 — SQLite Encryption at Rest (SQLCipher)

**Goal:** `assistant.db` is encrypted with a user-provided key. No plaintext on disk.

- Replace `aiosqlite` with `sqlcipher` driver (or `aiosqlite` + `sqlcipher` via `pysqlite3`). SQLCipher is a SQLite build with AES-256 encryption.
- Add `DB_KEY` to `.env` (and `.env.example`). The key is derived via PBKDF2 if a passphrase is provided.
- On first boot with a new DB and no key, prompt the user to set a key or opt out.
- If `DB_KEY` is empty/null, use the existing unencrypted behavior (backwards compatible).
- Encrypted backups (see 2B.2) use the same key.
- The key is never logged, never in stack traces, never in memory dumps if we can avoid it.

**Files:** `assistant/backend/memory/store.py`, `assistant/backend/config.py`, `assistant/bin/assistant`

#### 2B.2 — Encrypted Backup/Restore

**Goal:** Backups are encrypted and portable.

- Add `assistant db backup --path <dest>` CLI command: exports full DB to an encrypted JSON bundle (all tables, not just frames).
- Bundle format: `{"version": 1, "key_id": "md5-of-key", "encrypted_data": "<base64-aes-256-gcm>", "iv": "<base64>"}`.
- Add `assistant db restore --path <backup>`: verifies key, decrypts, loads into DB (replacing current memory).
- Backup file is AES-256-GCM encrypted with the DB key.
- This complements (not replaces) the Solid pod sync plan.

**Files:** `assistant/backend/cli/commands.py`, `assistant/backend/memory/backup.py`

#### 2B.3 — TLS for Local LLM and Search (Defense in Depth)

**Goal:** Even though Ollama and SearXNG are on localhost, encrypt intra-host traffic.

- Ollama supports TLS. Configure `OLLAMA_TLS_CERT`/`OLLAMA_TLS_KEY` in `.env` if Ollama is configured with TLS.
- SearXNG can be configured with TLS. Update `docker-compose.yml` to expose SearXNG on `127.0.0.1` with TLS if available.
- This is defense-in-depth: local-loop attacks on macOS are rare but TLS adds a layer.

**Files:** `.env.example`, `docker-compose.yml`

---

### 2C — Coding Persona

#### 2C.1 — Coding Persona System Prompt

**Goal:** When the user is coding, the agent switches to a programming-focused persona with injected project context.

- Add `PERSONA` env var (`default`, `coding`, `creative`, etc.).
- Add `assistant/backend/personas/coding.py`: returns a system prompt fragment with:
  - Project structure summary (generated from `ls -R` once and cached)
  - Key file descriptions: `store.py`, `retrieval.py`, `orchestrator.py`, `extractor.py`, etc.
  - Active interface/function signatures from the relevant modules
  - The agent's own architectural decisions (e.g. "frames use UUIDs as primary keys")
  - Recent refactorings or architectural changes (stored as episodic memories tagged `code_architecture`)
- When `PERSONA=coding`, inject the persona fragment into the chat system prompt.
- `PERSONA` can be changed mid-session via a CLI command or chat command: "switch to coding mode".

**Files:** `assistant/backend/personas/`, `assistant/backend/pipeline/llm_client.py`, `assistant/backend/config.py`

#### 2C.2 — Code Memory Extraction

**Goal:** The agent learns from its own coding sessions and remembers decisions, patterns, and project structure.

- When in coding persona, after each LLM response that produces code, extract and store:
  - A `CodePattern` frame: the filename, the pattern type (e.g. "repository-pattern", "pydantic-schema"), and a summary.
  - A `SymbolRef` frame: function/class name, file, signature hash, last seen.
  - An `ArchitectureDecision` frame: the decision made, the alternatives considered, the reason.
- This is similar to how the extractor learns facts from conversation, but specialized for code.
- Retrieval in coding persona biases heavily toward `CodePattern`, `SymbolRef`, and `ArchitectureDecision` frames.

**Files:** `assistant/backend/pipeline/extractor.py`, new `assistant/backend/pipeline/code_extractor.py`

#### 2C.3 — Multi-File Context Tracking

**Goal:** When working on a task spanning multiple files, the agent maintains context across files without re-explaining.

- Add `CodeSession` table: `id`, `session_id`, `files_touched` (JSON list), `created_at`, `last_active_at`.
- When a user asks to "refactor X across Y files", create a `CodeSession` and track all files involved.
- The coding persona's context injection includes: "You are currently working on session `<id>` touching: `<file1>`, `<file2>`..."
- If the user switches to an unrelated task, mark the `CodeSession` inactive; don't inject stale file context.
- Limit to 10 active `CodeSession` rows (oldest auto-archived to episodic memory).

**Files:** `assistant/backend/memory/store.py`, `assistant/backend/pipeline/orchestrator.py`

#### 2C.4 — Context Window Budget Management

**Goal:** For large code tasks, manage the context window proactively rather than hitting OOM or truncation.

- Add `MAX_CONTEXT_TURNS` config (default 20) — maximum conversation turns to include in context.
- For coding persona, maintain a "context budget" string that includes: system prompt + recent episode + retrieved code memory + current file contents (truncated to last N lines per file).
- Add `assistant context stats` CLI command: shows current context window size estimate and what's included.

**Files:** `assistant/backend/pipeline/llm_client.py`, `assistant/backend/cli/commands.py`

---

## 3. Implementation Order

### Phase 1: Foundation (no feature changes, just robustness)

1. **SQLCipher encryption** (2B.1) — do this first because it affects the DB schema. If we add columns, we want them encrypted.
2. **Source reliability tracking** (2A.4) — additive schema change, no behavior change to existing logic.
3. **Encrypted backup/restore** (2B.2) — depends on 2B.1.

### Phase 2: Memory Rework

4. **Working memory module** (2A.1) — new table, retrieval bias, LRU eviction.
5. **AGM belief revision** (2A.2) — new module, wire into the conflict resolution path, keep old confidence math for compatibility.
6. **Priority decay GC** (2A.3) — depends on 2A.1 and 2A.2.
7. **Embedding model migration** (2A.5) — additive, re-embed command.

### Phase 3: Coding

8. **Coding persona** (2C.1) — persona system, env var, context injection.
9. **Code memory extraction** (2C.2) — code extractor, code-specific frames.
10. **Multi-file context tracking** (2C.3) — CodeSession table, session awareness.
11. **Context window budget** (2C.4) — budget management, stats CLI.

### Phase 4: Hardening

12. **Sources intelligence** — only show citations block for `task_type=search`. Show memory source indicator inline for introspective tasks (e.g. "Answered from memory · 3 facts retrieved"). Do not show citations for introspective, functional, or correction tasks.
13. **TLS for local LLM/search** (2B.3) — environment hardening.

---

## 4. Schema Changes Summary

| Table | Change |
|---|---|
| `slots` | Add `source_reliability` (float), `last_strengthened_at` (datetime nullable) |
| `frames` | Add `embedding_model` (text nullable) |
| `associations` | Add `embedding_model` (text nullable) |
| `metadata` | New: `key`, `value` — schema version, embedding model, embedding dimension |
| `working_memory` | New: `id`, `frame_id`, `entered_at`, `access_count`, `last_accessed_at` |
| `code_sessions` | New: `id`, `session_id`, `files_touched` (JSON), `created_at`, `last_active_at` |

All new columns are nullable with sensible defaults. No existing columns are modified or removed.

---

## 5. Security Considerations

- `DB_KEY` must never appear in logs, stack traces, or Git commits.
- SQLCipher key derivation uses PBKDF2 with 256k iterations minimum.
- Encrypted backups use AES-256-GCM with a random IV per backup.
- All new LLM prompts are screened for embedded secrets before memory extraction.
- TLS certs for Ollama/SearXNG should be self-signed or from a local CA; document how to generate them.

---

## 6. Testing

- All 258 existing tests must continue to pass.
- New tests for each Phase 2 module:
  - `test_working_memory.py` — LRU eviction, retrieval bias
  - `test_belief_revision.py` — expand/revise/contract postulates, legacy conflict resolution compatibility
  - `test_source_reliability.py` — corroboration bumps reliability
  - `test_gc.py` — decay math, essential-fact exemption
  - `test_backup_restore.py` — encrypted round-trip
  - `test_coding_persona.py` — persona injection, mode switching
  - `test_code_extraction.py` — code memory frames created from LLM output
  - `test_code_sessions.py` — multi-file tracking, session archival

---

## 7. Success Criteria

- `assistant db backup` produces an encrypted bundle; `assistant db restore` recovers it correctly.
- SQLCipher-encrypted DB is unreadable with standard sqlite3 (verify with `file` and hexdump).
- Working memory entries are retrieved before long-term entries when both match a query.
- AGM `revise` produces the same result as the legacy resolver for all existing conflict test cases.
- Priority decay does not affect priority-1.0 slots.
- `PERSONA=coding` injects project context; agent answers "what files handle memory?" correctly.
- Code sessions track touched files across a multi-file task.
- Context window budget is observable via `assistant context stats`.
- `pytest assistant/tests/` and `ruff check .` stay green throughout.

---

## 8. Open Questions

1. Should working memory entries be persisted across sessions (survive restart) or reset on each boot? Current design: persisted but evictable.
2. Should the agent proactively suggest memory sync to the Solid pod after a backup? Low priority.
3. Should code memory extraction run synchronously (blocking the response) or async? Async is safer for latency.
4. Should we support multiple concurrent coding sessions? Yes — session_id + archive strategy handles it.
5. SQLCipher requires a native library. Does `pysqlite3-sqlcipher` work in the Docker image? Verify early in Phase 1.
