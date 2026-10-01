# MyDigitalAssistant.ai — release notes

## v0.2.0-alpha

**The brain stops lying to itself.** This release is about accuracy: memory that
recorded decisions as undecided, files that duplicated their own contents, and a bell
full of notices nobody could act on. Four plans shipped, one closed on measurement.

### Alerts are memory

An alert is now a **frame of type `alert`**, not a row in a notifications table, and
the `alerts` table is gone. The bell is a view over memory, which means retrieval can
raise an alert *when it is contextually relevant* — not only when the user opens it.

- **The presence rule.** An alert is warranted when the agent learned something and
  the user **was not there to hear it**. Measured before this: of 111 alert rows, 103
  were mechanism — 54 "Task completed: …", 24 search notices fired mid-conversation,
  25 auto-resolved conflicts announced to the user watching them resolve.
- **An alert closes by being answered, not by being read.** There is no read flag —
  which is why 103 rows previously sat unread. Resolution happens in a conversation
  the user picks; the alert closes when they reply there. A new thread per alert is
  the fallback, not the default, so alert threads do not accumulate.
- **A task failure still alerts** (`task_failure`), and a task *completion* does not.
  The user asked for a recurring task and it is silently broken; that is the presence
  rule applied, not an exception to it.
- **The bell resolves rather than dismisses.** Open it, pick the conversation to
  settle an alert in, and go there. Inline SVG icons and a colour change on the trigger
  replace the emoji and the read badge.

### Files: memory holds what a file *is*, never what it *contains*

The code had already decided this and enforced it only at render time, so four sites
kept writing file content into memory — including `read_file`, which wrote a preview
on **every read**. The cost was not the wasted rows:

```
except FileNotFoundError:
    pass  # Not on disk — fall back to memory slots.
if not content:
    content = slots_dict.get("file_content_preview") or ""
```

A missing file returned a stale 200-character copy and the model answered believing it
had read the file. Content keys are now **refused at write time** by `upsert_slot`
(`FileContentInMemoryError`), the stat is now reported as missing, and 10 existing
preview rows were removed.

### Write-path hygiene

- **No blank records.** Frame names, slot keys, and slot values are validated at every
  write path. Frame 4387 — an empty-named frame created from a single paste turn — is
  gone, and its origin is closed.
- **A blank value is refused at the store, not just at extraction.** The extraction
  guard was not enough: CSV row ingestion writes one slot per column, including empty
  cells, which produced 65 blank-value slots in a single day. The refusal now sits in
  `upsert_slot`, where every writer funnels, and CSV ingestion skips empty cells. An
  empty cell is an absent fact, not a fact with an empty value.
- **The summarizer stopped arguing with itself.** Counters (`turn_count`, `date_end`)
  were written as beliefs and "conflicted" with every run: 3,307 conflict rows, 86% of
  the ledger, on zero disagreements. They are written directly now.
- **`POST /tasks/run-due` returns immediately** instead of timing out at Caddy's 300s
  while its work completed. It hands off to the scheduler loop — one execution path
  instead of two.
- **Scheduler re-enabled and verified**, embeddings migrated to a single model, 92
  unverifiable frames re-indexed.

### Conflicts: measured, and the ladder works

A long-held belief that the confidence ladder never discriminated was **wrong**, and
the correction is the point of this release.

- **The ladder fires.** A user-stated fact (reliability 0.99) against a search attempt
  (0.5) resolves to the user's value. The `grok` near-rename was not luck — it was
  rung 1 working, and it *read* as luck only because the record said `pending`.
- **`EXISTING_WINS` is recorded as decided**, not deferred: `auto_resolved` with
  `resolved_value` set. 252 of 279 "pending" rows were decided-and-applied, which
  inflated the apparent backlog 10×.
- **Every conflict records its decision inputs** — source reliability, confidence, and
  priority for both sides. `slot_history` keeps values but no provenance, so before
  these columns a past decision could not be audited at all: the gating experiment was
  inconclusive for 100% of its sample.
- **Plan D closed.** A model that reasons over conflicts would duplicate a working
  comparator. The gate was met in the negative.

### Web search and content the user supplies

- Supplied content (a pasted list, and the class generally) is registered as memory —
  it previously produced nothing at all, so a 45-URL list survived one turn and was
  unreachable after. It reaches the prompt on both orchestrator paths.
- `web_search` stays available on every turn. Three attempts were made to fix the
  follow-up failure with a static rule; all three were reverted. The model decides.

### Process

- **Plans are transient and now wiped when shipped.** Only active work remains in
  `/plans/`. Git history is the record.
- **Experiments are pre-registered** with falsification conditions fixed before data,
  and read-only against a brain copy. Two this cycle were disproved rather than
  confirmed, and both are written up as such.

### Known gaps

- The **journal** (Plan E phase 3) is not started — alerts may already cover the need.
- **~27 conflicts** are genuinely open, now distinguishable from the 252 mislabelled.
- **Portable brains are implemented but not wired.** `export_portable_brain` /
  `import_portable_brain` exist in `backend/memory/backup.py` with 12 tests, but no API
  endpoint and no CLI command reach them. `AGENTS.md` used to describe them as
  available; it now says so accurately. Small to wire.

## v0.1.1-alpha

**Memory is never forgotten on a timer.** This release removes the background
garbage collector and makes the maintenance that remains cheaper and more
predictable.

- **Time-based decay is gone.** The scheduler used to lower slot priority and
  soft-delete "stale" frames weekly, dropping their embedding vectors. It could
  only ever act on rows already below the default priority — i.e. data the user
  had already forgotten — so it was a timer that re-forgot things on a delay.
  Memory now leaves only through an explicit `forget` or a deliberate deletion.
- **Merging is ad hoc; backups are not.** Near-duplicate frames merge as soon as
  duplicates are found (the 6-hour consolidation tick is a *look* cadence, not a
  merge cadence). The brain snapshot moved to its own predictable 12-hour clock,
  so snapshot count no longer scales with merge frequency. A merge that runs
  without a fresh snapshot takes one first, so a merge is never unprotected.
- **Faster re-indexing.** Embedding top-up (turns and frames with missing/stale
  embeddings) runs every 6 hours as its own job, with no backup attached.
- **Removed:** the `assistant db gc` CLI command, and the `/plans/` archive
  convention (plans are transient now — git history is the record).
- **Docs:** `ARCHITECTURE.md` now documents the scheduler; several stale docs
  were removed and drifted claims corrected.

Full validation: `./run_ci.sh` green (dead-code check, frontend lint/test/build,
critical-path tests, backend suite in plain and encrypted modes).

---

## v0.1.0-alpha

**The first alpha.** A privacy-first cognitive digital assistant that remembers
what you tell it, learns over time, and corrects itself when it is wrong. All
inference runs locally through Ollama. Nothing leaves your machine unless you
explicitly opt into an external search backend.

This is alpha software for a **single household**. It is not hardened for
untrusted multi-user input or for exposure beyond localhost.

---

## What it does

- **Remembers.** Facts are stored as frames (entities/concepts/events) with
  key/value slots, a confidence score, and a source episode. It recalls them by
  semantic search plus a graph walk over typed associations.
- **Learns from every turn.** A utility model extracts facts before the answer is
  generated, so what it just learned can be acknowledged truthfully in its own
  words.
- **Corrects itself.** You can contradict it. The correction is parsed,
  validated against sources, and applied through a belief-revision ladder; the
  old value is preserved in an audit trail rather than overwritten.
- **Reacts to feedback.** Positive feedback reinforces the facts behind a turn,
  negative feedback weakens them.
- **Searches the web, locally.** Default search is a local SearXNG instance, so
  no query leaves the machine. Results are retrieval-only unless they pass
  extraction as high-signal, corroborated facts.
- **Runs scheduled tasks.** "Add an AI briefing to my mornings" creates a real
  memory frame; the agent runs it on a daily clock and the output is ordinary
  memory you can ask about later.
- **Talks.** Hands-free voice mode with silence detection and local
  transcription (faster-whisper).
- **Shows its work.** A Brain Observatory visualizes the frame graph; a trace
  panel shows what was searched, learned, and conflicted.

## Privacy posture

This is the point of the project, so it is worth stating plainly what is
enforced (there is a checked-in verifier — `assistant-verify-security` — that
asserts each of these):

- **All LLM inference is local** via Ollama on `127.0.0.1:11434`. There is no
  cloud LLM provider in the codebase.
- **Web search defaults to a local SearXNG instance.** No query leaves the
  machine in the default configuration.
- **The only permitted external backend is Brave Search**, and only with
  explicit opt-in (`BRAVE_ENABLED=true` + `BRAVE_API_KEY`). When enabled,
  sanitized query text and your IP address are sent to Brave. Nothing else.
- **No telemetry, analytics, or phone-home code of any kind.**
- **Everything binds to localhost.** FastAPI and SearXNG never listen on a public
  interface; the only published port is Caddy on `127.0.0.1:8443`.
- **The brain is a local SQLite database**, optionally encrypted at rest with
  SQLCipher (`DB_KEY`; AES-256).
- **Secrets live in `.env`** (gitignored). Only `.env.example` is committed.

## What's new in this alpha

This release is the product of a full principal-level review of the Python
backend, and the fixes that came out of it. Every non-trivial fix ships with a
regression test proven to fail against the pre-fix code.

**Security**
- `fetch_url` and `/og-preview` now resolve and validate the host on **every**
  redirect hop and reject private/loopback/link-local/reserved addresses, with a
  real streaming byte cap. Previously the host was checked once and redirects
  were followed unchecked.
- The math sandbox is honest about what it enforces: it runs off the event loop,
  passes a minimal environment (no `DB_KEY`/`BRAVE_API_KEY` leakage), and kills
  the whole process group on timeout.
- Model-authored `file_*` slot keys can no longer become filesystem paths.

**Correctness**
- Fixed a 500 on the scheduled-task search path (`skip_route`), the most common
  `merge_frames` shape raising `IntegrityError`, frame resurrection discarding
  every field but `name`, three registered tools that could never succeed, name/id
  confusion in `upsert_association`, an inverted `source_episode_id` ternary, and
  Brave returning a bare list where callers unpack a tuple.
- The web feedback buttons now actually reinforce or weaken memory; they were a
  silent no-op.
- `/chat/stream` (the path the UI uses) now matches `chat()`: bounded search
  relevance gate, graceful generation failure, compute results stored, learning
  alerts raised, sources footer present, and corrections no longer re-run the
  whole turn.

**Hygiene**
- Bounded `?limit=` endpoints, bounded extraction output, tolerant ragged-CSV
  ingestion, stable scheduler-heartbeat ids, and the frontend/backend API contract
  reconciled (the frontend Zod schemas now actually parse responses).

## Requirements

- Docker and Docker Compose
- Ollama on the host, with these models pulled:

  ```bash
  ollama pull qwen3.5:9b            # chat + tools
  ollama pull qwen3.5:4b            # extraction + routing
  ollama pull qwen3-embedding:0.6b  # embeddings
  ollama pull qwen3.8:27b           # on-demand escalation + math (optional but recommended)
  ```

- Recommended host environment so large models stay warm between turns
  (reloading a 27B model costs tens of seconds):
  `OLLAMA_KEEP_ALIVE=-1` and `OLLAMA_MAX_LOADED_MODELS=4`.

## Quick start

See `README.md` for the full first-run walkthrough. In short:

```bash
cp .env.example .env      # then set TZ, and optionally DB_KEY
docker compose up -d
```

Open the assistant at **https://localhost:8443**.

## What this alpha is not

- **Not multi-user hardened.** User isolation exists, but the release target is a
  single household. Do not expose it beyond localhost or treat it as safe for
  untrusted users yet.
- **Not feature-frozen.** Interfaces and data shapes may change between alphas.
- **Answers do not stream token-by-token** on the default tool path; you see
  progress stages, then the complete answer. This is a known, documented gap.

## Known issues

- `docs/TODO.md` — the open items from the memory/pipeline audit that are **not**
  addressed in this alpha (brain-import provenance, consolidation frame-id
  rewrite, search-fact episode linkage, and smaller debt).

## License

See `LICENSE`.

## Acknowledgements

Built on Ollama, FastAPI, SQLite + sqlite-vec, SolidJS, SearXNG, and Caddy.
