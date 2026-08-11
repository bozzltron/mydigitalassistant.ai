# Cognitive Digital Assistant

Privacy-first cognitive digital assistant that learns and remembers using a
frame/slot memory model. Runs 100% locally via Ollama.

## Quick Start

### Prerequisites
- Docker installed
- Ollama running on the host with these models pulled:
  ```bash
  ollama pull qwen2.5:7b
  ollama pull qwen2.5:3b
  ollama pull nomic-embed-text
  ```

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
curl http://127.0.0.1:8000/health
```

### Run tests
```bash
docker run -it --rm -v $(pwd):/app -w /app assistant pytest assistant/tests/
```

### Security verification
```bash
docker compose exec assistant python scripts/verify_security.py
```

## Development

If you do have Python installed locally, you can install the package for
development:

```bash
pip install -e "./assistant[dev]"
pytest assistant/tests/ -v
ruff check assistant/
```

The DB lives in the `assistant-data` Docker volume. Use `assistant db backup`
to create a backup and `assistant db restore <backup-file>` to restore it.

## Architecture

See `AGENTS.md` (in this directory) for the cognitive architecture and memory model details.
