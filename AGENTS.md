# AGENTS.md

## Project overview
A privacy-first cognitive digital assistant that remembers, learns, and error-corrects.
Learning happens in a memory layer (frames, slots, associations, episodic memory) —
LLM weights stay frozen. Runs 100% locally via Ollama. Safe for household use incl. kids.

## Repository layout
- `/assistant/` — the cognitive assistant (new, active development). See `/assistant/AGENTS.md`.
- `/archive/`  — previous Open WebUI / Docker orchestration setup. Reference only. Do not modify.

## Security constraints (HARD RULES — violate these and the project's purpose is broken)
- No outbound network calls except to Ollama on 127.0.0.1:11434.
- No cloud LLM APIs (OpenAI, Anthropic, Google, etc.). All inference via local Ollama.
- No telemetry, analytics, or phone-home code of any kind.
- FastAPI must bind to 127.0.0.1 only — never 0.0.0.0 or a public interface.
- Secrets/config via `.env` (gitignored). Commit `.env.example` only.
- Do not add dependencies that make external network calls without explicit review.

## Build and test commands
- Install: `pip install -e ./assistant`
- Run backend: `uvicorn assistant.backend.main:app --host 127.0.0.1 --port 8000`
- Run CLI: `assistant chat`
- Run tests: `pytest assistant/tests/`
- Lint: `ruff check assistant/`

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

## Commit messages
- Conventional commits: `feat:`, `fix:`, `test:`, `docs:`, `refactor:`.
- Reference the phase/task in the body, e.g. `Phase 1.2: slot confidence logic`.
