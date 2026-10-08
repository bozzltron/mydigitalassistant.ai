# Result — which model is best for each role?

Like-for-like Q4 comparison (the `sorc/qwen3.5-claude-4.6-opus-q4` family against
the incumbents). One swap is justified; the other two are not.

| role | incumbent | candidate (Q4) | verdict |
|---|---|---|---|
| **chat / tools** | `qwen3.5:9b` | `q4:9b` | **tie** — tools ✓, JSON ✓, ~40 tok/s both. Quality unmeasured → keep. |
| **utility** | `qwen3.5:4b` | `q4:4b` | **candidate worse** — 4/7 vs 7/7 facts extracted → keep `qwen3.5:4b`. |
| **math** | `qwen3.8:27b` | `q4:9b` | **candidate wins** — 8/8 both, but 2–4 s vs 7–50 s and 6.6 GB vs 17 GB. |

## The quantization finding

The earlier experiments used `sorc/qwen3.5-claude-4.6-opus:latest`, which is
**Q8_0** — a heavier build than the Q4 incumbents. That made it look slower (26 vs
40 tok/s) and less reliable (7/8 math). The `-q4` family removes both confounds:

- At equal quant, the candidate runs **~40 tok/s — identical to `qwen3.5:9b`**.
- At equal quant, the candidate is **8/8 on math** (the Q8 build was 7/8: it
  emitted `sum(i**2 …)` with no `print`). The miss was the quant, not the model.

## Verdict

- **Keep `qwen3.5:9b` for chat/tools.** The candidate ties mechanically; a swap
  needs a *quality* win that has not been measured. Churn without evidence.
- **Keep `qwen3.5:4b` for utility.** The candidate extracts fewer facts; this is
  the utility model's whole job.
- **Switch the math model to `sorc/qwen3.5-claude-4.6-opus-q4:9b`.** It matches the
  27B's accuracy on the probe, is ~4–10× faster per problem, and is 6.6 GB instead
  of 17 GB — a large RAM win on the box. `MATH_MODEL` is empty by default, so this
  is a recommendation for whoever enables it, not a change to the shipped default.

## Falsification conditions, resolved

1. Chat tool gate / slower → **not triggered** (ties).
2. Utility extracts fewer → **triggered** (4/7 < 7/7). Keep `qwen3.5:4b`.
3. Math solves fewer → **not triggered** (8/8 = 8/8). Candidate wins on speed/size.

## What is still unmeasured

- **Chat quality.** The only thing that would justify a chat swap; the mechanics
  are a tie.
- **Hard math.** Eight easy problems tie; the 27B's advantage, if any, is on the
  problems this probe did not ask.

## Adopted on trial (2026-10-08)

`MATH_MODEL` was set to `sorc/qwen3.5-claude-4.6-opus-q4:9b` in both dev and prod
(the shared `.env`) and verified end-to-end:

- `/health` now reports it as `math` (the health fleet was missing the role).
- A direct `execute_python("What is 17% of 2,480?")` from inside the dev container
  returned `421.6`.
- A live chat turn emitted a `compute` tool call and answered
  "17% of 2,480 is exactly **421.6**".

Rollback is one line (`MATH_MODEL=qwen3.8:27b`) plus `docker compose up -d` in each
project. Chat and utility are unchanged.
