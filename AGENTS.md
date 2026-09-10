# AGENTS.md

## Project overview
A privacy-first cognitive digital assistant that remembers, learns, and error-corrects.
Learning happens in a memory layer (frames, slots, associations, episodic memory) —
LLM weights stay frozen. Web search defaults to a local SearXNG instance for
retrieval-only information gathering; an optional Brave Search API backend is
available for higher-quality results. Runs locally via Ollama. Safe for household
use incl. kids.

## Repository layout
- `/assistant/` — the cognitive assistant. See `/assistant/AGENTS.md` for the cognitive architecture and memory model.
- `/plans/` — historical planning documents (archived for reference)

## Security constraints (HARD RULES — violate these and the project's purpose is broken)
- All LLM inference via local Ollama on 127.0.0.1:11434.
- Web search defaults to a local SearXNG instance (configurable in `.env`).
- Optional Brave Search API is permitted as the only non-local search backend,
  and only with explicit opt-in (`BRAVE_ENABLED=true` + `BRAVE_API_KEY`).
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

## Pre-Commit Flow (Required)
**Before every commit, run both lint and tests in Docker:**

```bash
# Backend
docker run -it --rm -v $(pwd):/app -w /app assistant ruff check .
docker run -it --rm -v $(pwd):/app -w /app assistant pytest assistant/tests/

# Frontend
docker run -it --rm -v $(pwd)/frontend:/app -w /app assistant npm run lint
docker run -it --rm -v $(pwd)/frontend:/app -w /app assistant npm run test
```

## Code style
- Python 3.11+. Type hints required on all public functions.
- `ruff` for lint/format. Line length 100.
- Pydantic v2 for all API schemas and config.
- Async for all LLM/Ollama calls and DB writes in the hot path.

## Testing instructions
- Every memory-system module must have unit tests (frames, slots, associations, conflicts, retrieval).
- End-to-end tests must cover: learn-a-fact-then-recall, and contradiction-then-auto-resolve.
- Critical path tests (voice, run_now, correction, search extraction) must pass before any merge.
- Run `pytest assistant/tests/test_daily_schedule.py assistant/tests/test_review_fixes.py` before considering any change done.
- The full suite (`pytest assistant/tests/`) must pass before merge — excluding known environment issues (test_api.py needs python-multipart, test_identity_name.py shares this dependency).

## Working with the memory system
- The SQLite DB is the agent's brain. Never wipe it in tests without explicit backup.
- Use `assistant db backup` / `assistant db restore` for safety.
- Confidence math is in `assistant/backend/memory/confidence.py` — change it there only.

## Web search
The search tool is built on a swappable `SearchBackend` ABC in `assistant/backend/pipeline/search.py`. The default implementation is `SearXNGBackend`. An optional `BraveBackend` is available for higher-quality results.

### SearXNG (default)
Result quality is guarded in three layers:
1. `sanitize_query()` strips conversational filler ("can you look up", "hey", etc.) before the query leaves.
2. `SearXNGBackend` pins safesearch and language, ranks by engine score, and dedups normalized URLs.
3. `filter_relevant()` drops results whose embedding sits below `search_min_relevance` from the query embedding — unrelated links never reach the system prompt. Graceful by design: if the embedder fails, all results pass through.

### Brave Search API (optional, opt-in)
- Activate with `BRAVE_ENABLED=true` plus `BRAVE_API_KEY`.
- Queries are sent to `https://api.search.brave.com`. See Brave's privacy policy
  for what they log.
- Brave results are measurably cleaner than the SearXNG/Bing mix, so the agent
  extracts more aggressively from Brave: larger result budget, lower relevance
  threshold, optional full-page fetch for top results, and higher base source
  reliability.
- When Brave is enabled, the UI shows a "Searched via Brave" indicator and the
  trace panel records the backend, query, and source engines.

**Search settings** (all in `.env`):
- `SEARCH_BASE_URL` — SearXNG URL (default `http://127.0.0.1:8080`)
- `SEARCH_TIMEOUT` — request timeout in seconds (default 30)
- `SEARCH_LANGUAGE` — locale filter (default `en`)
- `SEARCH_SAFESEARCH` — 0=off, 1=moderate, 2=strict (default 1)
- `SEARCH_MIN_RELEVANCE` — cosine similarity threshold below which results are dropped (default 0.30 for SearXNG, 0.20 for Brave)
- `BRAVE_ENABLED` — `true` to enable Brave Search API (default `false`)
- `BRAVE_API_KEY` — Brave Search API key (required when `BRAVE_ENABLED=true`)

**`fetch_url` tool** — direct HTTP fetch with auto-extraction:
- Fetches URL content, strips HTML, respects robots.txt, detects JS-rendered pages.
- After fetching, calls `extract_facts_from_document()` and stores facts in memory with `source_type="web_fetch"`.
- Extraction errors are logged and degrade gracefully — fetched content is always returned.
- Tool user-agent: `Mozilla/5.0 (compatible; AssistantBot/1.0)`.

## Do / Don't
- **Do** let the model handle ambiguous or unparseable input — when the utility model fails, pass the user's message to the chat model rather than generating a scripted fallback. The model's own reasoning determines the response.
- **Do** lean on the model everywhere design allows a choice between scripted logic and model reasoning.
- Don't make a second blocking LLM call for task routing when heuristics suffice.
- Don't expose backend directly — always route through Caddy HTTPS proxy.
- Don't add cloud LLM APIs (OpenAI, Anthropic, Google, etc.) — all inference via local Ollama.
- Don't skip SearXNG localhost binding when using search.
- Don't add hardcoded response text keyed on what the user might have typed — prefer model reasoning instead.

## Design principles

### Clean ship
Dead code is a liability. Unused imports, dead helper functions, and commented-out
snippets make the codebase harder to reason about and increase the risk of breaking
something that still matters. Before shipping a change:
- Use `ruff check --select=F401,F811` (or `ruff check` in general) to surface unused
  imports and variables.
- Before removing any function or class, verify it has zero callers across the entire
  codebase — including tests. If in doubt, add a test proving it can be removed rather
  than deleting blind.
- A periodic "dead code audit" is fine; a blanket "delete unused code" without tooling
  verification is not. The goal is a codebase where nothing exists without purpose.

### Speed-first UI responsiveness
Our system should respond quickly to user inputs within 500ms for basic interactions,
and keep response durations under 2 seconds for complete LLM processing cycles.
Performance timing logs must be maintained for analysis of interaction speed trends.
Visual feedback should be immediately provided during processing.
UI animations should enhance rather than distract from the user experience.

### Visual feedback and transitions
All UI elements should use appropriate CSS transitions with durations between 150-300ms
to provide consistent feedback. Complex animations must only be used when they add clear
value to the interaction flow. For text inputs that auto-expand, use smooth height transitions.

### Stability: no regressions while adding features
Every non-trivial change should be accompanied by a regression test — a test that would
have caught the bug before the fix ships. This builds the suite in the direction of
real bugs rather than abstract coverage.
Critical paths that need regression tests (in priority order):
1. **Voice recording flow**: silence detection → stopRecording → transcription → sendMessage.
2. **run_now**: scheduled task found by name → executed → last_run updated → once disabled.
3. **Correction pipeline**: parse → validate → apply → response.
4. **Search extraction + merge**: snippet extract → deduplication → document extract → merge.

Run `pytest assistant/tests/test_daily_schedule.py assistant/tests/test_review_fixes.py`
before considering any change done. The full suite must pass before merge.

## Commit messages
- Conventional commits: `feat:`, `fix:`, `test:`, `docs:`, `refactor:`.
- Reference the phase/task in the body, e.g. `Phase 1.2: slot confidence logic`.
