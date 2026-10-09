# AGENTS.md

## Project overview
A privacy-first cognitive digital assistant that remembers, learns, and error-corrects.
Learning happens in a memory layer (frames, slots, associations, episodic memory) —
LLM weights stay frozen. Web search defaults to a local SearXNG instance for
retrieval-only information gathering; an optional Brave Search API backend is
available for higher-quality results. Runs locally via Ollama. Safe for household
use incl. kids.

## Project goal
The assistant's job is to construct an accurate model of the user's world by
triangulating **the user and the internet as sources**, and to use that model to
give the most useful possible help — without the user having to repeat themselves.
Every interaction is a learning opportunity; every stored belief is revisable.

The user and the internet are the two *sources*. Memory is not a third peer source:
it is the accumulating model those sources are reconciled into, and the thing that
gets corrected when it is wrong.

Triangulation is not averaging. It is knowing which source is authoritative for a
given claim, and when they disagree, recording both with their provenance rather
than silently discarding one:

- **The user is authoritative about their own world** — their intent, preferences,
  possessions, plans, and any content they supply.
- **The internet is authoritative about the external world** — facts the user
  cannot know first-hand.
- **Neither is authoritative about the other's domain.** A user can be wrong about
  the external world; a search result cannot tell you what the user meant. A
  conflicting claim is stored with provenance and resolved by the confidence ladder
  in `assistant/backend/memory/confidence.py`, never overwritten by default.
- **When the request is to transform user-supplied content** — sort, rank,
  summarise, compare, rewrite — that content is the *subject* of the request.
  Search may enrich it, never replace it.

Help is judged by whether it reflects what the assistant already knows and has been
told. The user should never have to re-supply what they already gave.

## Value: be data driven

**When we don't know, and the answer is non-trivial, we run an experiment to find out.**
The system is built on beliefs about how memory, retrieval, and reasoning actually behave,
and a belief that has not been measured is a guess wearing a design document's clothes.

Concretely:

- **Measure before building on it.** If a plan rests on "the model can reason about
  conflicts", "the walk recovers useful memory", or "the ladder changes decisions", that
  claim gets an experiment before the work is built on top of it.
- **Pre-register.** Write the question, hypotheses, variables, and **falsification
  conditions** before collecting data. A hypothesis that cannot be falsified is not a
  hypothesis.
- **State the bar in advance.** "If walk-sourced frames are under ~20% of the final
  context, the fix did not deliver and I will say so." Decide what would count as
  disproof *before* seeing the numbers.
- **Name the threats upfront**, including the ones that remain after the run — not only
  the checks that passed.
- **A negative result is a result.** A measurement that kills a proposed feature is a
  success: it saved the work. `max_graph_frames=20` being no better than 7 was worth more
  than a yield number that looked good.
- **Read-only against a brain copy.** `assistant/experiments/preflight.py` gates every
  experiment that opens a brain: the experiment DB must not be the live DB, the live volume
  must not be mounted, a restorable backup must exist, and the copy's SHA-256 is recorded.
  Table digests prove non-mutation rather than asserting it.

The pattern lives in `assistant/experiments/` — see `graph_walk_yield` (pre-registration +
falsification conditions) and `frame_budget` (deriving a constant by measurement). An
experiment's `plan.md` is committed **before** data is collected, and `result.md` is written
only after `verification.md`.

This value is not ceremony. It is how the project avoids the failure mode of building
sophisticated machinery that does nothing, and then reading its own code as evidence that
it works.

## Repository layout
- `/assistant/` — the cognitive assistant. See `/assistant/AGENTS.md` for the cognitive architecture and memory model.
- `/plans/` — **transient** working plans, one per active task (see `RUNBOOK.md`).
  Plans are deleted when their work ships; they are not kept as history. Git
  history is the archive.

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
- Run backend (dev): `docker compose up -d --build` — Vite dev server, Caddy on 127.0.0.1:8443
- Run backend (prod): `docker compose -f docker-compose.prod.yml up -d --build` — built bundle, Caddy on 127.0.0.1:8444
  - **Dev and prod are separate Compose projects** (separate images, databases, ports) and run side by side. A source change does not reach either until its image is rebuilt, so rebuild **both** to verify a change: "it still does the old thing" is usually an un-rebuilt environment. After rebuilding, hard-refresh the browser (⌘⇧R) — the SPA holds its JS in memory.
  - The two Caddy ports differ on purpose (dev 8443, prod 8444); working in one says nothing about the other.
- Run CLI: `./assistant/bin/assistant chat`
- Run tests: `docker run -it --rm -v $(pwd):/app -w /app assistant pytest assistant/tests/`
- Run full suite (plain + encrypted): `./run_ci.sh`
- Run single test mode: `docker compose -f docker-compose.test.yml run --rm test-plain`
- Lint: `docker run -it --rm -v $(pwd):/app -w /app assistant ruff check .`
- Frontend check: `cd frontend && npm run check` (lint + typecheck + tests; requires host Node)

### Container resilience
- `restart: unless-stopped` handles a crashed process (Docker backs off
  exponentially, capped at 1 minute, so it cannot hot-loop). It does **not**
  handle a hung process, which never exits — `autoheal` (prod) restarts
  containers whose health status goes unhealthy.
- The healthcheck probes `/healthz`, a **liveness** route that does no I/O. Do
  not point it at `/health`: that probes Ollama and can block for the client
  timeout when Ollama is down, which would mark the app unhealthy and restart it
  in a loop over an outage a restart cannot fix.
- `autoheal` is scoped by label (`AUTOHEAL_CONTAINER_LABEL=autoheal`), never
  `all` — this host runs other stacks, and a global autoheal would restart them.
- Logs are rotated (json-file defaults to unbounded and the app logs per turn and
  per LLM call).

## Pre-Commit Flow (Required)
**Before every commit, run both lint and tests in Docker:**

```bash
# Backend
docker run -it --rm -v $(pwd):/app -w /app assistant ruff check .
docker run -it --rm -v $(pwd):/app -w /app assistant pytest assistant/tests/

# Frontend
docker run -it --rm -v $(pwd)/frontend:/app -w /app assistant npm run lint
docker run -it --rm -v $(pwd)/frontend:/app -w /app assistant npm run typecheck
docker run -it --rm -v $(pwd)/frontend:/app -w /app assistant npm run test
```

`npm run check` runs lint + typecheck + tests in one pass. The typecheck gate is
`tsconfig.typecheck.json` (strict, source-only). It exists because `vite build` and
`vitest` both transpile without type-checking, so without it a type error can ship
silently.

## Code style
- Python 3.11+. Type hints required on all public functions.
- `ruff` for lint/format. Line length 100.
- Pydantic v2 for all API schemas and config.
- Async for all LLM/Ollama calls and DB writes in the hot path.

## Testing instructions
- **Scope tests to the change while iterating.** Run only the test files that
  exercise the feature you are touching (e.g. `pytest assistant/tests/test_extractor.py`
  for an extractor change, or the new regression test you just wrote). Keep the
  edit/test loop fast. The full suite is a pre-commit gate, not an inner-loop tool.
- **Run the full suite before every commit/merge** (`pytest assistant/tests/`), per
  the Pre-Commit Flow below.
- Every memory-system module must have unit tests (frames, slots, associations, conflicts, retrieval).
- End-to-end tests must cover: learn-a-fact-then-recall, and contradiction-then-auto-resolve.
- Critical path tests (voice, run_now, correction, search extraction) must pass before any merge.
- Run `pytest assistant/tests/test_daily_schedule.py assistant/tests/test_review_fixes.py` before considering any change done.
- The full suite (`pytest assistant/tests/`) must pass before merge. `python-multipart`
  is a declared dependency, so the API tests run in the standard image; there is no
  longer a known-exceptions carve-out.

## Working with the memory system
- The SQLite DB is the agent's brain. Never wipe it in tests without explicit backup.
- Use `assistant db backup` / `assistant db restore` for safety.
- Confidence math is in `assistant/backend/memory/confidence.py` — change it there only.

## Web search
The search tool is built on a swappable `SearchBackend` ABC in `assistant/backend/pipeline/search.py`. The default implementation is `SearXNGBackend`. An optional `BraveBackend` is available for higher-quality results. The end-to-end pipeline — router → query distillation → backends → relevance gate → extraction → imagery — is documented in **`docs/SEARCH.md`**; read it before changing the search path.

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
- Fetches URL content, strips HTML, detects JS-rendered pages.
- **robots.txt is parsed with `urllib.robotparser`** (per User-agent, per path) and
  checked against our product token `AssistantBot` — not the full UA, whose first
  token `robotparser` would read as "Mozilla". Absent/unreadable robots = allow.
  See `docs/FETCHING.md` for the policy and the AI-block intent caveat.
- `follow_links` (0–5) reads further into the same site: same-host links only,
  deduped, robots-checked per URL, paced, every hop SSRF-checked.
- After fetching, calls `extract_facts_from_document()` and stores facts in memory with `source_type="web_fetch"`.
- Extraction errors are logged and degrade gracefully — fetched content is always returned.
- Tool user-agent: `Mozilla/5.0 (compatible; AssistantBot/1.0)` — honest; the
  project does not spoof a browser UA to evade a block.

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

### Speed-first responsiveness
Be as fast as the task allows, and no faster than it deserves. Response time
should match what was asked: a quick factual recall or UI interaction should feel
immediate, while a question that genuinely benefits from deep reasoning is worth
waiting for. There is no fixed millisecond target — a number chosen without
reference to the work is meaningless, because the right latency depends on the
task.

What we do commit to:
- No *avoidable* latency. If a wait is fixed cost rather than necessary thinking
  (a serial round trip, a blocking call on the hot path, a redundant model call),
  remove it. The point of being fast is not a benchmark; it is not making the user
  wait for something that did not need to happen.
- Flexibility over consistency. A cheap turn should be quick; a hard, valuable
  turn may take longer. Do not flatten that difference to hit a constant.
- Immediate feedback. Whatever the duration, the user should see that work has
  started right away.
- Measure, never assume. Timing logs (`turn_pregen`, `turn_timings`, `ttft_ms`)
  exist so speed work is driven by data, not by feel. Read them before and after
  a change to the hot path.

### Visual feedback and transitions
Use CSS transitions for state changes (hover, focus, open/closed, loading) so
changes read as deliberate rather than abrupt. Keep them brief — around a fifth
of a second — so feedback feels responsive rather than laggy. Animations should
add clarity to the interaction flow, not decorate it; reach for one only when it
communicates something (progress, activity, arrival). For text inputs that
auto-expand, use a smooth height transition.

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

### Safety & Privacy: user data protection
The system is designed to support and protect the user and their data. 
The agent must confirm before performing actions that could expose sensitive information:

- **Search confirmation**: Before sending queries to external search APIs (Brave), confirm the user wants to send that data. Local SearXNG searches stay on-device.
- **File upload awareness**: When uploading files to chat, show what content will be sent to the LLM (preview, entities, questions).
- **Web fetch transparency**: The `fetch_url` tool shows the target URL before fetching and extracting facts.
- **Data locality**: All LLM inference runs locally via Ollama (127.0.0.1). No data leaves the machine unless the user explicitly opts into Brave Search.
- **No telemetry**: Zero analytics, tracking, or phone-home code. The agent's memory stays in the local SQLite database.
- **User consent for external calls**: Any network request beyond localhost (Ollama, SearXNG, Brave) requires explicit user action or configuration.

## Commit messages
- Conventional commits: `feat:`, `fix:`, `test:`, `docs:`, `refactor:`.
- Reference the phase/task in the body, e.g. `Phase 1.2: slot confidence logic`.