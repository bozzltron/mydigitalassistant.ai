# Result — is the one-nudge-per-turn bound costing writes?

**No. Keep the single bound.** The gap is real in the code — on a read-instead-of-write
turn the completion judgement is never consulted (proven deterministically) — but it
**did not occur in 10 live turns**: the nudge was armed on all ten and fired on none,
because the model called `write_file` in its first round every time.

## By hypothesis

| | Verdict | Evidence |
|---|---|---|
| **H1 — the gap exists (deterministic)** | **holds** | shape S consults the completion judgement 0 times; the control 1 |
| **H2 — the suppressed case occurs (live)** | **fails** | 0 of 10 |
| **H3 — the judgement would fire on it** | **n/a** | no suppressed turn to judge |

## Falsification conditions, resolved

1. H1 false → not triggered; the gap is real.
2. H2 ≤ 1 in 10 → **triggered** (0 in 10) → keep the single bound. **A negative result.**
3. H2 ≥ 3 in 10 and H3 → not reached.

## The finding

- **The suppression is real but latent.** `_maybe_nudge` returns `None` whenever
  `already_nudged` is set, and the action nudge sets it. So the completion nudge — the
  second judgement, built for "a tool ran but the action is outstanding" — cannot run
  after the action nudge. On the read-instead-of-write shape the turn finalizes with no
  write and the judgement is never consulted.
- **It did not cost anything in this run.** The announce-then-not-act shape the action
  nudge exists for did not occur: the model wrote in round 1 on 9/10 turns, and the
  10th was an unreproducible no-tool-call turn (likely a failed decision call, below).
  So the completion nudge was never *needed*, and its suppression was never felt.
- **The nudge armed reliably.** `needs_action=True` on 10/10 (p 0.63–0.71), so the
  scope change shipped in `21ed399` holds up on fresh phrasings — including the
  question-form "Can you create X …?".
- **A transient decision failure silently costs the fix.** `_ask` returns `None` on any
  error and `needs_action` returns `None` without logging, so `expects_action` is
  `False`, the nudge is unarmed, and an announce-then-not-act turn is unprotected. The
  `template r1` anomaly is consistent with this. It is the best-effort contract working
  as designed — but it is invisible in the logs, which is worth knowing.

## What this proves, and what it does not

- **Proved:** the bound *can* suppress a warranted completion nudge (H1), and doing so
  is not observable in the loop's logs.
- **Not proved:** that the suppression is harmless in general. 0/10 is a probe; a rate
  under ~26% could still yield 0/10. The run used explicit imperatives, which may
  under-sample the announce-then-not-act behaviour the original failure showed.
- **Not proved:** that a per-kind bound would recover a suppressed turn (H3 unreached).
  It remains unmeasured because the case did not arise.

## Recommendation

**Keep the single bound.** Per falsification condition 2, the case it would fix did not
occur, and the pre-registered action is to leave the policy alone. The measurement is
recorded so the gap is documented rather than discovered later.

Revisit only on evidence: if the logs ever show a turn that fires the action nudge and
then finalizes without the write, the mechanism is already proven and the per-kind
bound is the fix. A cheap way to get that evidence would be to log when the completion
judgement is *skipped* because `already_nudged` is set — instrumentation, not a policy
change.

**Follow-up worth its own probe:** the transient-decision-failure path — the nudge is
silent when the decision model call fails, so the fix's coverage depends on a call that
can fail without a trace.
