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

## 7. What the suite covers

Full suite runs in both plain SQLite and SQLCipher-encrypted modes. It is
described **by area, not by file or count** — file lists and per-file counts go
stale the moment the suite changes, and a number in a doc is a claim nobody
re-measures.

- **Memory store** — frame/slot/association CRUD, episode logging, slot history,
  conflict auto-resolution, merges and aliases, ownership isolation, blank-value
  refusal, slot-write atomicity.
- **Extraction & correction** — JSON parse and retry, frame/slot/association
  creation, degenerate-record dropping, identity routing, correction subject
  routing, user-content registration.
- **Confidence & belief revision** — bump/lower formulas, the conflict ladder and
  its recorded provenance, AGM expand/contract/revise.
- **Retrieval** — cosine similarity, frame→text, memory-context formatting and
  truncation, graph walk, identity-frame boosts, episode recall.
- **Pipeline** — task router, reasoner, orchestrator (full loop, search
  injection, correction, scheduled tasks), prompt assembly, streaming events,
  turn timings.
- **Search** — SearXNG backend, relevance filter, backend selection, Brave gating,
  `fetch_url`, URL safety.
- **Files** — extractors, upload/agent-write parity, sandbox safety, reserved
  keys, read resolution.
- **Scheduler** — daily clock, run-now, consolidation, summarization, alerts.
- **API & CLI** — endpoints, sessions, backup/restore, encrypted-DB paths.
- **Security & ops** — local-only bindings, Dockerfile/compose config, subprocess
  safety.
- **Meta** — `test_suite_hygiene.py` (every test asserts something; no fixture
  named like a test), `test_plan_citations.py` (docs cite files that exist).

The integration tests — learn-then-recall, contradiction-then-auto-resolve,
multi-user privacy, the full cognitive loop — are the highest-signal tests in the
suite; they exercise whole paths rather than units.

### Suite hygiene

A test that cannot fail is not a test. `test_suite_hygiene.py` fails the suite if
any `test_*` function contains no assertion construct (assert / `pytest.raises` /
mock assertion), or if a fixture is named like a test. A test that genuinely
cannot assert needs an explicit `# no-assert-ok: <reason>` marker — deliberately
noisy so it is not used casually.

### Known gaps to close
- **Scheduled-task execution:** No integration test fires a due task through the
  scheduler and asserts the output episode + daily-run frame + associations.
- **Schema-only tests:** `test_schema.py` validates Pydantic models, which is
  framework behavior per section 4. Consider removing or replacing with behavior
  that exercises the models through real store/pipeline code.
- **Belief-revision operators:** `test_belief_revision.py` covers `expand`/
  `contract`, standalone operators not used in production. Keep only if they are
  documented as public utilities; otherwise test `revise()` instead.
