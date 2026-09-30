# Verification: Does a Long List Survive a Transform? (transform_decomposition_2026_09_30)

Status: **IN PROGRESS — this file is completed before `result.md` is written.**

Verification against the pre-registered plan in `plan.md`.

## 1. Isolation

| Check | How established | Result |
|---|---|---|
| No database opened | Imports are stdlib + `httpx` + the app's own `WebSearchTool` (used only to search, never to store). No `MemoryStore`, no `aiosqlite`. | _pending_ |
| No retrieval constructed | No `Retriever` import. | _pending_ |
| No memory written | No store is constructed anywhere in the module. | _pending_ |
| Live DB unchanged | `assistant.db` SHA-256 and mtime recorded before and after. | _pending_ |
| Local inference only | `OLLAMA_URL` recorded. | _pending_ |
| **Search is issued** | This experiment searches, unlike `decision_routing_value`. Every query is appended to `SEARCH_LOG` and written to `result.json`, so what left the machine is auditable rather than inferred. | _pending_ |

## 2. Was the measurement sound before it measured anything?

The corruption metric was unit-checked against five hand-built cases **before** the
first real run, because a metric that miscounts would invalidate every number in the
result.

| Case | Expected | Observed | Verdict |
|---|---|---|---|
| perfect ordering | pass, rate 0.0 | pass, rate 0.0 | **PASS** |
| one item dropped | fail, rate 1/3 | fail, rate 0.333 | **PASS** |
| one invented item | fail, rate 1/3 | fail, rate 0.333 | **PASS** |
| trailing slash added | fail, rate 1/3 | fail, rate 0.333 | **PASS** |
| prose with most items missing | fail | fail, rate 0.333 | **PASS** |

**A bug was found and fixed here, before any data.** The first version counted a
mutated URL *twice* — once as a mutation and once as an invention — so a trailing-slash
change scored 0.667 instead of 0.333. Every "invented" count in an early run would have
been inflated by the number of near-miss mutations, and the direction of the error
would have flattered whichever condition mutated more. This is the same class of
defect as `graph_walk_yield`'s silent constant: a plausible-looking number that is
wrong in a way the output does not reveal.

## 3. Was the manipulation real?

- Condition A (one-shot): the list is sent in a single user message; the model's reply
  is parsed for URLs.
- Condition B (decomposed): one call per item, then one ordering call. The count of
  per-item calls must equal N, and the ordering call must receive N annotated entries.
- `SEARCH_LOG` length must be 0 when `TD_NO_SEARCH=1`, and > 0 otherwise.

Recorded per cell in `result.json`: N, condition, run, items in, items out, and the
output text.

## 4. Hypotheses frozen before data

`plan.md` was committed as `2678c50`, before the harness existed. The bar (corruption
must be **0** to pass) and the sizes (7/20/45) are fixed there and unchanged here.

## 5. Harness bugs, and what they would have cost

Known risks stated before the run, and their outcomes:

1. **Corruption metric double-counting** — found and fixed pre-run (§2).
2. **N and input length confounded** — a plan threat, not a bug; carried into the
   result's limitations rather than resolved.
3. **Module-level import of the app's search tool** — the experiment imports
   `assistant.backend.pipeline.search`, so it inherits whatever backend config is
   active (Brave in this configuration). Recorded because it means the experiment's
   search behaviour is production's, not a stub's.
4. **One run per cell** — plan threat 2. Reported with the result, not hidden.
5. **The `-m` invocation requirement** — loading `experiment.py` by file path breaks
   the dataclass import machinery (a module registered as `None` in `sys.modules`).
   Only affects harness debugging, not the experiment; recorded so the next person
   does not lose time to it.

Bugs found during the run are appended here with their user-visible cost.

## 6. Non-mutation summary

_pending — no database is opened, so there are no table digests. The DB SHA-256 and
mtime check in §1 stands in for them._
