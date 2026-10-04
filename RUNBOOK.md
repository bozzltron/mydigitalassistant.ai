# Agent Run Book

**Follow this run book for every task. No exceptions.**

---

## 0. Scope Check — Skip Plan Doc for Small Tasks

- If estimated effort **< 2 hours**: skip Sections 3–4 (plan doc + cleanup), go straight to **Section 5**
- If estimated effort **≥ 2 hours**: follow the full run book

---

## 1. Receive Requirements

- Read the user's request carefully
- Ask clarifying questions if anything is ambiguous
- Confirm understanding before proceeding

---

## 2. Context Gathering

- Explore the codebase to understand existing patterns, structures, and conventions
- Read relevant AGENTS.md files (root + `/assistant/AGENTS.md`)
- Identify affected modules, tests, and configuration
- Check for existing similar implementations to follow

---

## 3. Write Plan Document

- Create a plan in `/plans/` with frontmatter:
  ```yaml
  ---
  date: YYYY-MM-DD
  status: active | done | archived
  estimated_hours: <number>
  ---
  ```
- Plan content:
  - **Objective**: What we're building/fixing
  - **Phases/Milestones**: Logical breakdown of work
  - **Technical approach**: Key decisions, data flow, APIs
  - **Test strategy**: What to test, edge cases
  - **Rollback plan**: How to revert if things go wrong
- Name format: `YYYY-MM-DD-short-description.md`

---

## 4. Documentation Gate — Then Delete the Plan

A plan is a **workspace**, not a home. It is where reasoning happens *before* the
reasoning has a durable address. Deleting one loses nothing **iff the transfer
already happened**, so the gate is a check, not an intention.

### 4.1 Three homes, by what the information *is*

| The information | Its home |
|---|---|
| A rule that governs code someone will change | `assistant/AGENTS.md` — with the rule, its reason, and its measurement |
| How a subsystem behaves and is operated | `docs/<SUBSYSTEM>.md` |
| Why *this line* is written the way it is | the code comment, at the point of use |
| What shipped, and when | `docs/RELEASE_NOTES.md` |

**The reason travels with the code.** A rule that exists only in a doc gets violated
by someone who never opened the doc; a rule that exists only in a comment cannot be
found by grep. For a rule with wide blast radius, both: the full statement in
`AGENTS.md`, and the short reason at each site that depends on it.

### 4.2 Before deleting, confirm

1. **Every rule the plan introduced has a home** from the table above. Not "it is in
   the code somewhere" — a specific line you can point at.
2. **Every site that depends on a rule cites the rule's home**, not the plan. A
   `See plans/<file>.md` in a comment is a promise that outlives the plan; when the
   plan is deleted the promise breaks and the reader is left with a dead link.
3. **Run the check**: `pytest assistant/tests/test_plan_citations.py`. It fails if
   any tracked file cites a plan that is not in `plans/`. It is in the default gate
   because this failure is silent — nothing breaks, the knowledge just quietly goes
   missing at the next read.

### 4.3 Then delete

- A plan is deleted when its work ships. `plans/` holds only **active** work.
- Do not keep shipped plans as history — git history and `docs/RELEASE_NOTES.md`
  are the record. Archive nothing (there is no `archived/` directory by design).
- If a plan is abandoned rather than shipped, delete it too.
- **Delete the plan's cross-references with it.** A `supersedes:` field pointing at a
  file you are deleting is the same dangling pointer, one directory over.

### 4.4 Why this gate exists

This is not ceremony. Fifteen comments across `main.py`, `store.py`,
`tool_executor.py`, `orchestrator.py`, `runner.py`, `schema.py`, `AGENTS.md`,
`docs/FILES.md` and four tests pointed at three plans that had already been deleted
on ship. Every one of those comments turned out to state its rule and reason in
full — **nothing was lost**, which is exactly why the rot went unnoticed for as long
as it did. A dangling pointer produces no error; it just makes a reader doubt
whether the comment above it is still true.

---

## 5. Enrich Plan with Requirements

- Add specific acceptance criteria from requirements
- Note any constraints (security, performance, compatibility)
- Document dependencies and ordering

---

## 6. Double-Check Plan for Accuracy

- Verify technical approach matches codebase patterns
- Confirm all requirements are addressed
- Check that phases are correctly ordered and independent where possible

---

## 7. Double-Check Plan Aligns with Design Principles

Verify against AGENTS.md principles:

- [ ] **Clean ship** — No dead code, unused imports, or commented-out snippets
- [ ] **Speed-first UI responsiveness** — Sub-500ms basic interactions, sub-2s LLM cycles
- [ ] **Visual feedback & transitions** — 150-300ms CSS transitions, purposeful animations
- [ ] **Stability: no regressions** — Regression tests for critical paths
- [ ] **Safety & Privacy** — Local inference, no telemetry, user consent for external calls
- [ ] **Do/Don't rules** — Model reasoning over scripted fallbacks, no cloud LLMs, localhost-only binding

---

## 8. Execute the Plan

### For each phase/milestone:

#### 8.1 Implement
- Write code following project conventions (type hints, async, Pydantic v2, ruff)
- Add unit tests for new memory-system modules
- Add regression tests for critical path changes

#### 8.2 Run Live Code & Read Logs

**Both environments must be rebuilt.** A source change does not reach a running
container until its image is rebuilt and the container recreated, so "it still
does the old thing" usually means one environment was not rebuilt. Dev and prod
are separate Compose projects with separate images, databases, and ports; a
change is not verified until **both** are rebuilt.

```bash
# Dev — Vite on https://localhost:8443 (hot reload for the frontend)
docker compose up -d --build

# Prod — the built bundle on https://localhost:8444
docker compose -f docker-compose.prod.yml up -d --build

# Watch logs, verify behavior matches expectations
docker compose logs -f assistant
```

The backend image is shared by both (same `Dockerfile`); only the frontend
differs (Vite vs. the built bundle). Rebuild after any backend change, since
neither environment reloads Python.

After a rebuild, hard-refresh the browser (⌘⇧R): the SPA keeps its JavaScript in
memory and will otherwise keep running the previous bundle.


#### 8.3 Code Review Against Design Principles
- Self-review: Does this change violate any principle in Section 7?
- Check for: hardcoded fallbacks, cloud API calls, 0.0.0.0 binding, telemetry

#### 8.4 Run All Tests & Fix Failures

**Backend (Python):**
```bash
docker run -it --rm -v $(pwd):/app -w /app assistant pytest assistant/tests/
```

**Frontend (TypeScript):**
```bash
docker run -it --rm -v $(pwd)/frontend:/app -w /app assistant npm run test
```

#### 8.5 Evaluate Flaky Tests
- Run failing tests 3x to confirm flakiness
- If not meaningful (environment, timing): **remove them**
- If meaningful: **fix the root cause**

#### 8.6 Run Lint & Fix

**Backend (Python) — Principal Python Engineer review:**
```bash
docker run -it --rm -v $(pwd):/app -w /app assistant ruff check .
# Code must pass principal Python engineer standards: type hints, async patterns, Pydantic v2 usage
```

**Frontend (TypeScript) — Principal TypeScript Engineer review:**
```bash
docker run -it --rm -v $(pwd)/frontend:/app -w /app assistant npm run lint
# Code must pass principal TypeScript engineer standards: strict mode, no any, proper types
```

#### 8.7 Update Docs & Clean Docs
- Update API docs, README, AGENTS.md if patterns changed
- Remove stale documentation

#### 8.8 Commit
```bash
git add -A
git commit -m "feat: <short description>

Phase X.Y: <phase description>

<details if needed>"
```

#### 8.9 Push
```bash
git push
```

---

## 9. Post-Plan Completion (Holistic Review)

Once all phases/milestones are done:

### 9.1 Holistic Code Review
- Is this the **best solution** for our goals?
- Is this the **best solution for our stack** (FastAPI, Ollama, SQLite, Pydantic, Ruff, TypeScript, React)?
- Does it **follow all design principles** (Section 7)?

**Principal Engineer Sign-off Required:**
- [ ] **Principal Python Engineer**: Backend architecture, async patterns, type safety, Pydantic models, memory system integrity
- [ ] **Principal TypeScript Engineer**: Frontend architecture, strict types, React patterns, state management, API contracts

### 9.2 Vet Docker Compose Environments
```bash
# Dev environment
docker compose up -d --build
# Verify: FastAPI on 127.0.0.1:8443 via Caddy, all services healthy

# Prod environment
docker compose -f docker-compose.prod.yml up -d --build
# Verify: Compiles, runs, no published backend ports, Caddy on 127.0.0.1:8444

# Both: containers report healthy, and only this project's containers are
# autohealed (autoheal is label-scoped, not global).
docker compose ps
docker compose -f docker-compose.prod.yml ps
```

Note the prod Caddy port is **8444** (dev is 8443); they run side by side, so
"it works" in one says nothing about the other.

### 9.3 Final Commit & Push
```bash
git add -A
git commit -m "feat: <feature name> — complete

Holistic review passed. All design principles verified.
Prod docker compose validated."
git push
```

---

## Critical Path Regression Tests (Run Before Any Merge)

```bash
# Required before considering any change done
docker run -it --rm -v $(pwd):/app -w /app assistant pytest assistant/tests/test_daily_schedule.py assistant/tests/test_review_fixes.py

# Full suite must pass before merge
docker run -it --rm -v $(pwd):/app -w /app assistant pytest assistant/tests/
```

---

## Pre-Commit Checklist (Every Commit)

**Backend (Python):**
- [ ] `ruff check .` passes
- [ ] `pytest assistant/tests/` passes
- [ ] Principal Python Engineer review: type hints, async patterns, Pydantic v2, memory system

**Frontend (TypeScript):**
- [ ] `npm run lint` passes
- [ ] `npm run test` passes
- [ ] Principal TypeScript Engineer review: strict mode, no `any`, proper types, React patterns

**Security & Architecture:**
- [ ] No cloud LLM APIs introduced
- [ ] No 0.0.0.0 binding
- [ ] No telemetry/analytics
- [ ] Secrets in `.env` only (gitignored)
- [ ] Type hints on all public functions (Python)
- [ ] Strict types on all public APIs (TypeScript)

---

## Emergency Stop Conditions

**Halt immediately if:**
- Any cloud LLM API (OpenAI, Anthropic, Google, etc.) is added
- Backend binds to anything but 127.0.0.1
- SearXNG exposed beyond localhost
- Telemetry/analytics code appears
- `.env` committed (not `.env.example`)

---

*This run book is mandatory. Deviations require explicit user approval.*