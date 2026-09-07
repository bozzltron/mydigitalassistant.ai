# Testing strategy

A living document. This is a household assistant, not SaaS. The strategy is proportionate: solid business-logic coverage, end-to-end cognitive-loop tests, no ceremonial suite.

## 1. Philosophy
- Test business logic, not frameworks. Don't test Pydantic, SQLite, httpx, FastAPI — they have their own test suites. Test your code's behavior.
- Mock external boundaries, test everything else for real. Ollama is mocked at the HTTP boundary. SQLite is real (via `tmp_path`). In-process logic is real.
- High signal, low bloat. Every test must answer: "If this fails, what specific behavior broke?" If a test only verifies a mock returned its config, delete it.
- Meaningful variants only. A parametrize over the same branch with cosmetic input changes has no signal. Distinct logic branches each need at least one test.
- Integration tests > unit tests for the cognitive loop. The core value is the end-to-end learning loop. One good test that learns a fact and recalls it outweighs 20 unit tests on individual pieces.

## 2. Tooling
- **pytest** — runner, configured in `pyproject.toml`.
- **pytest-asyncio** — async support; `asyncio_mode = "auto"` is set; no need to decorate individual tests.
- **`unittest.mock`** — `AsyncMock` and `MagicMock` are enough. No `pytest-mock`, no `flexmock`.
- **No additional frameworks.** Do not add `hypothesis`, `coverage.py`, `mutmut`, `pytest-benchmark`, or similar without explicit justification. Every new dependency is a maintenance cost.
- **No coverage measurement.** We test by judgment, not percentage. A coverage metric becomes a number to game, not a quality signal. If a module feels undertested, write a meaningful test for it.

## 2. What to test (in scope)
### Business logic — always test
- Confidence math (`backend/memory/confidence.py`): bump formula, conflict resolution, recency bias, boundary clamping.
- Conflict auto-resolution: each resolution matrix branch (`NEW_WINS`, `EXISTING_WINS`, recency tiebreaker).
- Memory store CRUD (`backend/memory/store.py`): create, read, update, delete, frame merge, slot history, embedding CRUD.
- Association graph: add association, directional query, hop traversal, cycle prevention in `_graph_walk`.
- Episode logging + per-user isolation: episodes scoped to user, never visible across users.
- Retrieval (`backend/memory/retrieval.py`): cosine similarity, top-k selection, graph walk, relevance decay.
- Task router (`backend/pipeline/task_router.py`): each heuristic pattern hit at least once, LLM fallback path, heuristic-first priority.
- Extractor (`backend/pipeline/extractor.py`): JSON parse success, retry on malformed output, frame creation, slot application, conflict integration.
- LLM client (`backend/pipeline/llm_client.py`): request shape, error handling on non-2xx, response parsing.
- Full cognitive loop: chat input → task route → retrieve → LLM → respond → async extract → store episode → memory updated. End-to-end, with Ollama mocked.
- Scheduled tasks as memory: create task → set `next_run` in the past → trigger scheduler → assert the task prompt runs through `orchestrator.chat()`, the output episode exists, a `daily_run_YYYY_MM_DD` event frame is created, and associations link task → run → episode.

### Boundaries and edge cases
- Empty inputs: no frames, no episodes, no users.
- Confidence boundaries: `0.0`, `0.5`, `0.99`, `1.0`.
- Contradiction resolution across every meaningful confidence pairing.
- Graph walk at hop `0`, `1`, `2`; cycle prevention.
- User isolation: user A's data never visible to user B.
- Malformed extractor output (invalid JSON, missing fields, wrong types).

### Out of scope
- Framework behavior. "Does Pydantic validate this field?" "Does `asyncio.create_task` return a task?" — upstream tests.
- Mock verification. "When I mock `httpx` to return 200, does my function return True?" — that only tests the mock.
- Trivial getters/setters. A function returning a hardcoded constant doesn't need a test.
- String pass-through. A function forwarding an argument to an HTTP client — unless it has conditional logic that could break.
- SQLite PRAGMAs. Testing that SQLite's documented behavior works.
- Third-party library internals. Don't test how `pydantic-settings`, FastAPI, or `rich` work.

### Avoid
- Excessive `@pytest.mark.parametrize` exercising one branch with cosmetic input variation. Use parametrize only when the logic is the same and only the inputs differ.
- Tests that require network. Ollama must always be mocked.
- Tests that share state. Every test gets a fresh DB via `tmp_path`.
- Snapshot tests, golden files, recorded cassettes. Overkill here.
- Performance/load tests. Out of scope for a household assistant.

## 3. Test file structure
Tests live in `/assistant/tests/`, mirroring source under `/assistant/backend/`. One test file per production module:

| Source module | Test file |
|---|---|
| `backend/memory/confidence.py` | `tests/test_confidence.py` |
| `backend/memory/store.py` | `tests/test_memory_store.py` |
| `backend/memory/retrieval.py` | `tests/test_retrieval.py` |
| `backend/memory/models.py` | `tests/test_schema.py` |
| `backend/pipeline/task_router.py` | `tests/test_task_router.py` |
| `backend/pipeline/extractor.py` | `tests/test_extractor.py` |
| `backend/pipeline/llm_client.py` | `tests/test_llm_client.py` |
| `tests/conftest.py` | shared fixtures (DB, mocks, etc.) |

Plus dedicated integration files for cross-module scenarios:
- `tests/test_cognitive_loop.py` — full chat → learn → recall.
- `tests/test_conflict_resolution.py` — contradiction + auto-resolve across the confidence matrix.
- `tests/test_user_isolation.py` — multi-user episodic privacy.
- `tests/test_learning_loop.py` — learn a fact, then verify recall on a fresh request.

Test names should describe the behavior, not the implementation. A non-author should read the test name and know what it's checking.

Good:
- `test_resolve_conflict_new_wins_when_higher_confidence`
- `test_retrieve_graph_walk_finds_neighbors_via_association`
- `test_user_b_episodes_not_visible_to_user_a`
- `test_extractor_retries_on_malformed_json`
- `test_task_router_uses_heuristic_before_llm`

Bad:
- `test_x`, `test_works`, `test_basic_case`
- `test_confidence_1`, `test_confidence_2` — numbers in names signal copy-paste branches that should be a single parametrized test, or distinct tests that deserve real names.

## 5. Mocking policy
- **Ollama is always mocked** at the HTTP layer. Patch `httpx.AsyncClient.post` (or the LLM client's transport) in tests. No real Ollama calls in CI or local tests.
- **SQLite is real.** Tests use `tmp_path` to create a fresh DB per test via the `fresh_db` fixture. Don't mock the DB.
- **Filesystem is real.** `tmp_path` for any file I/O. Don't mock file ops.
- **Async is real.** `pytest-asyncio` runs the real event loop. Don't mock `asyncio`.
- **Time is real** unless a test specifically targets time-dependent logic, in which case inject a clock via the source module's parameter — don't patch `datetime.now` globally.
- **Mock at the boundary, not deep in the call stack.** The LLM client is the boundary for Ollama. If you find yourself patching an internal helper to test another internal helper, write an integration test instead.

## 6. Running tests
### In Docker (recommended for consistency)
```bash
docker compose -f docker-compose.test.yml run --rm test
```

### Locally
```bash
pip install -e ".[dev]"
pytest tests/ -v
```

A single file:
```bash
pytest tests/test_confidence.py -v
```

A single test:
```bash
pytest tests/test_confidence.py::test_resolve_conflict_new_wins -v
```

No coverage flags. No parallel runner by default — if a test is slow enough to need parallelism, it's probably wrong (hitting the network or doing real I/O when it shouldn't be).

## 7. Adding new tests — checklist
Before adding a test, ask:
1. Does this test verify behavior the user can observe? If not, why does it exist?
2. If this test fails, will the error message clearly indicate what broke? A failing test should point at the bug, not the test infrastructure.
3. Is this a distinct branch of logic, or am I re-testing the same path with a different input?
4. Am I testing the framework or my code? Pydantic validation, async scheduling, SQLite PRAGMA effects — all out of scope.
5. Am I writing a more meaningful integration test instead? If the unit test is exercising glue between two modules, fold it into an end-to-end test.

If 1–2 fail: don't add the test.
If 3–4 fail: rewrite or delete.
If 5: write the integration test instead.

When deleting a test, don't just comment it out — remove it. A commented-out test is a code smell, not a safety net.

## 7. Current state of the test suite
Snapshot: 2026-09-05. Full suite runs in both plain SQLite and SQLCipher-encrypted modes.

| File | Tests | Covers |
|---|---|---|
| `test_memory_store.py` | 40 | Frame/slot/association CRUD, episode logging, slot history, conflict auto-resolution. |
| `test_extractor.py` | 39 | JSON parse, retry on malformed, frame/slot/association creation, conflict integration. |
| `test_api.py` | 34 | Health, users, chat, sessions, frames, conflicts, DB backup/restore endpoints. |
| `test_reasoner.py` | 28 | Intent classification, plan formatting, self-correction branches. |
| `test_task_router.py` | 23 | Heuristic patterns (parametrized), LLM fallback paths, heuristic-first priority. |
| `test_working_memory.py` | 21 | LRU cache, boost map, touch/lookup/eviction behavior. |
| `test_retrieval.py` | 21 | Cosine similarity, frame→text, memory-context formatting, truncation. |
| `test_confidence.py` | 21 | Bump formula, bounded confidence, initial confidence, conflict resolution branches. |
| `test_cli.py` | 20 | Chat, memory, users, status, DB backup/restore via API. |
| `test_belief_revision.py` | 20 | Standalone AGM operators (expand/contract/revise). |
| `test_search_tool.py` | 19 | SearXNG backend, relevance filter, backend selection, Brave gating. |
| `test_llm_client.py` | 19 | Chat/embed requests, response parsing, system prompts, error handling. |
| `test_security.py` | 18 | Security verification script, local-only bindings, Brave endpoint gating. |
| `test_docker.py` | 18 | Dockerfile security, compose config, CLI service, shell wrapper. |
| `test_orchestrator.py` | 17 | Full cognitive loop, search injection, correction handling, scheduled-task chat flow. |
| `test_gc.py` | 16 | Decay math, boundary conditions, essential-fact exemption, dry-run, live GC. |
| `test_brain_portable.py` | 16 | Encrypted portable brain export/import. |
| `test_identity_name.py` | 14 | Identity frame/slot behavior, self-description. |
| `test_tools.py` | 12 | `fetch_url` tool, robots.txt, extraction from fetched documents. |
| `test_daily_schedule.py` | 12 | Clock helpers, store semantics, create/list/delete/pause/resume chat flow. |
| `test_phase9_memory_strength.py` | 11 | Memory strength reinforcement and slot priority. |
| `test_phase9_consolidation.py` | 10 | Frame merge planning and execution. |
| `test_episode_recall.py` | 8 | Semantic episode recall across sessions. |
| `test_backup_restore.py` | 8 | Full-DB JSON backup/restore. |
| `test_review_fixes.py` | 7 | Regression fixes for frame tombstones, aliases, retrieval. |
| `test_phase9_scheduler_consolidation.py` | 6 | Scheduler-driven consolidation integration. |
| `test_brain_search.py` | 6 | Brain Observatory memory search endpoint. |
| `test_self_context.py` | 3 | Agent self-description in system prompt. |
| `test_search_learn_recall.py` | 3 | Search → learn → recall integration. |
| `test_introspective_recall.py` | 3 | Introspective query handling. |
| `test_schema.py` | 2 | Pydantic model validation (low signal; candidates for removal). |
| `test_conflict_resolution.py` | 2 | **Integration:** contradiction → auto-resolve across confidence pairings. |
| `test_user_isolation.py` | 1 | **Integration:** multi-user episodic privacy. |
| `test_learning_loop.py` | 1 | **Integration:** learn a fact, verify recall on a fresh request. |
| `test_learning_exam.py` | 1 | **Integration:** broader learn-and-recall scenario. |
| `test_cognitive_loop.py` | 1 | **Integration:** full chat → learn → recall; turns 1 + 2 of a session. |
| **Total** | **501** | |

The integration files — `test_learning_loop.py`, `test_conflict_resolution.py`, `test_user_isolation.py`, `test_cognitive_loop.py`, `test_learning_exam.py`, and `test_search_learn_recall.py` — are the highest-signal tests in the suite.

### Known gaps to close
- **Scheduled-task execution:** No integration test actually fires a due task through the scheduler and asserts the output episode + daily-run frame + associations are created.
- **Schema-only tests:** `test_schema.py` validates Pydantic models, which is framework behavior per section 4. Consider removing or replacing with behavior that exercises the models through real store/pipeline code.
- **Belief-revision operators:** `test_belief_revision.py` covers `expand`/`contract`, which are standalone operators not used in production. Keep only if the operators are documented as public utilities; otherwise move to tests that exercise `revise()`.
