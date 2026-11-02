# Cognitive Digital Assistant

Privacy-first cognitive digital assistant that learns and remembers using a
frame/slot memory model. Runs 100% locally via Ollama.

## Quick Start

### Prerequisites
- Docker installed
- Ollama running on the host with the **required** fleet pulled:
  ```bash
  ollama pull qwen3.5:9b            # chat + tools
  ollama pull qwen3.5:4b            # utility: extraction, routing, compute
  ollama pull qwen3-embedding:0.6b  # embeddings
  ollama pull tev1:0.8b             # decision model: does this turn need an action?
  ```
- Optional tiers, each adds a model load and each is off by default:
  ```bash
  ollama pull qwen3.8:27b   # MAX escalation — only if you set MAX_MODEL
  ollama pull nimble        # completion nudge — only if you enable it
  ```
  `MAX_MODEL` ships empty, and `MATH_MODEL` defaults to the already-warm utility model, so
  neither is needed for a first run. See `docs/MODEL_SELECTION.md`.
- Recommended host env so models stay warm between turns (reloading a 27B
  model costs tens of seconds): `OLLAMA_KEEP_ALIVE=-1` and
  `OLLAMA_MAX_LOADED_MODELS=4`.

### Start the backend
Docker compose files are at the repo root. Run from the repo root:
```bash
docker compose up -d
```

### Use the CLI
The CLI runs in a Docker container — no host Python needed.

**Option A: shell wrapper (recommended)**
```bash
./assistant/bin/assistant status
./assistant/bin/assistant users add alice
./assistant/bin/assistant chat
./assistant/bin/assistant memory list
./assistant/bin/assistant db backup
```

**Option B: docker compose directly**
```bash
docker compose exec assistant python -m assistant.cli.app status
docker compose exec assistant python -m assistant.cli.app chat
```

### Verify
```bash
curl -k https://localhost:8443/health
```
(That is the Caddy endpoint. Nothing is published on `8000` by the dev compose
project — a bare-metal run, or the `dev` service in `docker-compose.test.yml`, is
the only thing that listens there.)

### Run tests
```bash
docker run -it --rm -v $(pwd):/app -v assistant-test-data:/app/data -w /app -e DATABASE_PATH=/app/data/assistant-test.db assistant pytest assistant/tests/ --timeout=20
```

### Security verification
```bash
docker compose exec assistant python -m assistant.scripts.verify_security
```

## Development

If you do have Python installed locally, you can install the package for
development. The project is installed **from the repo root** — there is no
`assistant/pyproject.toml`:

```bash
pip install -e ".[dev]"
pytest assistant/tests/ -v
ruff check assistant/
```

The DB lives in the `assistant-data` Docker volume. Use `assistant db backup`
to create a backup and `assistant db restore <backup-file>` to restore it.

## Architecture

See `AGENTS.md` (in this directory) for the cognitive architecture and memory model details.
