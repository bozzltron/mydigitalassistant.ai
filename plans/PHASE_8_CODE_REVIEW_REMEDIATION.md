# Phase 8 — Code Review Remediation

Post Phase 7 full-codebase review. Goal: fix real bugs, remove duplication and dead
code, surface hardcoded values into config, align docs with reality. No new features,
no new abstractions. Every task keeps the existing test suite green.

## Verified findings

### Real bugs
| ID | Location | Problem |
|----|----------|---------|
| B1 | `backend/main.py:718` | `POST /correction` with `episode_id` calls `store.db.execute_fetchall(...)` — `MemoryStore` has no `.db` attribute → guaranteed `AttributeError`. |
| B2 | `scheduler/runner.py:46-57` | `_new_orchestrator()` builds `OllamaClient(base_url=...)` with class defaults: retired models `qwen2.5:7b/3b` and 120s timeout instead of `settings.CHAT_MODEL`/`UTILITY_MODEL` and `OLLAMA_TIMEOUT=600`. Also constructs duplicate httpx clients per due batch. |
| B2b | `backend/main.py:141` | `store._orchestrator = orchestrator` assigned, never read anywhere. Dead wiring. |
| B3 | `memory/store.py:1806` | Slot decay branch filters on `slots.last_accessed_at < ?`, but nothing ever writes that column → branch is a silent no-op. Correct decay math lives in `memory/gc.py::run_gc` (uses maintained `last_strengthened_at`). Two GC implementations exist; only `store.gc()` caller is `runner.py:114`. |

### Test failures (3 failed / 406 passed / 5 skipped at review time)
- `test_static_files_served`: Starlette now serves `.js` as `text/javascript`; test
  asserts `application/javascript`. Fix assertion to accept both.
- `test_next_daily_run_before_and_after_tick`, `test_format_next_run_contains_weekday_and_time`:
  tests compute expected hours in UTC but `_tz()` (schedule.py:18-28) resolves
  `settings.daily_tasks_tz` → `TZ` env → host-local zone. On a non-UTC host they fail.
  Mechanism is host-timezone dependence, NOT `.env` leakage (pydantic-settings does not
  inject into `os.environ`). Fix: pin `settings.daily_tasks_tz = "UTC"` inside the tests.

### Hardcoded values in logic
- `task_router.py:69` — eval-specific regex `\bfollow artificial intelligence in the news\b`
  baked into production heuristics. Delete.
- `pipeline/search.py:51` — `timeout=30.0` literal while `settings.search_timeout`
  exists and is referenced nowhere else (dead config). Wire it through.
- `memory/retrieval.py:172` — inline `min_distance=0.7`. Surface as
  `settings.retrieval_min_distance`.
- `pipeline/whisper.py:34` — `/app/.cache/whisper` download root and `language="en"`.
  Surface as settings.
- `main.py:206` — `"Cognitive Assistant"` fallback name. Surface as setting.

### Dead / deprecated code
- `store.apply_correction_feedback` (store.py:1077) — legacy correction parser, wired
  only at `main.py:668` (`POST /feedback kind=correction`). Superseded by the LLM
  pipeline on `POST /correction`. Remove method + endpoint branch.
- `store.get_episodes_for_frame` (store.py:840) — zero production callers; only its own
  test uses it. Remove method + test.

### Docs/config drift
- `SEARCH_ENABLED` documented in `AGENTS.md:18-19`, `SECURITY.md:15`,
  `config.py:23` comment — field removed from Settings long ago. Search is always-on.
- `llm_client.py:72`, `AGENTS.md:93` — utility model described as doing "cron
  generation"; cron generation was removed in Phase 7.
- `docker-compose.yml:27` — `--host ${BACKEND_HOST}` is interpolated from the HOST
  `.env` at compose-parse time (user's `.env` sets `0.0.0.0`). Container env
  `BACKEND_HOST=127.0.0.1` never takes effect. Note: in-container loopback bind would
  break the Caddy → `assistant-backend:8000` proxy over `appnet`; the security rule
  (loopback-only) applies to host-network deployments, while the compose deployment is
  protected by unpublished ports. Make this explicit rather than "fixing" the bind.

## Tasks

- [x]T1 — B1: replace raw SQL at main.py:716-723 with existing
      `store.get_episodes_for_session()`; add API regression test posting `/correction`
      with `episode_id`.
- [x]T2 — B2/B2b: `start_scheduler(store, orchestrator)` receives the lifespan
      orchestrator; delete `_new_orchestrator` and `store._orchestrator`.
- [x]T3 — GC consolidation: single entry point `memory/gc.py::run_gc(store)` owning
      slot decay (existing math) AND frame soft-deletion (moved from `store.gc`);
      delete `store.gc`; update runner + tests; extend `GcReport` with
      `frames_soft_deleted`. Resolves B3 by deleting the no-op branch outright.
- [x]T4 — Config surfacing: wire `search_timeout` into SearXNGBackend;
      `retrieval_min_distance`, whisper download-root/language, fallback assistant
      name → Settings; delete AI-news regex. Domain-prestige list stays a named module
      constant (editorial heuristic, not deployment-specific).
- [x]T5 — Remove deprecated `apply_correction_feedback` + its `/feedback` branch;
      remove `get_episodes_for_frame` + its test. `kind=correction` on `/feedback`
      still records the feedback row for audit.
- [x]T6 — Fix the 3 failing tests; pin `daily_tasks_tz="UTC"` in clock tests.
- [x]T7 — Docs/compose alignment: rewrite SEARCH_ENABLED references, drop cron mention,
      runtime-expand `--host $${BACKEND_HOST:-127.0.0.1}`… wait, see note: use container
      env explicitly with explanatory comment + SECURITY.md nuance. Update
      `llm_client.py:72` docstring.
- [x]T8 — Full `pytest assistant/tests/` green + `ruff check .` clean.

## Option A — model residency (approved)

Ollama runs on the host (`host.docker.internal:11434`), so residency is configured on
the host daemon plus the app's per-request keep-alive — not in compose:

- Host Ollama env: `OLLAMA_KEEP_ALIVE=-1`, `OLLAMA_MAX_LOADED_MODELS=2` (both chat +
  utility stay warm; embedding model is tiny/fast anyway).
- App: `settings.ollama_keep_alive` default `"30m"` → `"-1"` (per-request override,
  wins over server default). Update `.env.example`.

Result: alternating 27B↔4B turns stop paying cold-load penalties (~5-20s → ~0).

## Explicitly deferred / rejected

- Option B (single model for everything) and Option C (reorder extraction) — gated on
  eval-harness results; revisit after Option A lands and latency is re-measured.
- Merging router + extraction into one utility call — Phase 9 candidate; touches prompts
  and many tests; do not bundle here.
- Dropping `slots.last_accessed_at` schema column — migration churn for zero benefit;
  column stays, unused.
- Extractor domain-prestige list — stays as named constant with docstring.
