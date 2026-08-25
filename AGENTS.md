# AGENTS.md

## Project overview
A privacy-first cognitive digital assistant that remembers, learns, and error-corrects.
Learning happens in a memory layer (frames, slots, associations, episodic memory) —
LLM weights stay frozen. Web search via local SearXNG for retrieval-only information gathering.
Runs 100% locally via Ollama. Safe for household use incl. kids.

## Repository layout
- `/assistant/` — the cognitive assistant. See `/assistant/AGENTS.md` for the cognitive architecture and memory model.
- `/plans/` — historical planning documents (archived for reference)

## Security constraints (HARD RULES — violate these and the project's purpose is broken)
- All LLM inference via local Ollama on 127.0.0.1:11434.
- Web search only via local SearXNG instance (configurable in `.env`).
- No cloud LLM APIs (OpenAI, Anthropic, Google, etc.). All inference via local Ollama.
- No telemetry, analytics, or phone-home code of any kind.
- FastAPI must bind to 127.0.0.1 only — never 0.0.0.0 or a public interface.
- SearXNG must bind to 127.0.0.1 only — never expose to the network.
- Secrets/config via `.env` (gitignored). Commit `.env.example` only.
- Do not add dependencies that make external network calls without explicit review.
- Docker: backend binds inside the container with no published ports; Caddy reverse
  proxy on `127.0.0.1:8443` is the only published surface. All services on internal
  Docker network (`appnet`).

## Build and test commands
- Install: `docker build -t assistant .`
- Run backend: `docker compose up` (FastAPI server via Caddy on 127.0.0.1:8443)
- Run CLI: `./assistant/bin/assistant chat`
- Run tests: `docker run -it --rm -v $(pwd):/app -w /app assistant pytest assistant/tests/`
- Run full suite (plain + encrypted): `./run_ci.sh`
- Run single test mode: `docker compose -f docker-compose.test.yml run --rm test-plain`
- Lint: `docker run -it --rm -v $(pwd):/app -w /app assistant ruff check .`

## Code style
- Python 3.11+. Type hints required on all public functions.
- `ruff` for lint/format. Line length 100.
- Pydantic v2 for all API schemas and config.
- Async for all LLM/Ollama calls and DB writes in the hot path.

## Testing instructions
- Every memory-system module must have unit tests (frames, slots, associations, conflicts, retrieval).
- End-to-end tests must cover: learn-a-fact-then-recall, and contradiction-then-auto-resolve.
- Run `pytest assistant/tests/` before considering any task done.

## Working with the memory system
- The SQLite DB is the agent's brain. Never wipe it in tests without explicit backup.
- Use `assistant db backup` / `assistant db restore` for safety.
- Confidence math is in `assistant/backend/memory/confidence.py` — change it there only.

## Don't
- Don't make a second blocking LLM call for task routing when heuristics suffice.
- Don't expose backend directly — always route through Caddy HTTPS proxy.
- Don't add cloud LLM APIs (OpenAI, Anthropic, Google, etc.) — all inference via local Ollama.
- Don't skip SearXNG localhost binding when using search.

## Commit messages
- Conventional commits: `feat:`, `fix:`, `test:`, `docs:`, `refactor:`.
- Reference the phase/task in the body, e.g. `Phase 1.2: slot confidence logic`.
