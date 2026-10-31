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
