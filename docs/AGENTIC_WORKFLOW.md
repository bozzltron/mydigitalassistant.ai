# Agentic dev workflow

How we run agents on this repo: single-purpose subagents, two runbooks, handoff
files, and a human at every gate. It follows the org spec ("Agentic Dev Workflow:
Architecture & Requirements", Oct 2026) with OpenCode as the harness rather than
Cursor. Nothing here changes how the **assistant** runs — it is still local Ollama
only.

## The one rule that matters

> Agents remove toil. Humans keep judgment, approval and ownership.

Concretely: a human approves the plan, answers the verifier's questions, and merges.
No agent edits `spec-verifier.md`, `.agents/evals/` or the scoring log — an agent
that can move its own score will.

## Layout

```
AGENTS.md                         # team rules every agent follows
.agents/
  agents/                         # source of truth — edit here only
    observer.md
    planner.md
    implementer.md
    tester.md
    reviewer.md
    design-guardian.md
    doc-gardener.md
    memory-architect.md
    spec-verifier.md
  skills/
    ship-feature/SKILL.md         # spec → plan → code → tests → review → verify
    self-improve/SKILL.md         # logs → findings → plan → … → measure
  runs/
    log.jsonl                     # committed: one line per verified run
    <run-id>/                     # transient: the handoff files
  evals/                          # later: golden tasks for regression-testing runbooks
scripts/
  log-snapshot.sh                 # dev + prod docker logs → a run's evidence
  agents-link.sh                  # rebuilds the vendor symlinks
.cursor/agents  -> ../.agents/agents   (symlink, generated)
.claude/agents  -> ../.agents/agents   (symlink, generated)
.opencode/agents -> ../.agents/agents  (symlink, generated)
.claude/CLAUDE.md -> @AGENTS.md        (generated)
```

Run `.agents/runs/<run-id>/` is working state, like `/plans/`: git-ignored. The log
is the durable record and is committed.

## Agents and their models

One agent, one job, one model. Models are OpenCode IDs — swap them for another
tool's IDs (or `ollama/...` for a fully local loop) without touching the runbooks.

| Agent | One job | Model | Tier | Why this model |
|---|---|---|---|---|
| **observer** | Logs → evidence (`findings.md`) | `opencode-go/gpt-6-luna` | fast | 272k context and cheap (0.1/0.5); logs are the biggest input in the pipeline |
| **planner** | Spec → plan + pre-registered bar | `opencode-go/kimi-k3.1-pro` | strong reasoning | runs once per run, and a bad plan wastes the whole run — the one place to pay for reasoning |
| **implementer** | Plan → code | `opencode-go/deepseek-v4.1-flash` | fast coding | the **highest-volume role**, so the cheapest capable model: 6× cheaper per token than the code specialist. The user's pick after using it for implementation |
| **tester** | Criteria → tests + report | `opencode-go/claude-haiku-5-5` | fast/mid | writing tests against a plan needs mid-tier judgement, not deep reasoning |
| **reviewer** | Diff → blocking findings | `opencode-go/glm-5.3` | mid | **different family from the implementer** (DeepSeek); strong on code, and it reads one diff per run, so its price is not the driver |
| **design-guardian** | Product pass → principles upheld | `opencode-go/gemini-4-argon` | strong, product judgement | owns the principles checklist; a fourth family, so the design pass is not the reviewer's opinion twice |
| **doc-gardener** | Docs corpus → drift + cleanup audit | `opencode-go/glm-5.3` | mid, large reading | owns the docs lifecycle; a fifth family, and it proposes rather than deletes |
| **memory-architect** | Memory code → hygiene + performance findings and patches | `opencode-go/kimi-k3.1-pro` | strongest reasoning | **ad hoc only**; deep, occasional, and the one review whose subject is the product's core code |
| **spec-verifier** | Spec + bar → verdict + score | `opencode-go/qwen3.8-max` | strong judge | **different family from the implementer**; the verifier is the measurement, so it gets the strongest available judge |

Two rules the model table encodes:

- **Different, not just stronger.** The reviewer and the verifier are deliberately a
  different family from the implementer. A second opinion from the same model is a
  formality.
- **Cost follows the work.** The implementer, observer and tester run on the cheap
  models, because they touch the most tokens. The planner, reviewer, guardian and
  verifier each run once per run, so they get the stronger models — cheapening a
  gate saves cents and risks the run.

Where the tool supports reasoning variants (`#low`, `#high`, `#max`), the planner and
verifier are the only two worth raising; the rest should keep the default.

### What "OpenCode models" does and does not change

`AGENTS.md` says **all assistant inference is local Ollama** and no cloud LLM API may
be added. That is a rule about the shipped system: the backend makes no cloud calls
and `TID` lint fails the build if a cloud SDK is imported. These agent files are the
**dev harness**, using the provider this repo is developed with, and they make no
calls at runtime. If you want the dev loop local too, replace each `model:` with an
Ollama id — the same roles map to `qwen3.8:27b` (planning/verification),
`qwen3.5:9b` (implementation/observation) and `qwen3.5:4b` (fast work).

## Running a runbook

**One agent alone.** Ask for it by name and run ID: "use the tester subagent on run
`2026-10-25-ab12`". It reads the existing handoff files and writes its own; it does
not need the earlier steps to have run in this session.

**The chain.** Invoke the skill (`ship-feature` or `self-improve`). It calls the
agents in order and stops for plan approval.

**Stop and resume.** State is in the files. Stop anywhere; re-invoke with the same run
ID and the runbook skips steps whose output already exists.

**Run ID.** `YYYY-MM-DD-xxxx` (date + a short suffix).

### ship-feature

```
plan.md → implementation.md → test-report.md → ledger.md → principles.md
         ↑ human approves the plan                  → docs-audit.md → verification.md
                                                          ↑ human merges
```

`PARTIAL` or `FAIL` sends the gaps back to the implementer up to twice; then a human
takes over with `verification.md`.

### self-improve

```
live logs → findings.md → (human picks) → plan.md → (human approves) → code + tests
          → ledger.md → principles.md → deploy + soak → measurement.md
          → docs-audit.md → verification.md
```

It starts from the **live dev and prod docker logs**, not from an idea. `observer`
turns them into evidence, a human picks one candidate, and the rest is the feature
path — except the verifier scores the plan's pre-registered bar against a fresh
`measurement.md`, not the implementer's story.

```bash
# what the observer reads (both stacks)
docker compose logs --no-color --since 7d assistant
docker compose -f docker-compose.prod.yml logs --no-color --since 7d assistant

# optional: freeze the evidence when a before/after must not move
scripts/log-snapshot.sh --since 7d --out .agents/runs/<run-id>/logs
```

One finding per run: a run that fixes three things cannot tell you which one worked.

## The ledger — how a finding becomes work

Every run has `.agents/runs/<run-id>/ledger.md`. It is created at step 1, it is append-only,
and it is the **only** place a finding may live. A finding that exists only as prose is a
finding nobody can prove was acted on: si01's "log the measured chars/token ratio" gap was
never picked up by si02, while si02's CLI finding was fixed only because it happened to land
inside the same diff.

### The row

```
| id   | src      | sev | claim                          | where         | falsifier                        | disposition | closed   |
|------|----------|-----|--------------------------------|---------------|----------------------------------|-------------|----------|
| r-01 | reviewer | S2  | guard reads only the last measured prompt | loop.py:212 | wrong if `test_over_window_shape` passes | open        |          |
```

- **`id`** — a source prefix, so no dispatcher is needed to allocate numbers: `r-` reviewer,
  `t-` tester, `o-` observer, `d-` design-guardian, `g-` doc-gardener, `v-` verifier.
- **`claim`** — the defect in one sentence. The defect, not a recommendation.
- **`where`** — `file:line`, a test name, or a log line.
- **`falsifier`** — "wrong if ⟨check⟩": a test name, a log line, a doc line.
- **`disposition`** — `fix <commit>` · `reject <result>` · `defer → B-<n>`.
- **`closed`** — the agent that confirmed the disposition. Empty until then.

### Rules

**L1 — Rows only.** No agent reports a defect in prose anywhere. `principles.md`,
`docs-audit.md` and `test-report.md` keep their verdicts and their non-finding sections; their
findings are ledger rows.

**L2 — The writer dispositions; it never closes.** The implementer sets `disposition` and
stops. The agent that opened the row closes it once the disposition checks out; the verifier
closes any row whose opener is no longer in the run. Writes stay single-threaded, judgement
stays distributed, and nobody grades their own work.

**L3 — Every row carries a falsifier.** A row you cannot attach a check to is a
**preference**: write `preference` in that cell. A preference may not be S1 or S2, and the
writer may reject it without argument — which is the point: it costs nobody a loop.

**L4 — Severity belongs to the opener.** The writer may not change it. A mis-set severity is
a finding of its own, opened by the verifier.

**L5 — Duplicates merge; contradictions escalate.** Two rows with the same `where` and the
same claim merge into one, `src` becoming `reviewer+tester`, keeping the stricter severity.
Two agents that *disagree* are not the writer's call: that is a question for the human, with
both falsifiers attached. Artifacts have tiebreaks; opinions do not.

**L6 — Priority is computed, not negotiated.** Drain S1 → S4, then by source reliability:
verifier > reviewer > tester > observer. Deterministic, so findings never compete for
attention on rhetorical force.

**L7 — One reopen.** A row may be reopened once. A second reopen means the disposition was
wrong: stop and hand the ledger to the human. Do not loop.

### Severity

| sev | class | may the writer defer? |
|---|---|---|
| **S1** | correctness, data loss, privacy/security, a test that passes wrongly | **no** — human only |
| **S2** | stability, a broken invariant | no — fix or escalate |
| **S3** | speed: hot-path cost, avoidable latency | yes, with a reason |
| **S4** | docs, dead code, cleanup | yes, with a reason |

Deferred rows move to `.agents/backlog.md` carrying the run id that raised them, and the
register is read at step 2 of `self-improve` before the logs.

## OpenCode support

The architecture does not change for OpenCode; four details do. OpenCode is the
harness this repo is developed in, so these are the ones we exercise every day.

| Piece | OpenCode path | Notes |
|---|---|---|
| Instructions | `AGENTS.md` | read natively; no `CLAUDE.md` needed |
| Agents | `.opencode/agents/<name>.md` | **the spec's `.agents/agents/` is not discovered** — `agents-link.sh` adds the symlink |
| Skills | `.opencode/skills/<id>/SKILL.md` | `.agents/skills` is a **project compatibility source**, discovered as-is — no link needed (confirmed: both runbooks appear in the skill list) |
| Models | `provider/model#variant` | already the form in the agent files (`opencode-go/...`) |
| Subagent mode | `mode: subagent` | without it a new agent defaults to `primary` |
| Read-only enforcement | `permissions` (`action`/`resource`/`effect`) | **enforced**, unlike advice: read-only agents can edit only `.agents/runs/**` |
| Hooks / telemetry | plugins | OpenCode has no `hooks.json`; measurement stays verifier-based |

### Adjustments the spec needs

The org spec was written for Cursor. These are the deltas, in order of how much
they matter:

1. **The vendor table gains a third column.** The spec lists Cursor and
   Claude Code; add OpenCode with the paths above. `.agents/` remains the source of
   truth, and `scripts/agents-link.sh` now creates three links instead of two.
2. **Agent frontmatter gains two fields.** `mode: subagent` and `permissions` are
   OpenCode-specific; other tools ignore them. The spec's "Can edit code? No"
   column becomes enforceable rather than advisory — the planner, observer,
   reviewer and verifier can write only their handoff files.
3. **Hooks become plugins.** The spec's Phase-2 telemetry (`scripts/agent-hooks/` +
   `.cursor/hooks.json`) has no OpenCode equivalent; use a plugin subscribing to
   events, or keep the verifier's log as the measurement. Given the spec's own
   note that the vendor's hooks were unreliable, the log-first route is the safe
   one.
4. **Invocation wording.** "Use the tester subagent on run `<id>`" is Cursor
   phrasing. In OpenCode you mention the agent (`@tester`) or ask the primary
   agent; skills load by ID, or explicitly with `@ship-feature` / `@self-improve`.
   The handoff files make the runbook vendor-independent regardless.

Everything else — R1–R8, the five agents, the gates, the log format, the registry
route — is unchanged.

### What is OpenCode-specific, and what is not

Keep this line clean when the files move to a shared registry:

- **Portable:** the markdown bodies (Inputs / Process / Output / Rules), the two
  `SKILL.md` files, `.agents/runs/log.jsonl`, `scripts/log-snapshot.sh`, the
  `AGENTS.md` rules.
- **Per-harness:** `model:` IDs, `mode:`, `permissions:`, and the symlink path.
  If a second harness needs different model IDs, give it its own thin config that
  points at the same bodies rather than forking the agents.

## Ad hoc audits (not runbook steps)

Two agents are deliberately **not** on a runbook's mandatory path, because their
value is depth and their cost is a full read:

- **`memory-architect`** — the **code** that maintains memory: invariants, write
  and read paths, consolidation, confidence, retrieval, extraction, summarization,
  and the hot-path cost of each. It reviews what the code enforces and proposes
  patches plus the tests that lock them, because a hygiene fix belongs where the
  next turn inherits it. It never reads the brain — no `/memory/*`, no SQL, no
  consolidation dry-run on live data; a data question is a finding for the owner.
- **`doc-gardener` full corpus** — the whole 144-file audit. The **scoped** pass
  stays in both runbooks (the docs a change touched); the full audit is its own
  run.

Both write to `.agents/runs/<run-id>/` like any other agent; a one-step run still
gets a run ID, so the audit is reproducible and citeable.

## Gates

Agents do not carry their own copy of the test commands — they call
`scripts/agent-gates.sh`, the one place that knows them:

| mode | runs |
|---|---|
| `--scoped <paths> [--frontend]` | `ruff check .`, the named pytest files (`--timeout=20`), optional `npm run check` |
| `--full` | `./run_ci.sh` — dead-code check, frontend lint/tests/build, critical-path tests, full suite in **plain and encrypted** DB modes — plus `assistant/scripts/verify_security` |

`run_ci.sh` is the pre-commit gate from `RUNBOOK.md`; scoped mode exists because the
full suite is a four-minute gate, not an inner loop. When a gate changes, it changes
in `RUNBOOK.md` and in this script **in the same commit** — that is what stops an
agent from running yesterday's commands. The script also encodes two host quirks: the
suite hangs at `--timeout=120` here (use 20), and the named test volume keeps tests
off the repo's `data/` and is ~4x faster.

## Measurement

One measure to start: the verifier's verdict and score per run, one line in
`.agents/runs/log.jsonl`.

```json
{"ts":"2026-10-25T14:02:00Z","run_id":"2026-10-25-ab12","runbook":"ship-feature@v1","verdict":"PASS","met":7,"total":8,"tests":"passing","retries":1,"human_questions":1}
```

Git trailers join runs to commits:

```
Runbook: ship-feature@v1
Run-ID: 2026-10-25-ab12
```

Richer metrics (cycle time, first-try CI pass rate, a 1–5 "did this save you toil")
wait until the log shows a pattern worth chasing. An eval set of 10–20 past tickets
lives in `.agents/evals/` when there is something stable to regress against.

## Requirements → where they live

| # | Requirement | Here |
|---|---|---|
| R1 | Agent runs a specific model, does one task | `.agents/agents/*.md` — YAML `model:` + one job |
| R2 | Agents hand off to one another | runbooks chain them through handoff files |
| R3 | Run one agent, or chain | every agent reads/writes the run dir; works alone |
| R4 | Local or cloud | OpenCode locally; the same files are read by Cursor/Claude via symlinks || R5 | Stoppable and startable | state is in `.agents/runs/<run-id>/`; skip-if-exists |
| R6 | Measured efficacy | `spec-verifier` → `verification.md` + `log.jsonl` |
| R7 | No vendor lock-in | `.agents/` + `AGENTS.md` + `scripts/`; vendor folders are symlinks |
| R8 | Shareable with owners | move `.agents/agents/` into a registry repo with `CODEOWNERS`; link back with `scripts/agents-link.sh` |

## Guardrails specific to this repo

- **Measure first.** The planner pre-registers the metric, the bar and the
  falsification condition; a verifier that cannot find the measurement returns
  `PARTIAL`, never `PASS`. (`RUNBOOK.md` §10 is the stopping rule.)
- **Brain protection.** Any run that writes to memory takes a portable export,
  restores it and compares table counts first (`RUNBOOK.md` §11).
- **Both environments or it isn't verified.** Dev 8443 and prod 8444 are separate
  images and databases; rebuild both before claiming a live change.
- **A product pass gets a design pass and a docs pass.** `design-guardian` runs on
  every feature and self-improve run, after the reviewer; it owns the principles
  checklist and reports a gap when a principle has no test or lint enforcing it.
  `doc-gardener` runs after it, scoped to the docs the change touched: a claim the
  change made false blocks, cleanup proposals do not. Both propose; the principles
  and the normative docs stay human-owned.
- **Runs are transient, the log is not.** Never commit `.agents/runs/<id>/`; never
  lose `log.jsonl`.

## Known limits

- Cursor does not enforce step order; the handoff files show which steps ran, and the
  verifier judges the outcome regardless.
- Hook-based telemetry is not trusted (the vendor's subagent hooks were unreliable);
  measurement is the verifier's line plus the before/after log snapshot.
- The observer reads logs, not intent. If a failure is invisible in the logs, that is
  the finding: add the log line in its own run first.
- Same-family reviewer and verifier would weaken both; the table keeps them apart.
