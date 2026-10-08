# Result — can `sorc/qwen3.5-claude-4.6-opus` replace the chat and math models?

**It passes the mechanics gate and is a competent middle tier — but it is *slower*
than the warm chat model, so the honest answer is "not as a warm-chat swap"; it is
a candidate for an on-demand tier, pending a quality measurement this probe does
not provide.**

## By hypothesis

| | Verdict | Evidence |
|---|---|---|
| **H1 — tools (the gate)** | **pass** | Emits a valid `tool_calls` entry on the tool prompt. It can take the chat/tools role. |
| **H2 — latency** | **fails for the warm role, but confounded by quant** | ~26.6 tok/s vs the chat baseline's ~40.2; ~2.8× *faster* than the 27B. The gap is largely quantization (below), not capability. |
| **H3 — math/codegen** | **pass (one prompt)** | Produces correct Python (`sum(i*i for i in range(1,101))`); so does the 27B. |
| **H4 — fit** | **pass** | 10 GB vs the 9b's 6.6 GB — warm set 10.6 → ~14 GB, fits 48 GB. |

## Falsification conditions, resolved

1. Tool probe fails → **not triggered** (passes).
2. Math/codegen worse than the 27B → **not triggered** on the one prompt (both
   correct); harder problems unmeasured.
3. Warm latency above budget → **triggered**: the candidate is ~1.5× slower than
   the warm 9b for the same role, so it belongs in an **on-demand tier** (max /
   math), not the warm chat set.
4. Warm set no longer fits → **not triggered**.

## Decision

- **Do not swap the warm chat/tools model.** On mechanics and latency alone the
  candidate is strictly slower than `qwen3.5:9b` for the same job, and this probe
  finds no compensating signal.
- **The candidate is a plausible `max` / `math` tier.** It is ~2.8× faster than
  the 27B and produced correct code; the 27B's advantage would be quality on hard
  problems, which is exactly what was not measured.
- **The 10 GB it costs** is real but affordable (warm set 10.6 → 14 GB).

## Quantization confound (recorded)

The candidate ships **only as Q8_0** (10.70 GB). Both baselines are **Q4_K_M**
(`qwen3.5:9b` 6.59 GB, `qwen3.8:27b` 17.74 GB). Q8 is ~1.6× the bytes of Q4 and
typically ~1.5–2× slower per token, so the 26.6 vs 40.2 tok/s gap is **largely a
quant difference, not a capability one**. `:latest` and `:9b` are the same build
(identical config digest); the other tags (`:4b`/`:2b`/`:0.8b`) are smaller
*models*, not a lighter quant of this one. So a chat swap means running Q8 —
genuinely slower than the current Q4 9B — unless a Q4 build is made.

This weakens the "do not swap" verdict from "the candidate is a slower model" to
"the candidate is a heavier build; its per-token cost is not comparable to a Q4
baseline." The math verdict is unaffected (see that experiment).

## What is still unmeasured — the decisive piece

**Answer quality.** Every signal here is mechanical. Whether the candidate writes
better answers (or better reasoning) than the 9b is the question that decides a
chat swap, and it needs a different experiment: a small set of representative
prompts graded against the baseline. Until that exists, swapping chat trades a
measured latency loss for an unmeasured quality gain — which is the trade the
data-driven rule says not to make blind.
