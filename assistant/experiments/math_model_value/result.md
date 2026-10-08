# Result — is `sorc/qwen3.5-claude-4.6-opus` the right MATH_MODEL?

**No — keep `qwen3.8:27b`.** The candidate is ~2.6× faster and smaller, but it is
*less reliable*: it is correct on **fewer** problems than the current math model,
and the miss is a real, reproducible one.

## By hypothesis

| | Verdict | Evidence |
|---|---|---|
| **H1 — correctness (decisive)** | **fails** | candidate **7/8** vs the 27B's **8/8**. The pre-registered rule was: fewer correct → keep the 27B. |
| **H2 — latency** | **passes** | candidate avg 5.2 s/problem vs the 27B's 13.5 s (~2.6× faster; max 9.5 s vs 31.7 s). |
| **H3 — fit** | **passes** | 10 GB vs 17 GB. |

## The decisive miss

The candidate failed "sum of the squares 1..100" on every attempt because it
emitted

```python
sum(i**2 for i in range(1, 101))     # no print()
```

so stdout was empty and the computed answer never reached the system. The 27B
printed its answer (and an interest line, and a year-by-year breakdown) and was
correct on all eight. **For a `compute` tool whose whole contract is "the answer
comes back on stdout", a model that sometimes forgets to print is the wrong
model — its speed does not compensate.**

## Falsification conditions, resolved

1. Candidate correct on fewer problems than the 27B → **triggered**. Keep the 27B.
2. Candidate fails to emit a valid tool call → not triggered (it always emitted
   one).
3. Candidate slower than the 27B → not triggered (it is faster).

## Verdict

**Keep `qwen3.8:27b` as `MATH_MODEL`.** The candidate is a faster, smaller model
that is one reliability step behind on the role's core contract. The trade —
~2.6× speed for a reproducible "did not print the answer" failure — does not favor
a swap.

## What this does and does not say

- **This is the right experiment.** Executing the code and grading the answer
  reversed the earlier, shape-only probe, which had leaned "plausible math tier".
  The candidate is *fast*, but speed was never the binding constraint.
- **A mitigation exists but is unmeasured.** Instructing the model to print the
  final answer (in the compute system prompt) would likely fix the miss, and would
  change the prompt for *every* math model — so it needs its own measurement
  before it is adopted, not a quiet prompt edit to rescue one candidate.
- **The candidate remains viable elsewhere** — it passed the chat/tools mechanics
  gate and is a plausible on-demand tier — but nothing here justifies displacing a
  working model.
