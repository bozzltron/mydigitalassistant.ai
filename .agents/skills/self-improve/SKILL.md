---
name: self-improve
description: Read the register and the dev and prod docker logs, learn what is actually failing, and ship one measured improvement through the same path as a feature. Use when asked to improve the system from its own behaviour.
---

# self-improve@v1

The system is measured by its own logs, and the register says what is still owed. This
runbook turns both into one improvement per run, and it is judged by the bar the plan
pre-registers — not by whether the change felt good.

**planner · implementor · reviewer · verifier**, with the log reading as step 3 rather than an
agent of its own. A separate observer meant the evidence and the plan were written by different
sessions that could disagree about what the logs said; the reading is a step, and its output is
a file. The design-principles and scoped-docs checks are verifier rubric rows.

1. **Create or reuse a run ID** (`YYYY-MM-DD-xxxx`). Work in `.agents/runs/<run-id>/`. Create
   `ledger.md` with the header from `docs/AGENTIC_WORKFLOW.md` §The ledger. Skip any step whose
   output file exists.
2. **Read the register first, then both stacks — always.** The register first: a deferred
   finding is the one thing a window of logs cannot tell you, because it happened in an earlier
   run, and if nothing reads it forward it dies there.

   ```bash
   cat .agents/backlog.md
   docker compose logs --no-color --since 168h assistant
   docker compose -f docker-compose.prod.yml logs --no-color --since 168h assistant
   ```

   **`168h`, not `7d`** — Docker rejects days in `--since` and says so on *stderr*, so the
   failure reads as "no logs". `scripts/log-snapshot.sh` normalises `7d` for you; the raw
   commands above do not. Dev is 8443, prod is 8444; a signal in one says nothing about the
   other. Docker discards a container's log when the container is recreated, so the window may
   be shorter than you asked for — say so rather than reporting a truncated window as the whole
   truth.
3. **Turn the logs into evidence** → `findings.md`. Extract the per-turn facts, not impressions:
   `turn_pregen`/`turn_timings` (where the dead time goes, `ttft_ms`), `tool_loop_round`,
   `tool_loop_exit`, `context_fixed`/`context_usage`, `tool call:` failures and
   `duplicate (#N)`, and `Task '…' completed|failed`. Rank by **frequency × user-visible cost**,
   not novelty, and separate three buckets explicitly — **healthy** (do not "fix" these),
   **failing** (evidence of user harm) and **candidates** (a hypothesis worth measuring). Each
   candidate carries the metric that would move, its current value, and what would prove it did
   not work. **No number without a log line**; never invent a baseline, and say which
   confounders were in play. Keep it under two pages.
4. **STOP — the human picks the candidate.** Put the evidence's **blind spot** to them as the
   question before they choose: the one candidate the window *cannot* see because the failure
   leaves no log line. A failure that leaves no log line outranks a ranked list. Step 3
   proposes; it does not choose. If the human says "top one", use it.
5. **planner** → `plan.md`, including the pre-registered bar, the falsification condition, a
   risk tier and its **weakest link**. **STOP** again — put that weakest link to the human as a
   question before asking for approval. "Looks good" answers a different question.
6. **implementor** → code + tests + `implementation.md` + `test-report.md`. For a bug the
   failing test comes first and is watched going red before the fix. It drains rows **S1 → S4**
   and may not defer an S1 or S2 — those escalate. Dispositions are filled once, at step 10.
7. **reviewer** → `ledger.md` rows, on a different model family from the implementor. Blocking
   rows back to the implementor once to *fix* — code only, no dispositions — and the reviewer
   closes them.
8. **Deploy and soak.** Rebuild **both** environments, then let the system run for the window
   the plan named (usually enough turns or one scheduler tick). This step is the human's gate —
   a change measured on the machine that built it has not been measured.
9. **Measure** → `measurement.md`: the **same** metrics, the **same** method, over the soak
   window. Before and after side by side, plus the confounders that were in play (concurrent
   turns, model eviction, a rebuild).
10. **disposition pass** — **implementor** fills in every open `disposition` and completes
    `implementation.md` as the ledger projection. The **only** disposition pass, after the last
    gate that can open a row. No code changes unless a row demands one, and no deferral of S1 or
    S2 — those escalate.
11. **verifier** → `verification.md` + one line in `.agents/runs/log.jsonl`. It checks the bar
    from step 5, not the story from step 6; it reads `measurement.md` and every row in
    `ledger.md`, walks the seven **design principles**, checks whether this diff made any
    doc claim false, and reads the test diff for weakened assertions. A missing measurement
    is `PARTIAL`; an open row is `PARTIAL`.
12. **PARTIAL or FAIL** → gaps to the implementor, re-run 6–11. **Two retries maximum**, then a
    human takes over with `verification.md`.
13. **PASS** → run `scripts/agent-gates.sh --full`, then commit with the trailers:

    ```
    Runbook: self-improve@v1
    Run-ID: <run-id>
    Risk: <tier>
    ```

## What this runbook may not do

- **Lead every reply with the four-part signal** — progress, core issue, core solution,
  generalisation (`docs/AGENTIC_WORKFLOW.md` §Reporting back). The human's attention is the
  binding constraint, not tokens.
- **No scoring edits.** `verifier.md`, `.agents/evals/` and the log format are human-owned. This
  is the one rule the spec is emphatic about: an agent that can move its own score will.
- **No "improvement" without a metric.** If the logs cannot show the effect, the run stops at
  step 5 and the finding is recorded as unmeasured.
- **No second mechanism for a known residual.** If the last run showed the problem is model
  behaviour, `RUNBOOK.md` §10 says stop, not add a layer.
- **No bundling.** One finding per run. A run that fixes three things cannot tell you which one
  worked.
- **No memory surgery without §11.** Any change that writes to the brain takes a portable
  export, restores it, and compares table counts first.
- **No brain-derived values outside `.agents/runs/`.** Docker logs carry user content — a
  turn's text, a frame value, a filename. Quote the line's *shape*
  (`tool_loop_exit reason=answered`), never a personal value.
  `scripts/pii-guard.sh` runs in the pre-merge gate against a gitignored denylist.

## Known limits

- The harness does not enforce step order; the handoff files are what prove which steps ran,
  and the verifier checks the outcome regardless.
- Hook-based telemetry is not trusted (the vendor's subagent hooks were unreliable in 2026);
  measurement is the verifier's log line plus the before/after snapshot.
- The log reading sees behaviour, not intent. If a failure is invisible in the logs, that is
  itself the finding: add the log line first, in its own run.
