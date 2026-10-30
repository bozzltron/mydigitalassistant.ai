---
name: self-improve
description: Read the dev and prod docker logs, learn what is actually failing, and ship one measured improvement through the same path as a feature. Use when asked to improve the system from its own behaviour.
---

# self-improve@v1

The system is measured by its own logs. This runbook turns those logs into one
improvement per run, and it is judged by the bar the plan pre-registers — not by
whether the change felt good.

1. **Create or reuse a run ID** (`YYYY-MM-DD-xxxx`). Work in
   `.agents/runs/<run-id>/`. Create `ledger.md` with the header from
   `docs/AGENTIC_WORKFLOW.md` §The ledger. Skip any step whose output file exists.
2. **Read the live logs** — both stacks, always:

   ```bash
   docker compose logs --no-color --since 168h assistant
   docker compose -f docker-compose.prod.yml logs --no-color --since 168h assistant
   ```

   **`168h`, not `7d`** — Docker rejects days in `--since` and says so on *stderr*, so
   the failure reads as "no logs". `scripts/log-snapshot.sh` normalises `7d` for you;
   the raw commands above do not. So does piping them through `2>/dev/null`.

   `scripts/log-snapshot.sh --since 7d --out .agents/runs/<run-id>/logs` is
   **optional**: use it when you need the evidence frozen for the before/after at
   step 11, not as a prerequisite. Docker discards a container's log when the
   container is recreated, so a short window is normal — say so.
3. **observer** → `findings.md`. Evidence first: what is healthy, what is failing,
   and the candidate improvements ranked by frequency × user-visible cost. Each
   candidate carries a metric, its current value, and the bar it must clear.
4. **STOP — the human picks the candidate.** The observer proposes; it does not
   choose. If the human says "top one", use it.
5. **planner** → `plan.md`, including the pre-registered bar, the falsification condition
   and its **weakest link**. **STOP** again — put that weakest link to the human as a
   question before asking for approval. "Looks good" answers a different question.
6. **implementer** → code + `implementation.md` (files changed, decisions, what it did not
   touch). It fixes what the gates ask for, draining **S1 → S4**, and may not defer an S1 or
   S2 — those escalate to you. Dispositions are filled once, at step 12b, not here.
7. **tester** → tests + `test-report.md`, plus rows for anything that is a defect rather
   than a test gap.
8. **reviewer** → `ledger.md` rows; blocking rows back to the implementer once to *fix* —
   code only, no dispositions — and the reviewer closes them. Dispositions happen once, at
   step 12b.
9. **design-guardian** → `principles.md`; a violated verdict blocks, advisory
   findings do not. This is a product pass like any other.
10. **Deploy and soak.** Rebuild **both** environments, then let the system run for
    the window the plan named (usually enough turns or one scheduler tick). This
    step is the human's gate — a change measured on the machine that built it has
    not been measured.
11. **observer (after)** → `measurement.md`: the **same** metrics, the **same**
    method, over the soak window. Before and after, side by side, plus the
    confounders that were in play (concurrent turns, model eviction, a rebuild).
12. **doc-gardener** → `docs-audit.md`, scoped to the docs that describe the
    behaviour this run changed. A doc the change made false blocks; cleanup
    proposals are advisory.
12b. **disposition pass** — **implementer** fills in every open `disposition` and completes
    `implementation.md` as the ledger projection. The **only** disposition pass, after the
    last gate that can open a row. No code changes unless a row demands one, and no deferral
    of S1 or S2 — those escalate. Two bookkeeping passes are what the first run cost ~3M
    tokens on.
13. **spec-verifier** → `verification.md` + one line in `.agents/runs/log.jsonl`.
    It checks the bar from step 5, not the story from step 6, and reads
    `principles.md`, `docs-audit.md`, `measurement.md` and every row in `ledger.md`. A
    missing measurement is `PARTIAL`; an open row is `PARTIAL`.
14. **PARTIAL or FAIL** → gaps to the implementer, re-run 7–13. **Two retries
    maximum**, then a human takes over with `verification.md`.
15. **PASS** → run `scripts/agent-gates.sh --full`, then commit with the trailers:

    ```
    Runbook: self-improve@v1
    Run-ID: <run-id>
    ```

## What this runbook may not do

- **Lead every reply with the four-part signal** — progress, core issue, core solution,
  generalisation (`docs/AGENTIC_WORKFLOW.md` §Reporting back). The human's attention is the
  binding constraint, not tokens.
- **No scoring edits.** `spec-verifier.md`, `.agents/evals/` and the log format are
  human-owned. This is the one rule the spec is emphatic about: an agent that can
  move its own score will.
- **No "improvement" without a metric.** If the logs cannot show the effect, the run
  stops at step 5 and the finding is recorded as unmeasured.
- **No second mechanism for a known residual.** If the last run showed the problem is
  model behaviour, `RUNBOOK.md` §10 says stop, not add a layer.
- **No bundling.** One finding per run. A run that fixes three things cannot tell you
  which one worked.
- **No memory surgery without §11.** Any change that writes to the brain takes a
  portable export, restores it, and compares table counts first.
- **No brain-derived values outside `.agents/runs/`.** The observer reads logs, which can
  carry user content, and any audit of memory carries it by definition. Quote the shape of
  a log line, never a personal value; cite the code path and an aggregate count instead.
  `scripts/pii-guard.sh` runs in the pre-merge gate against a gitignored denylist.

## Known limits

- Cursor does not enforce step order; the handoff files are what prove which steps
  ran, and the verifier checks the outcome regardless.
- Hook-based telemetry is not trusted (the vendor's subagent hooks were unreliable in
  2026); measurement is the verifier's log line plus the before/after snapshot.
- The observer reads logs, not intent. If a failure is invisible in the logs, that is
  itself the finding: add the log line first, in its own run.
