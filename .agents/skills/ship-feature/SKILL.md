---
name: ship-feature
description: Plan, implement, review and verify a feature or bug fix from a spec. Use when asked to ship or build a change end to end.
---

# ship-feature@v1

Chains four agents through handoff files, so any step can run alone and the run can stop and
resume at the last file written. A human approves the plan and merges the result; no agent
grades its own work.

**planner · implementor · reviewer · verifier.** The implementor writes the tests as well as
the code. The design-principles and scoped-docs checks are verifier rubric rows, not
dispatches — `docs/AGENTIC_WORKFLOW.md` §Audits that are not runbook steps.

1. **Create or reuse a run ID** — `YYYY-MM-DD-xxxx` — and work in `.agents/runs/<run-id>/`.
   Create `ledger.md` with the header from `docs/AGENTIC_WORKFLOW.md` §The ledger; it is the
   only place a finding may live. Skip any step whose output file already exists.
2. **planner** → `plan.md`, with numbered acceptance criteria (`AC1`…), a **risk tier**, and a
   pre-registered bar if the change is measurable. If the work is too big — more than ~5
   criteria, more than one independently mergeable change, or more than a day — write
   `split.md` instead: smaller tickets that each ship on their own, in blocked-by order, and
   stop there. **STOP.** Put the plan's **weakest link** to the human as a question — the
   assumption that, if wrong, makes the plan wrong — then ask them to approve the plan, the
   risk tier, or the split. "Looks good" is not an answer to the weakest-link question.

   **Small-change lane — skip the planner.** A change that is **one file, under ~50 lines,
   and adds no new behaviour** (a regression test, a typo, a doc fix, a one-line guard) does
   not get a planner dispatch. Write `plan.md` yourself in the run directory: the goal in one
   sentence, the acceptance criteria, the falsifier, and the risk tier. Then go straight to
   step 3. The reviewer and the verifier still run — they are the gates that matter, and this
   lane removes fixed process cost, not judgement. If the change turns out to touch a second
   file or a memory path, stop and take the full path. Basis: an evaluator is worth its cost
   *when the task is beyond what the model does reliably alone*; a 38-line test is not, and a
   full four-gate pass on one measured $0.29.
3. **implementor** → code + tests + `implementation.md` + `test-report.md`. **For a bug the
   failing test comes first** and is watched going red before the fix. It drains the reviewer's
   rows in **S1 → S4** order and may not defer an S1 or S2 — those escalate to you. It does not
   fill in `disposition` yet: one bookkeeping pass after the last gate is cheaper than two.
4. **reviewer** → `ledger.md` rows, with `sev`, `claim`, `where` and a falsifier. It reads the
   diff only, on a different model family from the implementor.
5. **Blocking rows** go back to the implementor **once** to *fix* — code only, no dispositions
   — and the reviewer closes them. Then re-run steps 3–4. A row reopened twice escalates to the
   human: a third attempt is a guess with a plan attached.
6. **disposition pass** — **implementor** fills in every open `disposition` and completes
   `implementation.md` as the ledger projection. This is the **only** disposition pass, and it
   runs after the last **reviewing** gate (the reviewer, step 4) — the verifier in step 7 opens
   its own `v-` rows *after* this pass by design, and closes them itself, so a `v-` row is never
   something the implementor dispositions. No code changes unless a row demands one (then fix it
   and cite the fix); no deferral of S1 or S2 — those escalate.
7. **verifier** → `verification.md` + one line in `.agents/runs/log.jsonl`. It re-derives the
   requirements from the spec, audits every ledger row, walks the seven **design principles**
   (`AGENTS.md`; a principle with no test or lint enforcing it is a reported gap), checks
   whether this diff made any doc claim false, and reads your test diff for weakened
   assertions. **An open row is `PARTIAL`.** It never fixes anything.

   **It dispositions and closes its own `v-` rows.** They are opened *after* the only
   disposition pass, so nobody else may write them; leaving them open makes every run that
   finds anything permanently `PARTIAL` with all requirements met — measured on
   `2026-10-31-b03`, where `v-01` was born undispositioned and no agent could lawfully give
   it one. A severity it disputes is still a human ruling: it lands in the ledger's
   `## Amendments` table, never as an edit to the row.

8. **PARTIAL or FAIL** → the listed gaps go to the implementor, then re-run 3–7. **Maximum two
   retries**, then stop and hand `verification.md` to the human.
9. **PASS** → run the full gates, then commit with the trailers:

   ```
   Runbook: ship-feature@v1
   Run-ID: <run-id>
   Risk: <tier>
   ```

## Splitting

A plan that covers more than one independently mergeable change is a `split.md`, not a plan.
Work does not proceed on a split until a human turns it into tickets.

Each ticket then gets **its own run** — its own run ID, ledger and verifier score, verified in
`scope: ticket`. When the **last** ticket merges, dispatch the **verifier once more with
`scope: project`**: it re-reads every ticket's verification against the *original* spec and
writes `project-verification.md`. That pass is the only thing that catches a requirement lost
between the smaller tickets, because each ticket's own verification was — correctly — only
about that ticket. Do not skip it because every ticket passed.

## Gates (from RUNBOOK.md — non-negotiable)

These run **once per run, at step 9, by the orchestrator** — never by each agent. The
implementor runs its own test files, the reviewer runs at most one named test, and the verifier
re-runs the acceptance tests. Three full suites for one diff is ~10 minutes of serial
wall-clock that cannot change the result (measured, `2026-11-01-sf1`).

```bash
scripts/agent-gates.sh --full
```

That runs `./run_ci.sh` (dead-code check, frontend lint + tests + build, the critical-path
tests, and the full suite in **plain and encrypted** DB modes) plus
`assistant/scripts/verify_security`. For the inner loop, use
`scripts/agent-gates.sh --scoped <test files> [--frontend]`.

Rebuild **both** environments before claiming a live change works — dev on 8443
(`docker compose up -d --build`), prod on 8444
(`docker compose -f docker-compose.prod.yml up -d --build`). Neither reloads Python, and a
source change does not reach a running container until it is rebuilt.

## How to report to the human

Every reply leads with the four-part signal — progress, core issue, core solution,
generalisation (`docs/AGENTIC_WORKFLOW.md` §Reporting back) — and then carries five things.
The human is deciding whether to trust and merge, not reading for interest:

1. **Intent** — what the change is *for*, in one sentence, before what it does.
2. **Files** — how many change, which ones, and the diff size. A count alone hides which
   surface moved; a file list with no count hides the size.
3. **Coverage** — what proves it: the named test, whether it was *shown* to fail, and the
   suite count before and after. "Tests pass" with no delta is not evidence.
4. **Confidence** — high/medium/low and the one thing that would change it. An unhedged
   claim is not confidence, and a caveat with no bound is not either.
5. **Proof** — the command and its output, or an artifact a reader can re-run. An executed
   falsifier always beats an argument that the thing would pass.

## Rules

- **Lead every reply with the four-part signal** — progress, core issue, core solution,
  generalisation (`docs/AGENTIC_WORKFLOW.md` §Reporting back). The human's attention is the
  binding constraint, not tokens.
- One run, one change. If the spec grows a second feature, it is the next run — or a split.
- **No brain-derived values outside `.agents/runs/`.** A slot value, a person's name, a personal
  filename or a place is personal even when it looks mundane. Cite the code path and an
  aggregate count instead. `scripts/pii-guard.sh` runs in the pre-merge gate.
- A change that writes to memory must follow `RUNBOOK.md` §11 first (portable export, restored
  and compared).
- If two consecutive rounds miss their pre-registered bar, stop — `RUNBOOK.md` §10.
- The verifier, the evals, the design principles and the log format are human-owned. Agents
  propose; a human edits.
