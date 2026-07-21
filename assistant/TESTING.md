# Testing strategy

A living document. The bar for what counts as a good test on this project.

This is a household-scale assistant, not a SaaS. The strategy is proportionate:
solid coverage of business logic, end-to-end tests for the cognitive loop,
and no ceremonial test suite.

## 1. Philosophy

- **Test business logic, not frameworks.** Don't test Pydantic, SQLite, asyncio,
  httpx, or FastAPI. They have their own test suites. Test *your* code's behavior.
- **Mock external boundaries, test everything else for real.** Ollama is mocked
  at the HTTP boundary. SQLite is real (via `tmp_path`). The async runtime is
  real. In-process logic is real.
- **High signal, low bloat.** Every test should answer: "if this fails, what
  specific behavior broke?" If a test only verifies that a mock returned what
  it was configured to return, it has no signal — delete it.
- **Variants must be meaningful.** A parametrize over the same branch with
  cosmetic input variations is not coverage. But distinct branches of logic
  *do* each need at least one test.
- **Integration tests > unit tests for the cognitive loop.** The core value
  proposition is the end-to-end learning loop. One good end-to-end test that
  learns a fact and then recalls it is worth more than 20 unit tests on the
  individual pieces.

## 2. Tooling

- **pytest** — runner. Already configured in `pyproject.toml`.
- **pytest-asyncio** — async support. `asyncio_mode = "auto"` is set; no need
  to decorate individual tests.
- **`unittest.mock`** — `AsyncMock` and `MagicMock` are enough. No
  `pytest-mock`, no `flexmock`.
- **No additional frameworks.** Do not add `hypothesis`, `coverage.py`,
  `mutmut`, `pytest-benchmark`, or similar unless explicitly justified later.
  Every new dependency is a maintenance cost.
- **No coverage measurement.** We test by judgment, not by percentage. A
  coverage metric becomes a number to game, not a quality signal. If a
  module feels undertested, write a meaningful test for it.

## 3. What to test (in scope)

### Business logic — always test

- **Confidence math** (`backend/memory/confidence.py`): bump formula,
  conflict resolution, recency bias, boundary clamping.
- **Conflict auto-resolution**: each branch of the resolution matrix —
  `NEW_WINS`, `EXISTING_WINS`, recency tiebreaker.
- **Memory store CRUD** (`backend/memory/store.py`): create, read, update,
  delete, frame merge, slot history, embedding CRUD.
- **Association graph**: add association, directional query, hop traversal,
  cycle prevention in `_graph_walk`.
- **Episode logging + per-user isolation**: episodes scoped to user, never
  visible across users.
- **Retrieval** (`backend/memory/retrieval.py`): cosine similarity, top-k
  selection, graph walk, relevance decay.
- **Task router** (`backend/pipeline/task_router.py`): each heuristic pattern
  hit at least once, LLM fallback path, heuristic-first priority.
- **Extractor** (`backend/pipeline/extractor.py`): JSON parse success, retry
  on malformed output, frame creation, slot application, conflict integration.
- **LLM client** (`backend/pipeline/llm_client.py`): request shape, error
  handling on non-2xx, response parsing.
- **The full cognitive loop**: chat input → task route → retrieve → LLM →
  respond → async extract → store episode → memory updated. End-to-end,
  with Ollama mocked.

### Boundaries and edge cases

- Empty inputs: no frames, no episodes, no users.
- Confidence boundaries: `0.0`, `0.5`, `0.99`, `1.0`.
- Contradiction resolution across every meaningful confidence pairing.
- Graph walk at hop `0`, `1`, `2`; cycle prevention.
- User isolation: user A's data never visible to user B.
- Malformed extractor output (invalid JSON, missing fields, wrong types).

## 4. What NOT to test (out of scope)

### Never test these

- **Framework behavior.** "Does Pydantic validate this field?", "Does
  `asyncio.create_task` return a task?", "Does `PRAGMA foreign_keys = ON`
  enable foreign keys?" All have upstream tests.
- **Mock verification.** "When I mock `httpx` to return 200, does my function
  return True?" That only tests the mock. If the assertion could be satisfied
  by deleting the function body, the test is worthless.
- **Trivial getters/setters.** A function that returns a hardcoded constant
  doesn't need a test.
- **String pass-through.** A function whose entire job is to forward an
  argument to an HTTP client — unless the pass-through has conditional logic
  that could break.
- **SQLite PRAGMAs.** Testing that SQLite's documented behavior works.
- **Third-party library internals.** Don't test how `pydantic-settings`,
  FastAPI, or `rich` work.

### Avoid

- Excessive `@pytest.mark.parametrize` that exercises one branch with cosmetic
  input variation. Use parametrize only when the logic is the same and only
  the inputs differ.
- Tests that require network. Ollama must always be mocked.
- Tests that share state. Every test gets a fresh DB via `tmp_path`.
- Snapshot tests, golden files, recorded cassettes. Overkill here.
- Performance/load tests. Out of scope for a household assistant.

## 5. Test file structure

Tests live in `/assistant/tests/`, mirroring the source structure under
`/assistant/backend/`. One test file per production module:

| Source module                         | Test file                          |
| ------------------------------------- | ---------------------------------- |
| `backend/memory/confidence.py`        | `tests/test_confidence.py`         |
| `backend/memory/store.py`             | `tests/test_memory_store.py`       |
| `backend/memory/retrieval.py`         | `tests/test_retrieval.py`          |
| `backend/memory/models.py`            | `tests/test_schema.py`             |
| `backend/pipeline/task_router.py`     | `tests/test_task_router.py`        |
| `backend/pipeline/extractor.py`       | `tests/test_extractor.py`          |
| `backend/pipeline/llm_client.py`      | `tests/test_llm_client.py`         |
| `tests/conftest.py`                   | shared fixtures (DB, mocks, etc.)  |

Plus dedicated integration files for cross-module scenarios:

- `tests/test_cognitive_loop.py` — full chat → learn → recall.
- `tests/test_conflict_resolution.py` — contradiction + auto-resolve across
  the confidence matrix.
- `tests/test_user_isolation.py` — multi-user episodic privacy.
- `tests/test_learning_loop.py` — learn a fact, then verify recall on a fresh
  request.

Test names should describe the behavior, not the implementation. A non-author
should be able to read the test name and know what it's checking.

## 6. Test naming convention

`test_<unit>_<scenario>_<expected_outcome>`

Good:

- `test_resolve_conflict_new_wins_when_higher_confidence`
- `test_retrieve_graph_walk_finds_neighbors_via_association`
- `test_user_b_episodes_not_visible_to_user_a`
- `test_extractor_retries_on_malformed_json`
- `test_task_router_uses_heuristic_before_llm`

Bad:

- `test_x`, `test_works`, `test_basic_case`
- `test_confidence_1`, `test_confidence_2` — numbers in names signal
  copy-paste branches that should be a single parametrized test, or distinct
  tests that deserve real names.

## 7. Mocking policy

- **Ollama is always mocked** at the HTTP layer. Patch
  `httpx.AsyncClient.post` (or the LLM client's transport) in tests. No real
  Ollama calls in CI or local tests.
- **SQLite is real.** Tests use `tmp_path` to create a fresh DB per test
  via the existing `fresh_db` fixture. Don't mock the DB.
- **Filesystem is real.** `tmp_path` for any file I/O. Don't mock file ops.
- **Async is real.** `pytest-asyncio` runs the real event loop. Don't mock
  `asyncio`.
- **Time is real** unless a test specifically targets time-dependent logic,
  in which case inject a clock via the source module's parameter — don't
  patch `datetime.now` globally.
- **Mock at the boundary, not deep in the call stack.** The LLM client is the
  boundary for Ollama. If you find yourself patching an internal helper to
  test another internal helper, write an integration test instead.

## 8. Running tests

### In Docker (recommended for consistency)

```bash
docker compose -f docker/docker-compose.dev.yml run --rm test
```

The `test` service will be defined alongside the dev stack. It runs the
containerized environment with all system deps in place — same OS, same
Python version, same library versions. Use this for CI parity.

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

No coverage flags. No parallel runner by default — if a test is slow enough
to need parallelism, the test is probably wrong (likely hitting the network
or doing real I/O when it shouldn't be).

## 9. Adding new tests — checklist

Before adding a test, ask:

1. **Does this test verify behavior the user can observe?** If not, why
   does it exist?
2. **If this test fails, will the error message clearly indicate what
   broke?** A failing test should point at the bug, not at the test
   infrastructure.
3. **Is this a distinct branch of logic, or am I re-testing the same path
   with a different input?**
4. **Am I testing the framework or my code?** Pydantic validation, async
   scheduling, SQLite PRAGMA effects — all out of scope.
5. **Is there a more meaningful integration test I could write instead?**
   If the unit test is exercising glue between two modules, fold it into
   an end-to-end test.

If 1–2 fail: don't add the test.
If 3–4 fail: rewrite or delete.
If 5: write the integration test instead.

When deleting a test, don't just comment it out — remove it. A commented-out
test is a code smell, not a safety net.

## 10. Current state of the test suite

Snapshot taken 2026-07-20 after Phase 6 (containerized CLI). Added 10 tests
for DB backup/restore endpoints, CLI API calls, and Docker CLI service
configuration. Replaced 4 filesystem-based CLI backup/restore tests with
API-based versions.

| File                          | Tests | Covers                                                                                  |
| ----------------------------- | ----- | --------------------------------------------------------------------------------------- |
| `test_confidence.py`          | 8     | Bump formula, bounded confidence, initial confidence, conflict resolution branches.     |
| `test_memory_store.py`        | 27    | Frame/slot/association CRUD, episode logging, slot history, conflict auto-resolution.   |
| `test_retrieval.py`           | 19    | Cosine similarity, frame→text, memory-context formatting, truncation.                  |
| `test_extractor.py`           | 14    | JSON parse, retry on malformed, frame/slot/association creation, conflict integration.  |
| `test_task_router.py`         | 17    | Heuristic patterns (parametrized), LLM fallback paths, heuristic-first priority.       |
| `test_schema.py`              | 2     | Pydantic model validation.                                                              |
| `test_llm_client.py`          | 2     | System-prompt build for functional vs introspective task types.                         |
| `test_api.py`                 | 14    | Health, users, chat, sessions, frames, conflicts, DB backup/restore endpoints.          |
| `test_cli.py`                 | 19    | Chat, memory, users, status, DB backup/restore via API.                                 |
| `test_docker.py`              | 15    | Dockerfile security, compose config, CLI service, shell wrapper.                        |
| `test_cognitive_loop.py`      | 1     | **Integration:** full chat → learn → recall; turns 1 + 2 of a session.                  |
| `test_learning_loop.py`       | 1     | **Integration:** learn a fact, verify recall on a fresh request.                        |
| `test_conflict_resolution.py` | 1     | **Integration:** contradiction → auto-resolve → manual override across the matrix.      |
| `test_user_isolation.py`      | 1     | **Integration:** multi-user episodic privacy.                                           |
| **Total**                     | **141** |                                                                                      |

The four integration files — `test_learning_loop.py`, `test_conflict_resolution.py`,
`test_user_isolation.py`, and `test_cognitive_loop.py` — are the highest-signal
tests in the suite.
