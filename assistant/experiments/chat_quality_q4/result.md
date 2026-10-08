# Result — does the Q4 candidate write *better* answers than `qwen3.5:9b`?

**No quality win was demonstrated.** The candidate is never judged worse, but it is
mostly judged *equal*, and the judge is too noisy to call the two decided pairs a
pattern. Per the pre-registered bar, the incumbent stays.

## By hypothesis

| | Verdict | Evidence |
|---|---|---|
| **H1 — quality win** | **not demonstrated** | 5 ties, 2 candidate, 0 incumbent, 3 judge-flips over 10 prompts. |
| **H0 — no win** | **holds** | The candidate is not clearly preferred; 8 of 10 pairs are ties or flips. |

## Verdict

**Keep `qwen3.5:9b` for chat/tools.** Combined with the fleet comparison — a
mechanical tie (tools, JSON, ~40 tok/s) and no measured quality win — there is
nothing to justify a swap. The candidate is a *peer*, not an improvement.

## Falsification conditions, resolved

1. Win rate ≤ 50% → the *decided* rate is 100% (2/2), but only 2 of 10 pairs were
   decided; the honest reading is "not clearly above 50%", so **no win**. Keep.
2. Judge flips with position on most pairs → **partially**: 3 of 10 flipped. Not
   most, but enough that the decided pairs are thin. Reported, not hidden.

## How to strengthen it (if a chat swap is ever on the table)

The experiment *works*; its weakness is judge noise and sample size. To make a
chat-quality verdict trustworthy:

1. **More prompts** (30–50), so 2/2 decided becomes a rate with a confidence
   interval rather than two data points.
2. **More judges, and a rubric.** Have 2–3 different-family judges score each
   answer 1–5 on correctness/helpfulness/clarity against a reference, and average
   — a rubric is more stable than a single pairwise pick, and multiple judges
   dilute any one model's bias.
3. **A position-bias control** already exists (the swap); keep it and report the
   flip rate as a first-class number, as this run does.
4. **Task-grounded prompts** with checkable answers (factual QA with known keys,
   arithmetic, constraint-following) where the winner is not a matter of taste —
   these are the pairs a judge can decide reliably.

None of that changes the current decision; it would only matter if a future
candidate looked promising enough to warrant the larger run.
