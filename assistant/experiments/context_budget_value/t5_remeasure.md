# T5 re-measure — did T4 lower the fixed cost?

Re-ran `experiment.py` on the same fixed turn set after T4 (history bound + prose
dedup). Baseline is `result_pre_t4.json` (pre-T4); current is `result.json`.

| scenario | fixed chars | content allowance | peak tokens | truncated | dropped |
|---|---|---|---|---|---|
| `read_big_csv` | 20,891 → **20,253** | 28,260 → **28,896** | 8,194 → 8,194 | False | 0 |
| `compare_two_csvs` | 20,922 → **20,284** | 28,228 → **28,868** | 8,194 → 8,194 | False | 1 |
| `long_history` | 38,864 → **35,226** | 10,288 → **13,924** | 7,007 → **6,459** | False | 0 |

## What moved, and why

- **Prose dedup.** Every turn's fixed cost dropped by ~638 chars (~160 tokens) —
  the stale prose tool list is gone; the schemas are the contract. The content
  allowance rose by the same amount.
- **History bound.** The memory-heavy turn's fixed cost dropped by **3,638 chars
  (~910 tokens)** and its allowance rose **35%** (10,288 → 13,924), because
  history is now bounded by size rather than by turn count. Its peak fell 548
  tokens.
- **No regression.** No turn reaches the window (`truncated=False` everywhere),
  and the aggregate cap still fires (`dropped=1`) on the two-file comparison.

## Verdict

T4 delivered what it promised: a lower fixed cost, a larger content allowance, and
a lower peak on the memory-heavy turn — with the safety properties intact. The
remaining fixed cost is dominated by the tool schemas (~17.8k chars), which are
the contract, not duplication.

## Threats

Same as `verification.md`: one model, a synthetic corpus, ÷4 char→token estimates.
The harness now applies the production history bound (`Orchestrator._bounded_history`)
so the history change is visible; it still does not run the full `_run_turn`
(retrieval, search), only the tool loop.
