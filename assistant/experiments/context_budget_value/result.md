# Result — does the context budget earn its place?

**Yes, both halves — and the run reversed the pre-run lean.** I expected the
aggregate cap (T3) to be a solution to a problem we did not have. It is not: it
fires the moment a turn genuinely needs two large results, which is exactly the
turn that would otherwise overflow the window.

## By hypothesis

| | Verdict | Evidence |
|---|---|---|
| **H1 — safety** | **holds** | Peak prompt 7,026–8,194 tokens against a 16,384 window on every turn; `truncated=False`. |
| **H2 — aggregate (decisive)** | **holds, when exercised** | `tool_results_dropped=1` on the two-CSV comparison; `0` on single-read turns. The cap is load-bearing. |
| **H3 — binding** | **holds for reads; listings unexercised** | A ~200k-char CSV is capped to the 28,260-char allowance. |

## What this means

- **Do not remove T3.** The pre-registered falsification condition #1 ("H2 = 0
  across the set → delete the eviction") did **not** trigger once the aggregate
  was fairly exercised. A single-read turn never fires it; a two-file turn does.
- **The budget adapts as designed.** `long_history` shrank the allowance from
  28,260 to 10,288 chars — the fixed cost (6 × 3,000-char history turns) ate the
  space, which is the whole point of deriving rather than fixing it.
- **The aggregate's value is conditional on task shape.** It fires on multi-file
  comparisons, not on single reads. The mechanism is justified; its *frequency*
  is a separate question this probe did not measure.

## Bug surfaced (not part of the hypotheses)

`tool_result_chars` (29,452) exceeded the allowance (28,260) on `read_big_csv`.
The cap applies to the file *content*, but `format_tool_result` renders
`str(result.data)`, whose dict overhead (~1,200 chars: frame ids, sizes, totals)
pushes the actual result over the allowance. So the per-result cap is not a hard
cap on what enters the prompt — small, but real. Fix: budget the rendered result,
not just the content.

## Falsification conditions, resolved

1. H2 = 0 → **not triggered** (dropped=1 when exercised). T3 stays.
2. H1 wide margin + H3 never binds → **partially**: H1 has margin, but H3 binds
   for reads, so the budget is doing work. Not a simplify trigger.
3. H1 fails → **not triggered** (no truncation).

## What is still unmeasured

- The round ceiling (6 / 12) — fixed at the base here; no evidence it is right.
- The aggregate's frequency across real usage.
- The listing cap (out of scope; covered by unit tests).

## Recommendation

Keep T2 and T3. The remaining throughput work (T4: de-duplicate the prose tool
list, token-bound history) is unaffected — and T4's history bound is now
*supported* by this run, since `long_history` showed history eating two-thirds of
the allowance.
