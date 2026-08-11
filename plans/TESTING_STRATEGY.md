# Testing Strategy

## Purpose

This document defines a lean, intentional testing strategy for the cognitive assistant. The goal is to prove the features that matter, keep the suite maintainable, and avoid testing code we do not own.

## What we own vs. what we borrow

### We own — test deeply

These are the project's actual logic and guarantees:

- Memory model: frames, slots, associations, episodes, embeddings
- Confidence math and conflict resolution rules
- Task routing and reasoner decision logic
- Fact extraction from conversation and search results
- Correction validation policy
- Multi-user privacy isolation
- Security posture: local-only LLM, local-only search, no telemetry

### We borrow — test only at the boundary

These are external dependencies. We verify our interface to them, not their internals:

- **Ollama**: mock `OllamaClient.chat()` and `embed()`. Do not test real model responses.
- **SearXNG**: mock `SearchBackend.search()`. Do not test real search results.
- **SQLite / sqlite-vec**: test through `MemoryStore`, not raw SQL.
- **FastAPI / httpx / uvicorn**: test route behavior and startup config, not framework internals.
- **Docker**: test image build and container startup, not the Docker engine.

## Testing tiers

### Tier 1 — Core algorithms

Fast, deterministic, no network or LLM calls. These are the most important tests because they prove the brain works.

| Area | Tests | Why |
|------|-------|-----|
| Confidence math | `test_confidence.py` | Confidence updates, max bounds, repeated facts, conflict resolution math |
| Conflict resolution | `test_conflict_resolution.py` | Auto-resolve by confidence/recency, manual override, slot history |
| Memory store | `test_memory_store.py` | CRUD, associations, embeddings, user isolation at the store level |
| Retrieval | `test_retrieval.py` | Similarity search, graph-walk associations, relevance filtering |

**Guideline:** Every public function in `backend/memory/confidence.py` should have at least one unit test.

### Tier 2 — Pipeline logic with mocked boundaries

Test orchestration decisions and parsing with deterministic stubs.

| Area | Tests | Mock |
|------|-------|------|
| Task routing | `test_task_router.py` | `OllamaClient.chat()` returns fixed task type |
| Reasoner | `test_reasoner.py` | Provide `MemoryContext` directly; no LLM |
| Extractor | `test_extractor.py` | `OllamaClient.chat()` returns fixed JSON |
| Correction validation | `test_correction_validation.py` | `SearchBackend.search()` returns fixed results |
| Orchestrator state machine | `test_orchestrator.py` | Stub LLM and search tool |

**Guideline:** A test should fail only if the project's logic changes, not because a mock was re-wired.

### Tier 3 — End-to-end memory flows

Real `MemoryStore` + mocked LLM/search. These prove the user-facing promise.

| Flow | Tests | Proves |
|------|-------|--------|
| Learn from conversation → recall | `test_learning_loop.py`, `test_cognitive_loop.py` | Facts from user messages are stored and later retrieved |
| Search → extract → store → recall | `test_search_learn_recall.py` | Web search facts enter memory; recall does not re-search |
| Contradiction handling | `test_conflict_resolution.py` | Conflicting values create conflicts and resolve according to policy |
| User correction with validation | `test_correction_validation.py` | Corrections are checked against third-party sources when possible |
| Multi-user privacy | `test_user_isolation.py` | User A cannot see User B's private frames/episodes |
| Security | `test_security.py` | Bind addresses, env constraints, no cloud endpoints |

**Guideline:** End-to-end tests assert on database state and observable side effects (search call count, conflict status), not on mocked LLM outputs.

### Tier 4 — Operational smoke tests

Verify the system can be built and started.

| Test | Scope |
|------|-------|
| `test_docker.py` | Image builds; backend and SearXNG containers start; health endpoint responds |
| `test_cli.py` | CLI parses commands and talks to the backend |
| `test_api.py` | FastAPI routes return expected status codes |

## Anti-patterns to avoid

1. **Testing mock wiring.** If a test fails only because the mock was changed, it is testing the test, not the system.
2. **Testing third-party behavior.** Do not assert on real LLM output or real search rankings.
3. **Testing internal call counts as the primary assertion.** Call counts can be useful, but the real assertion should be on state or behavior.
4. **Overly broad integration tests.** One end-to-end test per major user promise is enough.
5. **Duplicating framework tests.** Do not test that FastAPI routes requests or that SQLite stores rows.

## Security-specific testing

Security is a first-class feature. The following must be covered:

- FastAPI binds to `127.0.0.1` only (`BACKEND_HOST`, `docker-compose.yml`, `bin/assistant`).
- `OLLAMA_URL` defaults to `http://127.0.0.1:11434` or `host.docker.internal:11434`.
- `SEARCH_BASE_URL` points to local SearXNG only.
- No cloud LLM endpoints (OpenAI, Anthropic, Google, Tavily, SerpAPI, etc.) are referenced in code.
- No telemetry, analytics, or phone-home calls exist.
- `.env.example` is committed; `.env` is gitignored.

## Target size

Aim for **60–80 focused tests** total. This is a rough target, not a hard limit. The current suite is larger because it includes several orchestrator tests that mostly exercise mock wiring. Those should be refactored into Tier 1 and Tier 2 tests.

## Running the suite

```bash
# Local (fast)
pytest assistant/tests/

# Lint
ruff check .

# Docker smoke tests
docker build -t assistant .
docker compose up -d
pytest assistant/tests/test_docker.py
```

## Decision log

- **Mock LLM responses, not the LLM itself.** Ollama is treated as an external dependency.
- **Mock SearXNG results, not the search engine.** The backend's value is what it does with results, not the results themselves.
- **Test the store, not SQL.** sqlite-vec and SQL details are implementation concerns.
- **Security is tested like a feature.** Bind addresses and endpoint constraints have dedicated tests.
- **Fewer, stronger tests over many brittle ones.** A failing test must indicate a real regression.
