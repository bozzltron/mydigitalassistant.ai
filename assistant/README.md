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
```bash
cd assistant
docker compose up -d
```

### Use the CLI
The CLI runs in a Docker container — no host Python needed.

**Option A: shell wrapper (recommended)**
```bash
./bin/assistant status
./bin/assistant users add alice
./bin/assistant chat
./bin/assistant memory list
./bin/assistant db backup
```

**Option B: docker compose directly**
```bash
docker compose --profile cli run --rm cli status
docker compose --profile cli run --rm cli chat
```

### Verify
```bash
curl http://127.0.0.1:8000/health
```

### Run tests
```bash
docker compose -f docker-compose.dev.yml run --rm test
```

### Security verification
```bash
docker compose run --rm cli security
```
(Or, if you have the project installed locally: `assistant-verify-security`)

## Development

If you do have Python installed locally, you can install the package for
development:

```bash
pip install -e ".[dev]"
pytest tests/ -v
ruff check .
```

The DB lives in the `assistant-data` Docker volume. Use `assistant db backup`
to create a backup and `assistant db restore <backup-file>` to restore it.
