# MyDigitalAssistant.ai — v0.1.0-alpha

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
