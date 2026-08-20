# Project Plan — Memory Rework, Encryption & Hardening

**Status:** completed
**Last updated:** 2026-08-20

---

## 1. Motivation

The memory system had three structural gaps that have been addressed in Phase 2:

1. **No working memory** — fixed with LRU cache and retrieval bias.
2. **Shallow conflict resolution** — fixed with AGM belief revision.
3. **No encryption at rest** — fixed with SQLCipher in Phase 1.

Phase 4 focuses on hardening: smarter citation display and TLS for local services.

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

## 3. Implementation Order

### Phase 1: Foundation ✅ (done)

1. **SQLCipher encryption** (2B.1) — done.
2. **Source reliability tracking** (2A.4) — done.
3. **Encrypted backup/restore** (2B.2) — done.

### Phase 2: Memory Rework ✅ (done)

4. **Working memory module** (2A.1) — done.
5. **AGM belief revision** (2A.2) — done.
6. **Priority decay GC** (2A.3) — done. `assistant db gc [--dry-run]`
7. **Embedding model migration** (2A.5) — done. `assistant db reembed [--model <model>]`

### Phase 3: Hardening ✅ (done)

8. **Sources intelligence** — only show citations block for `task_type=search`. Show memory source indicator inline for introspective tasks. Do not show citations for introspective, functional, or correction tasks.
9. **TLS for local LLM/search** (2B.3) — environment hardening.

---

## 4. Schema Changes Summary

| Table | Change |
|---|---|
| `slots` | Add `source_reliability` (float), `last_strengthened_at` (datetime nullable) |
| `frames` | Add `embedding_model` (text nullable) |
| `associations` | Add `embedding_model` (text nullable) |
| `metadata` | New: `key`, `value` — schema version, embedding model, embedding dimension |
| `working_memory` | New: `id`, `frame_id`, `entered_at`, `access_count`, `last_accessed_at` |

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

- All existing tests must continue to pass.
- Phase 2 tests:
  - `test_working_memory.py` — LRU eviction, retrieval bias
  - `test_belief_revision.py` — expand/revise/contract postulates, legacy conflict resolution compatibility
  - `test_gc.py` — decay math, essential-fact exemption
  - `test_backup_restore.py` — encrypted round-trip
- Run: `./run_ci.sh` (plain + encrypted modes)

---

## 7. Success Criteria

- `assistant db backup` produces an encrypted bundle; `assistant db restore` recovers it correctly.
- SQLCipher-encrypted DB is unreadable with standard sqlite3 (verify with `file` and hexdump).
- Working memory entries are retrieved before long-term entries when both match a query.
- AGM `revise` produces the same result as the legacy resolver for all existing conflict test cases.
- Priority decay does not affect priority-1.0 slots.
- `assistant db gc [--dry-run]` runs decay math and reports scanned/decayed/soft-deleted counts.
- `assistant db reembed --model <model>` re-embeds frames in batches and updates metadata.
- Startup logs a warning if `metadata.embedding_model` differs from `EMBEDDING_MODEL` env var.
- Sources intelligence: citations only shown for `task_type=search`.
- `./run_ci.sh` passes in both plain and encrypted modes.

---

## 8. Open Questions

1. Should working memory entries be persisted across sessions (survive restart) or reset on each boot? Current design: persisted but evictable.
2. Should the agent proactively suggest memory sync to the Solid pod after a backup? Low priority.
3. SQLCipher requires a native library. Does `pysqlite3-sqlcipher` work in the Docker image? Verified in Phase 1: yes.
