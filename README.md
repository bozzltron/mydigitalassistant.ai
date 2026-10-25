# MyDigitalAssistant.ai

A privacy-first cognitive digital assistant that remembers, learns, and
error-corrects. All inference runs locally through Ollama; nothing leaves your
machine unless you explicitly opt into an external search backend.

**Current release: [`v0.1.1-alpha`](docs/RELEASE_NOTES.md)** — the second alpha.
Alpha software, built for a single household, not hardened for untrusted
multi-user input or exposure beyond localhost.

## What it is

Most assistants are stateless: every conversation starts from zero, and the model
weights never change. This one keeps a memory instead. Facts you tell it are
stored as **frames** (entities, concepts, events) with key/value **slots**, a
confidence score, and the episode they came from. It recalls them by semantic
search and by walking a graph of typed associations, and it revises them when you
correct it — keeping the old value in an audit trail rather than overwriting it.

Learning happens entirely in that memory layer. The model weights stay frozen.

- **Remember** — facts persist across sessions and are recalled by meaning, not
  keyword.
- **Learn** — a utility model extracts facts from every turn *before* the answer
  is generated, so the reply can acknowledge what was just stored.
- **Correct** — contradict it and the correction is parsed, validated against
  sources, and applied through a belief-revision ladder.
- **Search locally** — web search defaults to a local SearXNG instance. Results
  are retrieval-only unless they pass extraction as corroborated facts.
- **Schedule** — "add an AI briefing to my mornings" creates a real task in
  memory; each daily run's output is ordinary memory you can ask about later.
- **Talk** — hands-free voice mode with silence detection and local
  transcription.
- **Export** — download any conversation as a plain-text transcript.
- **See it** — a Brain Observatory graph and a trace panel showing what was
  searched, learned, and conflicted.

Architecture and the memory model are documented in
[`assistant/AGENTS.md`](assistant/AGENTS.md).

## Privacy — what is actually enforced

A checked-in verifier asserts each of these:

```bash
docker compose exec assistant python -m assistant.scripts.verify_security
```

- **All LLM inference is local** — Ollama on `127.0.0.1:11434`. No cloud LLM
  provider exists in the codebase.
- **Web search defaults to local SearXNG.** No query leaves the machine by
  default.
- **Brave Search is the only permitted external backend**, and only with
  explicit opt-in (`BRAVE_ENABLED=true` + `BRAVE_API_KEY`). When enabled,
  sanitized query text and your IP are sent to Brave; nothing else.
- **No telemetry, analytics, or phone-home code.**
- **Localhost-only binding.** The only published port is Caddy on
  `127.0.0.1:8443`.
- **The brain is a local SQLite database**, optionally encrypted at rest with
  SQLCipher (`DB_KEY`).
- **Secrets live in `.env`** (gitignored); only `.env.example` is committed.

## Requirements

- Docker and Docker Compose
- **Docker Desktop with 8 GB of memory** (Settings → Resources → Memory).
  Ollama runs on the **host**, not in a container, so the Docker VM's memory is
  memory the models cannot use — and Docker Desktop's 16 GB default is more than
  this stack needs. The assistant's containers use ~0.6 GB steady and a
  `docker build` peaks ~2–3 GB. On the 48 GB reference box, 16 GB starved the
  models: loading the 27B left 283 MB free and 31.9 GB of swap in use. 8 GB is the
  working figure; if you build images while another project's ~5 GB stack is in the
  same VM, stop that stack for the build or raise the limit to 10 GB. A change here
  restarts the VM, and every container with it.
- Ollama running on the **host** (not in a container), with these models pulled:

  ```bash
  # Required — the minimum fleet (~11 GB):
  ollama pull qwen3.5:9b            # chat + tools
  ollama pull qwen3.5:4b            # extraction + routing + compute
  ollama pull qwen3-embedding:0.6b  # embeddings
  ollama pull tev1:0.8b             # decision model: "does this need an action?"

  # Optional tiers — opt-in, each adds a model load:
  ollama pull nimble                # the completion nudge (off by default)
  ollama pull qwen3.8:27b           # MAX escalation (off by default)
  ```

  Emptying a role's model disables its feature cleanly rather than failing — see
  `docs/MODEL_SELECTION.md` for the minimum fleet and what each tier buys.

- Recommended host environment so large models stay resident between turns —
  reloading a 27B model costs tens of seconds. **The host daemon also caps how many
  models stay loaded at once (default 3), and the warm set already fills that** — a
  fourth model evicts a warm one, so the next normal turn pays a reload:

  ```bash
  # macOS — the Ollama.app daemon reads these at launch:
  launchctl setenv OLLAMA_KEEP_ALIVE -1
  launchctl setenv OLLAMA_MAX_LOADED_MODELS 4
  # ...then quit and reopen Ollama.app for them to take effect.

  # Linux / a manually-run `ollama serve`: put them in the service environment
  # (or `export` them before starting it).
  ```

  `4` = the warm set (chat/tools, utility, embedding, decision) plus one on-demand
  tier. The cap defaults to 3, and a fourth model evicts a warm one, so it is
  already the smallest number that keeps the warm set intact.

  **A fifth slot does not buy coexistence on a 48 GB host.** Measured 2026-10-24
  with Docker capped at 8 GB and the cap raised to 5: after a normal turn all four
  warm models were resident, and the very next Max turn left **only the 27B** —
  the memory wasn't there, so Ollama evicted them regardless of the slot budget.
  If you do have the headroom (≥64 GB, or a host without a 6.5 GB editor and a
  Supabase stack running), 5 keeps the 27B next to the warm set. Otherwise budget
  the chat model's reload (~20–30 s) after a Max turn. See
  `docs/MODEL_SELECTION.md` for the measurements.

## First run

1. **Create your config** and set at least your timezone (inside Docker an unset
   `TZ` means UTC, so "9am" would not be your morning):

   ```bash
   cp .env.example .env
   # edit .env: set TZ=America/Chicago (or your zone)
   # required: set SEARXNG_SECRET=$(openssl rand -base64 32)
   # optional: set DB_KEY=$(openssl rand -hex 32) to encrypt the brain at rest
   ```

2. **Start the services:**

   ```bash
   docker compose up -d
   ```

3. **Open the assistant:** https://localhost:8443

   The TLS certificate is self-signed for localhost, so your browser will warn
   once — accept it for this origin.

4. **Check health:**

   ```bash
   curl -k https://localhost:8443/health   # via Caddy
   curl http://127.0.0.1:8000/health       # backend directly, bare-metal only
   ```

5. **Create a user** (the CLI runs in a container — no host Python needed):

   ```bash
   ./assistant/bin/assistant users add alice
   ./assistant/bin/assistant chat
   ```

Useful CLI commands:

```bash
./assistant/bin/assistant status
./assistant/bin/assistant memory list
./assistant/bin/assistant db backup
./assistant/bin/assistant db restore <backup-file>
```

## Configuration

Everything is set in `.env`; `.env.example` documents each option with its
default. The ones you are most likely to touch:

| Setting | Purpose | Default |
|---|---|---|
| `TZ` | Your local zone — makes the daily task clock mean your morning | host-local |
| `DB_KEY` | SQLCipher key; unset means the DB is stored unencrypted | unset |
| `CHAT_MODEL` / `UTILITY_MODEL` / `EMBEDDING_MODEL` | The role-based model fleet | `qwen3.5:9b` / `qwen3.5:4b` / `qwen3-embedding:0.6b` |
| `DAILY_TASKS_TIME` | When the scheduled-task clock fires | `09:00` |
| `BRAVE_ENABLED` + `BRAVE_API_KEY` | Opt into the external Brave search backend | off |
| `SEARCH_BASE_URL` | Your local SearXNG instance | `http://127.0.0.1:8080` |

Model selection rationale lives in [`docs/MODEL_SELECTION.md`](docs/MODEL_SELECTION.md).

## Development

**Backend** — tests and lint run in the built image:

```bash
docker run -it --rm -v $(pwd):/app -w /app assistant ruff check .
docker run -it --rm -v $(pwd):/app -v assistant-test-data:/app/data -w /app -e DATABASE_PATH=/app/data/assistant-test.db assistant pytest assistant/tests/ --timeout=120
```

**Frontend** — the SolidJS dev server hot-reloads on `http://localhost:5174` (the
container listens on 5173 and is published on 5174):

```bash
cd frontend && npm run lint && npm run test
```

**Full gate** — dead-code check, frontend lint/test/build, critical-path tests,
then the full backend suite in both plain and encrypted DB modes:

```bash
./run_ci.sh
```

**Security verification:**

```bash
docker compose exec assistant python -m assistant.scripts.verify_security
```

Guide for contributors (and agents): [`RUNBOOK.md`](RUNBOOK.md). Design
principles and conventions: [`AGENTS.md`](AGENTS.md) and
[`assistant/AGENTS.md`](assistant/AGENTS.md). Agent runbooks and model
assignment: [`docs/AGENTIC_WORKFLOW.md`](docs/AGENTIC_WORKFLOW.md).

## Documentation

- [`docs/RELEASE_NOTES.md`](docs/RELEASE_NOTES.md) — what is in each release
- [`docs/TODO.md`](docs/TODO.md) — open known issues (not fixed in this alpha)
- [`docs/TESTING.md`](docs/TESTING.md) — testing strategy
- [`docs/SECURITY.md`](docs/SECURITY.md) — security model and verification
- [`docs/MODEL_SELECTION.md`](docs/MODEL_SELECTION.md) — model fleet rationale
- [`docs/FILES.md`](docs/FILES.md) — file sandbox and document handling
- [`docs/CONVERSATION_MODE_FLOW.md`](docs/CONVERSATION_MODE_FLOW.md) — voice mode
- [`docs/DEPLOYMENT_MODES.md`](docs/DEPLOYMENT_MODES.md) — dev vs prod compose
- [`docs/AGENTIC_WORKFLOW.md`](docs/AGENTIC_WORKFLOW.md) — agent runbooks (`ship-feature`, `self-improve`) and per-agent models

## What this alpha is not

- Not multi-user hardened — built for one household.
- Not feature-frozen — interfaces and data shapes may change between alphas.
- The default tool path does not stream the answer token-by-token; you see
  progress stages, then the complete answer.

## License

See [`LICENSE`](LICENSE).
