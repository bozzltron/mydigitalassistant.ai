# Result — is the opus Q4 9B a better MAX model than the 27B?

**No. Keep `qwen3.8:27b`.** On hard, multi-step reasoning the candidate is **3
problems worse (6/10 vs 9/10)** and only **1.25× faster on the median** — not the
2× a swap required. The candidate is 30% less accurate on exactly the work the MAX
tier exists to do.

## By hypothesis

| | Verdict | Evidence |
|---|---|---|
| **H1 — accuracy within 1** | **fails** | 6/10 vs 9/10 — **3 worse** |
| **H2 — ≥2× faster (median)** | **fails** | 1.25× (mean 2.26×, but see below) |

## Falsification conditions, resolved

1. Candidate ≥ 2 worse → **triggered** (3 worse) → keep `qwen3.8:27b`.
2. (within 1 AND ≥2× faster) → not met.
3. (within 1, not faster) → n/a.

## The finding

- **The incumbent never gave a wrong answer.** Its single failure was a 360s
  `ReadTimeout`; it answered the other nine correctly. The candidate gave one
  wrong answer (242 → 262) and left three unanswerable.
- **The speed case does not hold up.** The median (the pre-registered, robust
  metric) is 1.25×, and on the problems both models completed it is ~1.1×
  (41.1s vs 36.7s). The 2.26× *mean* is an artefact of the incumbent's two retried
  calls and one timeout — infrastructure, not the model.

So the hypothesis that motivated this — "the opus 9B matches the 27B and is much
faster, so it should be MAX too" — does **not** survive the measurement. It matched
the 27B on *eight easy* math problems (the earlier probe); on hard reasoning it
does not, and it is not meaningfully faster.

## Why the earlier probe misled

`model_fleet_q4` put the candidate in the **math** role on 8 easy problems where
both scored 8/8. Easy problems have a ceiling: they cannot separate a 9B from a 27B.
The math role is narrow, checkable codegen; the MAX role is open-ended hard
reasoning. Same model, different job — and only one of them is measured.

## Verdict

- **MAX stays `qwen3.8:27b`.** No `.env` change.
- **The speed motivation is already addressed elsewhere.** The reason the 27B felt
  catastrophic was that *mechanical* turns were being escalated to it — the
  `escalation_trigger` finding. With that fixed, only genuinely hard turns reach
  the 27B, which is where the accuracy is worth the wait.
- **Do not re-propose the swap** without a probe that shows the candidate matching
  the 27B on *hard* reasoning. A 9B matching a 27B on easy problems is expected;
  on hard ones it does not, here.

## Recorded

`docs/MODEL_SELECTION.md` — MAX re-assessment row and rationale.
