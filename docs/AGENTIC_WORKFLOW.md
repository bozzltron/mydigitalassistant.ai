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
| **implementer** | Plan → code | `opencode-go/kimi-k2.7-code` | strong coding | purpose-built code model; the bulk of the tokens |
| **tester** | Criteria → tests + report | `opencode-go/claude-haiku-5-5` | fast/mid | writing tests against a plan needs mid-tier judgement, not deep reasoning |
| **reviewer** | Diff → blocking findings | `opencode-go/deepseek-v4-pro` | mid | **different family from the implementer**, cheap (0.66/1.98), strong on code |
| **spec-verifier** | Spec + bar → verdict + score | `opencode-go/qwen3.8-max` | strong judge | **different family from the implementer**; the verifier is the measurement, so it gets the strongest available judge |

Two rules the model table encodes:

- **Different, not just stronger.** The reviewer and the verifier are deliberately a
  different family from the implementer. A second opinion from the same model is a
  formality.
- **Cost follows the work.** The implementer and observer run on the cheap models;
  the planner and verifier — two calls per run — get the expensive ones.

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
plan.md → implementation.md → test-report.md → review.md → verification.md
         ↑ human approves the plan                    ↑ human merges
```

`PARTIAL` or `FAIL` sends the gaps back to the implementer up to twice; then a human
takes over with `verification.md`.

### self-improve

```
logs/ → findings.md → (human picks) → plan.md → (human approves) → code + tests
      → review.md → deploy + soak → measurement.md → verification.md
```

It starts from the **dev and prod docker logs**, not from an idea. `observer` turns
them into evidence, a human picks one candidate, and the rest is the feature path —
except the verifier scores the plan's pre-registered bar against a fresh
`measurement.md`, not the implementer's story.

```bash
# what the observer reads
scripts/log-snapshot.sh --since 7d --out .agents/runs/<run-id>/logs
```

One finding per run: a run that fixes three things cannot tell you which one worked.

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
| R4 | Local or cloud | OpenCode locally; the same files are read by Cursor/Claude via symlinks |
| R5 | Stoppable and startable | state is in `.agents/runs/<run-id>/`; skip-if-exists |
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
