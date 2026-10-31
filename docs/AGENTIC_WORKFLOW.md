# Agentic dev workflow

How we run agents on this repo: single-purpose subagents, two runbooks, handoff
files, and a human at every gate. It follows the org spec ("Agentic Dev Workflow:
Architecture & Requirements", Oct 2026) with OpenCode as the harness rather than
Cursor. Nothing here changes how the **assistant** runs — it is still local Ollama
only.

## The one rule that matters

> Agents remove toil. Humans keep judgment, approval and ownership.

Concretely: a human approves the plan, answers the verifier's questions, and merges.

The scoring surface is write-once. **No agent edits `verifier.md` or `.agents/evals/`,
and no agent edits a `log.jsonl` line other than the one it appends.** The verifier does
append its own measurement line — its prompt says so, both runbooks say so, and every run
in the file proves it — but a line that is written is not then edited, the format is not the
verifier's to change, and no agent revises the criteria it is scored against. An agent that
can move its own score will.

## Layout

```
AGENTS.md                         # team rules every agent follows
.agents/
  agents/                         # source of truth — edit here only
    planner.md                    # run path
    implementor.md                # run path: code + tests
    reviewer.md                   # run path
    verifier.md                   # run path: judge + score
    design-guardian.md            # audit, own run ID
    doc-gardener.md               # audit, own run ID
    memory-architect.md           # audit, own run ID
  skills/
    ship-feature/SKILL.md         # spec → plan → code → tests → review → verify
    self-improve/SKILL.md         # register + logs → findings → plan → … → measure
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

| Agent | One job | Model | Tier | When | Why this model |
|---|---|---|---|---|---|
| **planner** | Spec → plan + pre-registered bar | `opencode-go/qwen3.8-max` | strong reasoning | every run | runs once per run, and a bad plan wastes the whole run — so it stays on a strong model, but output is $6/Mtok rather than $10: the planner writes a long `plan.md`, so its cost is output-dominated |
| **implementor** | Plan → code + tests + `implementation.md` | `opencode-go/deepseek-v4.1-flash` | fast coding | every run | the **highest-volume role** (74% of the tokens, 10% of the cost), so the cheapest capable model — 5th cheapest of 15 candidates at that volume, with the second-cheapest cache read in the fleet. The user's validated pick; **deliberately not changed**, because the cheaper options save 0.5–2¢ a run |
| **reviewer** | Diff → blocking findings | `opencode-go/glm-5.3-flash` | fast-reading | every run | **different family from the implementor** (DeepSeek); reads **one diff**, which is bounded work, so it is the role most likely to be over-priced per unit of output. Yield is pre-registered and revertible — §Model choice has the bar and the three falsifiers |
| **verifier** | Spec + bar → verdict + score | `opencode-go/qwen3.8-max` | strong judge | every run | **different family from the implementor**; the verifier produces the score everything else is measured by, and `B-19` already leaves it self-scored — it is the worst place in the roster to save money |
| **design-guardian** | Principles → enforced, or a gap named | `opencode-go/gemini-4-argon` | strong, product judgement | periodic audit, own run ID | owns the principles checklist; its output is repo-level (unpinned constants, unenforced principles), not per-run |
| **doc-gardener** | Docs corpus → drift + cleanup audit | `opencode-go/glm-5.3` | mid, large reading | quarterly audit, own run ID | owns the docs lifecycle; a fifth family, and it proposes rather than deletes |
| **memory-architect** | Memory code → hygiene + performance findings and patches | `opencode-go/kimi-k3.1-pro` | strongest reasoning | memory-code change, own run ID | deep, occasional, and the one review whose subject is the product's core code |

Four agents are on the run path, and everything else is an audit with its own trigger
(§Audits that are not runbook steps). The log reading that used to be an `observer` agent is
step 3 of `self-improve`: it is a process whose output is a file, not an identity, and a
separate session for it let the evidence and the plan disagree about what the logs said.

Two rules the model table encodes:

- **Different, not just stronger.** The reviewer and the verifier are deliberately a
  different *family* from the implementor. A second opinion from the same model is a
  formality — and `glm-5.3` vs `glm-5.3-flash` is the same family, so the family is what
  `test_agent_workflow.py` compares, not the model id.
- **Cost follows the work, not the role's prestige.** A model should be as strong as the
  judgement its role demands, and no stronger.

Where the tool supports reasoning variants (`#low`, `#high`, `#max`), the planner and
verifier are the only two worth raising; the rest should keep the default.

### Model choice — what is measured, what is assumed

Prices come from the live `opencode-go` catalog and are exact. **Token volumes are
assumptions**, because no run has ever logged the `gates` token fields (`B-18`); the figures
below assume planner 30k/8k, implementor 250k/60k, reviewer 80k/15k, verifier 100k/20k at 70%
cache read. Re-derive before quoting them as fact.

| Role | Tokens/run (assumed) | Share of tokens | $/run | Share of cost |
|---|---|---|---|---|
| planner | 30k / 8k | 9% | 0.0713 | 22% |
| implementor | 250k / 60k | **74%** | 0.0478 | **10%** |
| reviewer | 80k / 15k | 23% | 0.0128 | 4% |
| verifier | 100k / 20k | 29% | 0.1975 | **60%** |
| | | | **0.3294** | |

**The implementor does three-quarters of the work for a tenth of the bill.** It is
`deepseek-v4.1-flash` at $0.15/$0.60 with the second-cheapest cache read in the fleet
($0.003/Mtok), which is the cost an iterating implementor actually pays. Ranked by cost at
its own volume it is 5th of 15 catalog models; the four cheaper ones save 0.5–2¢ a run. That
is why a test pins it: changing it is a quality bet with no money in it.

The reviewer moved `glm-5.3` → `glm-5.3-flash` (**−89%** on that line) on the argument that it
reads **one diff**, which is bounded work. The planner moved `kimi-k3.1-pro` →
`qwen3.8-max` (−30%) because a `plan.md` is output-dominated and $6/Mtok beats $10. The
verifier did not move: it produces the score everything else is measured by.

**The reviewer's yield is pre-registered, not assumed.** `2026-10-31-mdl1` states the bar
(≥1 blocking row or ≥4 rows on at least one of the next 2 runs) and three falsifiers, any one
of which reverts it to `glm-5.3`: zero rows across 2 runs; a defect the verifier records that
no reviewer row covers; or a reviewer row whose falsifier was never run. The baseline it is
measured against is the `glm-5.3` reviewer's 9 rows on one diff in `2026-10-31-aset3`.

**The larger lever is not the models.** Every agent receives ~1,355 lines of `AGENTS.md` on
every dispatch. Halving that halves every role's input cost permanently, for any model — it
is a bigger saving than the whole model table above, and it is `T3`.

### The ratchet — a gate that cannot lose its job will accumulate

A gate nobody judges for contribution becomes an obligation nobody remembers choosing. Two
rules stop the set growing back:

1. **Every verified run records what each gate contributed.** The verifier writes the
   `gates` object into `log.jsonl` (§The instrument): `dispatched`, `rows`, `hits` and the
   token fields. `hits` are rows that survived verification (not rejected), so a gate whose
   rows are consistently rejected is visible as `precision = hits / rows` rather than as a
   feeling.
2. **A gate with `hits: 0` across 6 consecutive dispatched runs is folded into another
   gate or deleted**, in the next harness run, and the deletion is recorded like any other
   change. A gate that was not dispatched is `dispatched: false` and is **unmeasured**,
   never `0 hits` — the two fields exist apart so this rule cannot delete a gate nobody
   invoked. The reviewer's first real dispatch found two blocking defects, one S1, after
   six runs in which it had never been called.

The count is mechanical on purpose: nobody has to argue that a gate "felt useful", and a
gate cannot defend itself with prose. The rule applies to gates on a run path; an audit
outside the run path (§Audits that are not runbook steps) is judged by its own runs.

### What "OpenCode models" does and does not change

`AGENTS.md` says **all assistant inference is local Ollama** and no cloud LLM API may
be added. That is a rule about the shipped system: the backend makes no cloud calls
and `TID` lint fails the build if a cloud SDK is imported. These agent files are the
**dev harness**, using the provider this repo is developed with, and they make no
calls at runtime. If you want the dev loop local too, replace each `model:` with an
Ollama id — the same roles map to `qwen3.8:27b` (planning/verification),
`qwen3.5:9b` (implementation/observation) and `qwen3.5:4b` (fast work).

## Running a runbook

**One agent alone.** Ask for it by name and run ID: "use the reviewer subagent on run
`2026-10-25-ab12`". It reads the existing handoff files and writes its own; it does
not need the earlier steps to have run in this session.

**The chain.** Invoke the skill (`ship-feature` or `self-improve`). It calls the
agents in order and stops for plan approval.

**Stop and resume.** State is in the files. Stop anywhere; re-invoke with the same run
ID and the runbook skips steps whose output already exists.

**Run ID.** `YYYY-MM-DD-xxxx` (date + a short suffix).

### ship-feature

```
plan.md → implementation.md → test-report.md → ledger.md → verification.md
         ↑ human approves the plan                            ↑ human merges
```

`PARTIAL` or `FAIL` sends the gaps back to the implementor up to twice; then a human
takes over with `verification.md`.

### Splitting — a plan that covers more than one change is not a plan

A spec that covers more than one independently mergeable change, more than ~5 criteria, or more
than a day of work is a **`split.md`**, not a `plan.md`. The planner writes the tickets, their
`blocked-by` order, and stops; work does not proceed until a human turns a ticket into its own
run.

Each ticket then gets **its own run** — its own run ID, ledger and verifier score, verified in
`scope: ticket`. When the **last** ticket merges, the verifier runs **once more in
`scope: project`**: it re-reads every ticket's verification against the *original* spec rather
than against the split, and writes `project-verification.md`. That second pass is the only
thing that catches a requirement lost between the smaller tickets, because each ticket's
verification was, correctly, only ever about its own ticket.

A split is therefore two things at once: the mechanism that keeps a run small, and the step
that makes the last run look back at the whole.

### self-improve

```
register + live logs → findings.md → (human picks) → plan.md → (human approves)
  → code + tests → ledger.md → deploy + soak → measurement.md → verification.md
```

It starts from the **register and the live dev and prod docker logs**, not from an idea.
Step 3 turns them into evidence, a human picks one candidate, and the rest is the feature
path — except the verifier scores the plan's pre-registered bar against a fresh
`measurement.md`, not the implementor's story.

```bash
# what step 3 reads (both stacks)
docker compose logs --no-color --since 168h assistant
docker compose -f docker-compose.prod.yml logs --no-color --since 168h assistant

# optional: freeze the evidence when a before/after must not move
scripts/log-snapshot.sh --since 7d --out .agents/runs/<run-id>/logs
```

One finding per run: a run that fixes three things cannot tell you which one worked.

## Reporting back — the four-part signal

Every turn **leads** with these four answers, in this order, before any detail. A turn that
leaves the human to reconstruct them has moved the most expensive work onto the person the
harness exists to spare.

1. **Progress** — what is now true that was not true before this turn. One or two lines. What
   was *attempted* is not progress.
2. **Core issue** — the one thing blocking or in question, stated as a problem. If several,
   the one that makes the others wait.
3. **Core solution** — what resolves it, and how it will be shown to be resolved.
4. **Generalisation** — will this class of problem recur, and what is the cheapest change that
   would stop it recurring? "No" is a valid answer: a mechanism invented for a one-off is the
   `RUNBOOK.md` §10 trap.

Detail goes below them. These four lines are what the human reads first; everything after is
skippable if they trust the summary.

## The question at each gate

A gate that asks "approve?" invites a rubber stamp. A gate that asks **one question the agent
cannot answer** uses the human for the only thing they have that the harness doesn't: intent,
risk appetite, and knowledge of their own world.

One question per gate, never a list. A checklist of questions is skipped; a single question
at a decision point is answered. The agent answers its own question first, in the artifact,
so the human is adding knowledge rather than re-deriving the case.

| gate | who asks | the question |
|---|---|---|
| **Plan approval** | planner | **weakest link** — the assumption that, if wrong, makes the whole plan wrong |
| **Candidate pick** (self-improve) | step 3 (log reading) | **blind spot** — which candidate this window cannot see, because the failure is invisible in logs |
| **Verification** | verifier | **overturn** — the single observation that would flip this verdict |
| **Merge** | the human | **residual** — what you accept by merging that the run did not measure |

### What only the human can answer

These are the questions worth holding in mind; an agent asking any of them is asking you to do
its job, and an agent *omitting* them is assuming an answer it does not have.

- **Intent** — is this the change I actually wanted, or a plausible neighbour of it?
- **Noticeability** — would I notice if this were wrong? If not, the bar is measuring the
  wrong surface.
- **Private knowledge** — what do I know about how I use this that is not in these logs?
- **Residual** — what am I willing to accept as still-broken when this merges?

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
  `i-` implementor, `r-` reviewer, `v-` verifier, `d-` design-guardian, `g-` doc-gardener.
  `t-` and `o-` remain valid in ledgers written before the tester and observer were folded
  in — a historical row keeps the prefix it was written with, and `scripts/ledger-audit.py`
  still accepts them.
- **`claim`** — the defect in one sentence. The defect, not a recommendation.
- **`where`** — `file:line`, a test name, or a log line.
- **`falsifier`** — "wrong if ⟨check⟩": a test name, a log line, a doc line.
- **`disposition`** — `fix <commit or test>` · `reject <result>` · `defer → B-<n>` ·
  `escalate → <reason>`. A writer dispositions *before* the runbook commits, so `fix` may
  cite the test or file that carries it; the commit sha is appended when it exists. That is
  why the grammar is "commit **or** test" and not a bare sha — a rule that cannot be
  followed by the agent it binds is not a rule. `escalate` is what an S2 uses when the fix
  is outside this run: it is **not** a deferral, it needs a human, and the reason must say
  who and why.
- **`closed`** — the agent that confirmed the disposition. Empty until then.

### Rules

**L1 — Rows only.** No agent reports a defect in prose anywhere. `principles.md`,
`docs-audit.md` and `test-report.md` keep their verdicts and their non-finding sections; their
findings are ledger rows.

**L2 — The writer dispositions; it never closes.** The implementor sets `disposition` and
stops. The agent that opened the row closes it once the disposition checks out; the verifier
closes any row whose opener is no longer in the run. Writes stay single-threaded, judgement
stays distributed, and nobody grades their own work.

**Disposition happens once, after the last gate that can open a row.** Fixing and
bookkeeping are different passes: a fix must land while later gates can still see it, but
filling in `disposition` is cheapest once, at the end. Doing both twice cost the first run
~3M tokens — 15% of it — because the runbook drained the ledger at step 5 while two gates
opened rows at steps 6–7.

**L3 — Every row carries a falsifier.** A row you cannot attach a check to is a
**preference**: write `preference` in that cell. A preference may not be S1 or S2, and the
writer may reject it without argument — which is the point: it costs nobody a loop.

**L4 — Severity belongs to the opener.** The writer may not change it. A mis-set severity is
a finding of its own, opened by the verifier.

**L5 — Duplicates merge; contradictions escalate.** Two rows with the same `where` and the
same claim merge into one, `src` becoming `reviewer+verifier`, keeping the stricter severity.
Two agents that *disagree* are not the writer's call: that is a question for the human, with
both falsifiers attached. Artifacts have tiebreaks; opinions do not.

**L6 — Priority is computed, not negotiated.** Drain S1 → S4, then by source reliability:
verifier > reviewer > implementor. Deterministic, so findings never compete for
attention on rhetorical force.

**L7 — One reopen.** A row may be reopened once. A second reopen means the disposition was
wrong: stop and hand the ledger to the human. Do not loop.

**A run directory is append-only once `verification.md` exists.** A run is evidence about a
moment; editing its ledger or report afterwards replaces the record of what was true with the
record of what became true later, and the gap between those two is usually the finding. When
something deferred from run 2 is fixed in run 5, the answer goes in the **register row**, not
back into run 2's files.

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
   column becomes enforceable rather than advisory — the planner,
   reviewer and verifier can write only their handoff files.
3. **Hooks become plugins.** The spec's Phase-2 telemetry (`scripts/agent-hooks/` +
   `.cursor/hooks.json`) has no OpenCode equivalent; use a plugin subscribing to
   events, or keep the verifier's log as the measurement. Given the spec's own
   note that the vendor's hooks were unreliable, the log-first route is the safe
   one.
4. **Invocation wording.** "Use the reviewer subagent on run `<id>`" is Cursor
   phrasing. In OpenCode you mention the agent (`@reviewer`) or ask the primary
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

## Audits that are not runbook steps

Three agents run outside the two runbooks, because their value is depth and their cost is a
full read. Each is triggered by a condition rather than by anyone remembering it, each gets
its own run ID, and each writes to `.agents/runs/<run-id>/` like any other agent — so the
audit is reproducible and citeable even though it is not a step.

- **`design-guardian` — periodic enforcement audit.** Trigger: quarterly, **or** immediately
  after a verifier verdict names a principle with no test, lint rule or log line enforcing
  it — the verifier's own verdict row is the trigger, since a `d-` row is this audit's
  output. It owns the seven principles in `AGENTS.md` → Design principles, walks them one
  at a time, and reports a gap where a principle is decoration.
  Its output is repo-level — unpinned constants, unmeasured precision — which is why it is
  not a per-run gate: the per-run version recorded 0 blocking findings in 2/2 runs **and had
  never actually been dispatched**, so there was no evidence it earned a slot. The per-run
  check now runs inside the verifier, as a rubric row the verdict cannot skip.
- **`doc-gardener` — quarterly full-corpus audit.** Trigger: quarterly, **or** when a run's
  scoped docs row finds three or more dead claims at once. It inventories every tracked
  markdown file, checks claims against the code, and proposes deletions. The **scoped** check
  also runs inside the verifier on every run (the docs a change touched); the full audit is
  its own run. It proposes; a human deletes or rewrites, and the normative docs (`AGENTS.md`,
  `RUNBOOK.md`) are never edited by an agent. In 0/2 runs did it produce `docs-audit.md` as a
  run step, which is the evidence for demoting it rather than a reason to keep dispatching it.
- **`memory-architect` — before or after a memory-code change.** Trigger: a diff touching the
  memory path (frames, slots, associations, confidence, retrieval, extraction, consolidation,
  summarization), or a scheduled audit of it. It is ad hoc in the sense that no runbook
  dispatches it — not in the sense that nothing triggers it. Run ID of its own
  (`YYYY-MM-DD-mha01`). The **code** that maintains memory: invariants, write and
  read paths, consolidation, confidence, retrieval, extraction, summarization, and the
  hot-path cost of each. It reviews what the code enforces and proposes patches plus the
  tests that lock them, because a hygiene fix belongs where the next turn inherits it. It
  never reads the brain — no `/memory/*`, no SQL, no consolidation dry-run on live data; a
  data question is a finding for the owner.

The move from run step to periodic audit is only honest because the verifier audits both
checks every run: a principle with no verdict or no evidence is `PARTIAL`, a violation no
row carries is `PARTIAL`, and a dead doc claim no ledger row carries blocks. A check nothing
audits can be skipped silently, which is exactly what happened to `docs-audit.md` in the two
runs before that audit existed.

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

### Local gate enforcement — there is no CI

`--full` **stamps the tree it certified**, and `.githooks/pre-push` refuses to publish a
tree without a stamp, so "I meant to run it" cannot put an unverified commit on `main`.
Install once per clone:

```bash
git config core.hooksPath .githooks
```

It stamps the **tree**, not the commit, because the gate looks at what is on disk and the
tree exists before the commit does — a commit-keyed stamp could never be current. A dirty
tree is not stamped: the stamp would vouch for content that is not what gets committed.
`git push --no-verify` is the escape hatch, and it should be visible in the shell history
rather than habitual.

A GitHub Actions workflow existed for one day (`eeacecf`, `bc0f0f6`) and was removed. It
earned its keep first: on its single run it found three host dependencies the local gate
had been hiding — `ruff` run from the host `PATH`, an `agent-gates.sh` that assumed the
`assistant` image already existed, and gitignored `.env`/`.env.test` files that a clean
clone cannot start without. All three are fixed, so what remained was a "did you actually
run it" guarantee, and the hook provides that locally for zero minutes and no third party.
The workflow is recoverable from git history if a second developer ever joins.

## Measurement

### The instrument

One measure to start: the verifier's verdict and score per run, one line in
`.agents/runs/log.jsonl`, plus a `gates` object so a gate's contribution is comparable run
to run:

```json
"gates": {"reviewer": {"dispatched": true, "msgs": 56, "tok_in": 4606783, "tok_out": 27076,
                     "rows": 5, "hits": 3}}
```

- **`dispatched`** — whether the gate actually ran. A gate that was never dispatched is
  **unmeasured**, never `0 hits`. Without this field the 6-run kill criterion deletes gates
  nobody invoked, which is how the reviewer would have been retired before its first run.
- **`rows` / `hits`** — the verifier counts these from the ledger: rows opened, and rows that
  survived (not rejected). `precision = hits / rows`.
- **`msgs` / `tok_in` / `tok_out`** — the orchestrator fills these from the harness store,
  because the verifier has no access to it and only the dispatcher holds the session ids.
- **Token accounting has a trap.** `tokens.input` is the *uncached* delta; providers that
  cache put the real prompt cost in `tokens.cache.write`. Summing `input` alone understated
  the planner by 370× on the first run. Total is
  `input + cache.read + cache.write + output`.
- **`human_minutes`** — recorded by the human at verification. Unrecorded means the attention
  bar is unmeasured, which is `PARTIAL`, not a pass.

**What survives a pruned run.** The line also carries the run's durable record, because the
directory holding the prose is gitignored scaffolding and is deleted:

- **`bar`** and **`falsifier`**, copied from `plan.md` §Measurement. A score without the
  standard it was judged against is a number with no rubric.
- **`touched`** — the files changed, so a later reader can find the commit without the plan.
- **`deferred`** — the register ids this run raised (`["B-16"]`), so closing the loop does
  not depend on a directory that no longer exists.

With those, the paper trail is three immutable surfaces — this line, the commit (linked by
its `Run-ID:` trailer), and the register row — and nothing in a run directory is needed after
verification.

Nested harness stores differ by version (legacy `message` vs `session_message`); attribute by
the `agent` field in the live one, and treat `cost`, which reads 0 on a subscription, as
unusable — the metrics are token-based for that reason.

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
| R6 | Measured efficacy | `verifier` → `verification.md` + `log.jsonl` |
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
- **The design pass and the docs pass are verifier rubric rows, not dispatches.**
  Every run, after the reviewer, the verifier walks the seven principles and asks which
  test or lint enforces each one — a principle with no verdict or no evidence is
  `PARTIAL`, and a *violated* principle that no ledger row carries is `PARTIAL`. It then
  checks whether the diff made any doc claim false; a dead claim no ledger row carries
  blocks, cleanup proposals do not. Both used to be separate agents (`design-guardian`,
  `doc-gardener`); those are now periodic audits with a trigger and their own run ID
  (§Audits that are not runbook steps), because a per-run dispatch with no measurable
  contribution is a slot nobody can defend. The principles and the normative docs stay
  human-owned either way.
- **Runs are transient, the log is not.** Never commit `.agents/runs/<id>/`; never
  lose `log.jsonl`. `scripts/runs-prune.sh` (dry run by default) trims raw logs after 7
  days and whole run directories after 30, keeping the newest five and **never** deleting a
  run that has no log line — unverified means unfinished, not history.

## Known limits

- Cursor does not enforce step order; the handoff files show which steps ran, and the
  verifier judges the outcome regardless.
- Hook-based telemetry is not trusted (the vendor's subagent hooks were unreliable);
  measurement is the verifier's line plus the before/after log snapshot.
- The log reading in step 3 sees behaviour, not intent. If a failure is invisible in the logs, that is
  the finding: add the log line in its own run first.
- Same-family reviewer and verifier would weaken both; the table keeps them apart.
