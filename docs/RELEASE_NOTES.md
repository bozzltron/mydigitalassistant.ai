# MyDigitalAssistant.ai — release notes

## v0.4.0-alpha

**One top bar, and the small lies it was telling.** This release makes the
header a single coherent control strip, and fixes two places where the UI showed
a state that was not true — a voice turn that said "Listening" while it was
transcribing, and an alert that looked unresolved after it had been resolved.

### The top bar is one set

The header mixed four button styles, three corner radii (6px, 20px, bare) and
four controls with no icon. Now:

- **One shared style.** Every control uses `.topbar-btn` — same radius, padding,
  font and hover.
- **Every control has an inline SVG icon**, coloured by `currentColor` so it
  matches its label. New gains a `+`, Let's talk a microphone, Files a folder,
  Brain a brain; Settings is icon-only.
- **A deliberate order:** Alerts, Let's talk, Files, Brain, Settings, Trash. New
  stays after the conversation switcher.
- **The voice status bar takes Let's talk's place** while a turn is live — it
  already carried the same stop/cancel actions, so showing both was redundant.

### Two UI states that were lying

- **"Transcribing…" now appears during a conversation turn.** The conversation
  capture hook kept its own private state and never advanced the global voice
  status, so the indicator read "Listening…" through the whole transcription.
- **Resolving an alert into the conversation you are already viewing now shows
  it.** The message-loading effect tracked the active conversation's id, so
  re-opening the same conversation was a no-op: the seeded message was written
  server-side and never appeared, making the alert look unresolved.

### One dependency source

`pyproject.toml` omitted `sqlcipher3`, `cryptography`, `faster-whisper` and
`python-multipart`, so the documented `pip install -e .` produced an install with
no SQLCipher — no encrypted brain. They are declared now, the Dockerfile builds
its wheels from `.[dev]` instead of a hand-maintained second list, and the stale
`assistant/pyproject.toml` is gone.

### Icons are SVG, not emoji

The file browser, frame detail and the modal close buttons used emoji and text
glyphs as icons. They are inline SVG now, from one shared set. The design-system
guard reads `.ts` files too — it only scanned `.tsx`, so a literal inline style
emitted in an HTML string slipped past it, which is how the tooltip's
`style="color:…"` survived; it is a custom property now.

### Clean ship

Dead CSS from the pre-Solid voice UI removed; stale build artifacts untracked and
gitignored; `.env.example` gained the one key it was missing; two experiment
plans now state whether they are pending or superseded.

## v0.3.0-alpha

**It reads your documents, and it remembers what it showed you.** This release
opens the assistant to the files a household actually has — PDFs, Word, Excel,
PowerPoint, and the legacy formats around them — and stops the chat losing its
imagery on reload. Along the way it fixes a summary frame that was never indexed,
the alerts that flickered, and a conversation that hid below the fold.

### It reads documents

The upload path accepted text and structured files and nothing else; a PDF or a
`.docx` was either rejected or decoded as UTF-8 noise. It now reads:

- **PDF** (`pypdf`, first 50 pages), **Word** (`.docx`), **Excel** (`.xlsx`) and
  **PowerPoint** (`.pptx`) — the modern Office set.
- **The business long tail:** `.rtf`, OpenDocument (`.odt`/`.ods`/`.odp`), legacy
  Excel (`.xls`), saved email (`.eml`) and tab-separated data (`.tsv`).
- **iCal via `icalendar`**, replacing a regex that missed line folding and TZIDs.

Every reader is offline and pure-Python (no system binaries), extraction runs off
the event loop so a large document cannot stall a request, and XML is parsed with
`defusedxml` so a hostile file cannot expand entities. Legacy `.doc`/`.ppt` have
no good offline reader and say so, pointing at `.docx`/`.pdf`.

### The chat remembers its media

Message imagery came only from a live search turn, so a refresh dropped every
image and video. A search turn now stores a compact projection of its results on
the assistant episode — titles, URLs, thumbnails and video fields, never page
bodies — and reload restores it.

- **Image-only grid tiles.** The small images dropped their hover caption; the
  details live in the lightbox, while the response hero keeps its overlay.
- **The hero follows the query.** A video query leads with the video.
- **One embed, the rest thumbnails.** A multi-video answer loads a single iframe
  and swaps the clicked thumbnail into it.
- **Full-resolution tiles**, falling back to the thumbnail when a host blocks
  hotlinking or the image exceeds the proxy's 5 MB cap.

### Links ask first

Clicking a link that leaves the assistant now names the host and asks before
opening a new tab — including the links markdown renders inside an answer, which
a per-link handler could never reach. Modals also close on Escape.

### Two UI defects from the manual pass

- **Alerts stopped flickering.** The 30s poll toggled a loading state, swapping
  the list for a spinner every half minute, and the initial fetch ran before the
  user was known and never retried — so a refresh showed an empty bell. Both fixed.
- **A new conversation is no longer buried.** The switcher sorted every never-used
  conversation last, so a just-created one sat below the fold; it is now ordered by
  creation time. ("Corrections" moved from 31st of 32 to 7th.)

### Accuracy and resilience

- **The summary frame was never indexed.** The summarizer handed `embed_frames` an
  `EmbeddingResponse` instead of a bare vector; `json.dumps` rejected it for every
  frame and the failure was swallowed. Summary frames are now embedded.
- **Idempotent hops retry; side effects do not.** Ollama, search, `fetch_url` and
  the read-only tools retry the transient class; writes, deletes and `compute` get
  a single attempt.
- **The encrypted DB retries the transient key-derivation read** that could fail a
  turn under I/O contention.

### Housekeeping

- **Dev and prod are separate Compose projects** — distinct container names,
  networks and databases — so both can run at once and dev no longer writes to the
  production brain.
- Two long-standing test defects are fixed: a missing `DB_KEY` marker on an
  encrypted-only test, and a store reused across event loops.

## v0.2.0-alpha

**The brain stops lying to itself.** This release is about accuracy: memory that
recorded decisions as undecided, files that duplicated their own contents, and a bell
full of notices nobody could act on. Four plans shipped, one closed on measurement.

### Alerts are memory

An alert is now a **frame of type `alert`**, not a row in a notifications table, and
the `alerts` table is gone. The bell is a view over memory, which means retrieval can
raise an alert *when it is contextually relevant* — not only when the user opens it.

- **The presence rule.** An alert is warranted when the agent learned something and
  the user **was not there to hear it**. In practice that means a task completion is
  not an alert (its output is already an episode), a search result learned mid-turn is
  not an alert (the user was watching), and a task **failure** is — the user asked for
  a recurring task and it is silently broken. Before this rule was enforced the bell
  was dominated by mechanism: task-completion notices, search notices fired during the
  conversation, and auto-resolved conflicts announced to the user watching them resolve.
- **An alert closes by being answered, not by being read.** There is no read flag, and
  none is coming back — a read flag is a status that changes nothing, which is why the
  old notification rows accumulated unread. Resolution happens in a conversation the
  user picks; the alert closes when they reply there. A new thread per alert is the
  fallback, not the default, so alert threads do not accumulate.
- **A task failure still alerts** (`task_failure`), and a task *completion* does not.
  That is the presence rule applied, not an exception to it.
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

A missing file returned a stale truncated copy and the model answered believing it had
read the file. Content keys are now **refused at write time** by `upsert_slot`
(`FileContentInMemoryError`), a missing file is reported as missing, and existing
preview rows are removed.

### Write-path hygiene

- **No blank records.** Frame names, slot keys, and slot values are validated at every
  write path, so a frame with no name and a slot with no key or value cannot be
  created. The extraction paths had a guard already; it was not enough, because CSV row
  ingestion writes one slot per column — including empty cells — and three other
  writers bypassed it. The refusal now sits in `upsert_slot`, where every writer
  funnels. An empty cell is an **absent** fact, so not writing a slot for it is the
  accurate representation, not a loss.
- **The summarizer stopped arguing with itself.** Counters (`turn_count`, `date_end`)
  were written as beliefs and "conflicted" with the previous run every time, making the
  summarizer the largest single source of conflict rows in the ledger — on zero
  disagreements. Counters are now written directly rather than through the belief path.
- **`POST /tasks/run-due` returns immediately** instead of timing out at Caddy's 300s
  while its work completed. It hands off to the scheduler loop — one execution path
  instead of two.
- **Embeddings migrated to a single model**, with frames that had none under the
  configured model re-indexed so they are retrievable again.

### Conflicts: measured, and the ladder works

A long-held belief that the confidence ladder never discriminated was **wrong**, and
the correction is the point of this release.

- **The ladder fires.** A user-stated fact (source reliability 0.99) against a search
  attempt (0.5) resolves to the user's value. A long-standing reading that the ladder
  never discriminated was wrong: the appearance of recency-always-winning came from
  *ties* resolving by recency, which is what a tiebreak is for, and the `grok`
  near-rename was rung 1 working — it read as luck only because the record called
  itself undecided.
- **`EXISTING_WINS` is recorded as decided**, not deferred: `auto_resolved` with
  `resolved_value` set. It previously wrote `pending`, which made a decision
  indistinguishable from a deferral and inflated the apparent review queue roughly
  tenfold.
- **Every conflict records its decision inputs** — source reliability, confidence, and
  priority for both sides. `slot_history` keeps a conflict's values but no provenance,
  so before these columns a past decision could not be audited at all.
- **Plan D closed.** A model that reasons over conflicts would duplicate a working
  comparator. The gate was met in the negative.

### Web search and content the user supplies

- Supplied content (a pasted list, and the class generally) is registered as memory —
  it previously produced nothing at all, so a pasted list survived one turn and was
  unreachable after. It reaches the prompt on both orchestrator paths.
- `web_search` stays available on every turn, including when the user supplies content
  and asks for it to be researched. Three attempts were made to fix a follow-up failure
  with a static rule (a router flag, a prompt line, and withholding the tool); all
  three were reverted. The model decides.

### Process

- **Plans are transient and now wiped when shipped.** Only active work remains in
  `/plans/`. Git history is the record.
- **Experiments are pre-registered** with falsification conditions fixed before data,
  and read-only against a brain copy. Two this cycle were disproved rather than
  confirmed, and both are written up as such.

### Clean ship

Dead code removed, verified by deletion rather than by inspection: eleven public
functions had no caller outside their own definition, and the full suite passed after
each removal — which is what proves they were dead. Eight were pre-existing (an AGM
entrenchment helper the ladder never used, unused store readers, abandoned file
generators writing to a hardcoded temp path, an encryption-migration function the CLI
does not call). One was introduced and removed in the same cycle.

### Known gaps

- The **journal** (Plan E phase 3) is not started — alerts may already cover the need.
- **Conflicts the ladder declines to settle** remain in `pending`, now distinguishable
  from the ones it decided (those record `auto_resolved` with a `resolved_value`).
  Reviewing the genuinely open set is a review, not a subsystem.
- **Portable brains are implemented but not wired.** `export_portable_brain` /
  `import_portable_brain` exist in `backend/memory/backup.py` with tests, but no API
  endpoint and no CLI command reach them. `AGENTS.md` now says so accurately rather
  than describing them as available.

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
