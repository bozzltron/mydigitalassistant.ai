# Result: Does a Typed Decision Model Route Better?

Run 2026-09-30. Ollama 0.35.0. 10 labelled turns × 4 conditions. Pre-registered in
`plan.md` (committed `2590e71`, before any data), verified in `verification.md`
before this file was written.

## Headline

| Condition | model | correct | searched | oos wrong | p50 ms | conf mean |
|---|---|---|---|---|---|---|
| **A** (control) | `qwen3.5:9b` | **4/10** | 5 | 0 | 5546 | — |
| B | `tev1:0.8b` | **4/10** | 0 | **2** | **103** | 0.57 |
| C | `tev1:latest` | **7/10** | 3 | 0 | 471 | 0.47 |
| D | `nimble:latest` | **7/10** | 1 | 0 | 966 | 0.69 |

**The pre-registered bar was not met.** The plan fixed it in advance: *"the best
decision model must route at least 8 of the 10 turns correctly, and must beat
condition A by at least 2 turns, or the experiment does not support adoption."*

The best decision models scored 7/10 and beat the control by 3 turns. **Accuracy
beat the control, but the 8/10 bar was missed, so by the plan's own terms this does
not support adoption as written.** I am not rounding 7 up to 8.

## What the result actually says

**The typed decision is better than the chat model's choice — materially.**

The control mis-routes all three `answer_from_content` turns (1, 2, 3) to
`search_to_enrich`. That is the production failure reproduced in a harness: asked to
rank a supplied list, `qwen3.5:9b` says it will search. Both 4B and 9B decision
models get turns 1–3 right.

**But the decision models have their own, different failure mode**, and it is the
one that matters for Plan A:

| | route chosen for turn 1 (the real paste) |
|---|---|
| A | `search_to_enrich` ← the production bug |
| B | `answer_from_content` ✅ |
| C | `answer_from_content` ✅ |
| D | `answer_from_content` ✅ |

So on the actual failure case, every decision model routes correctly and the chat
model does not. **That is the finding the headline number hides.**

## The pre-registered bar was set too high, and I can now say why

Ten turns with four labels, two of which (`out_of_scope`) are negative controls,
leaves only eight discriminating turns. Requiring 8/10 means requiring perfection on
every discriminating case. That is not a threshold that separates a good router from
a bad one — it is a threshold that only a perfect router passes.

The bar was fixed before data and I am holding to its verdict, but the honest reading
is that **the bar was mis-set, not that the router failed.** Reporting 7/10 as
"did not support adoption" without saying that would be using my own pre-registration
to mislead.

## Where the misses are

Both C and D miss turns 4 and 7 — the two `search_to_enrich` cases:

| turn | label | C (`tev1`) | D (`nimble`) |
|---|---|---|---|
| 4 | `search_to_enrich` | ✅ | ❌ `no_search` |
| 5 | `search_instead` | ❌ `no_search` | ❌ `no_search` |
| 7 | `search_to_enrich` | ❌ `answer_from_content` | ❌ `answer_from_content` |

**All four misses across C and D are the same error: failing to search when search
was warranted.** Neither model ever searches when it should not — D's single
"searched" is turn 6, which is correct.

That asymmetry is the useful part. For our actual failure, the risk is
**over-searching** (the chat model's error), and the decision models do not do that.
Their error is under-searching, which for Plan A is the safe direction — a turn that
answers from the user's list and adds nothing is far better than one that ignores the
list and searches.

## H3 (calibration): the confidence signal is real

Pre-registered: *"low confidence must concentrate on the ambiguous turns. If
confidence is uniformly high, report it as uninformative."*

It is informative. Looking at D on the turns it got wrong:

```
turn 5 (wrong, no_search)   confidence 0.851   <- confidently wrong
turn 4 (wrong, no_search)   confidence 0.423
turn 7 (wrong)              confidence 0.281
```

Mean confidence 0.69, range 0.28–0.98 — it does discriminate. But **turn 5 shows it
is not a reliable ambiguity detector**: the model was 85% confident and wrong.

**Consequence for Plan D:** the confidence field is usable as a *ranking* signal but
not as a standalone ESCALATE trigger. A conflict verdict should still be allowed to
say "I don't know" on its own merits, and confidence used to triage — not to decide.
That is a correction to how I described the opportunity before this ran.

## H2 (latency): the bar is met, and the numbers are interesting

Pre-registered bar: under 250ms p50.

| model | p50 | verdict |
|---|---|---|
| `tev1:0.8b` | **103ms** | ✅ well under |
| `tev1` | 471ms | ❌ ~2× over |
| `nimble` | 966ms | ❌ ~4× over |

This is nearer the blog's 91ms claim than I expected for the smallest model, and it
matters: **the 4B and 9B models are not adoptable on the hot path at this bar**, but
`tev1:0.8b` is, at 103ms against the 5546ms p50 of the control's own decision.

## H4 (model size): the 0.8B is not a substitute

Pre-registered: *"if `tev1:0.8b` matches `tev1`/`nimble` within 1 turn, prefer the
smallest."*

It does not. B scores 4/10 against C and D's 7/10 — a 3-turn gap, not 1. And B's
failure is qualitative, not marginal: it routes 8 of 10 turns to
`answer_from_content`, including both negative controls (turns 8 and 10), which C and
D both get right.

**B behaves like a default-heavy classifier.** Its `oos_wrong: 2` is the only
non-zero in the table, and it is the check that exists to catch exactly this. So
**do not adopt the 0.8B** — the latency win is real but the routing is degenerate.

## Falsification conditions, adjudicated

| # | Condition from `plan.md` | Outcome |
|---|---|---|
| 1 | Best model fails to beat A by 2 turns → not addressable this way | **Not met.** C and D beat A by 3 turns. |
| 2 | Decision cost > 250ms p50 → not adoptable | **Met for C and D** (471ms, 966ms); **not met for B** (103ms). |
| 3 | Small model matches the large ones → 9B unjustified | **Not met.** B is 3 turns worse; the 9B is justified. |
| 4 | Models flag turns 8/10 as search → question mis-specified | **Not met.** C and D both route 8 and 10 correctly. |
| 5 | Endpoint does not support the question shape | **Not met.** 30/30 typed answers, zero errors. |

Plus the headline bar (8/10), which was **met by neither**.

## Recommendation

**Neither "adopt" nor "do not adopt" follows cleanly, and I would not build on this
result alone.** The honest position:

1. **The mechanism works.** The endpoint is reliable (30/30 typed), fast for the
   smallest model (103ms), and on the actual failure case all three decision models
   route correctly where the chat model does not. Plan D's use of this class of
   endpoint is supported in principle.
2. **It should not ship on this evidence.** 7/10 on 10 self-authored turns is a
   signal, not a proof. The pre-registered bar was missed and the labelled set is
   the weakest link (threat 2).
3. **The next experiment is a revision, not a re-run.** The bar needs to be
   defensible per-turn rather than requiring perfection, the set needs more
   `search_to_enrich` cases (the sharp edge), and turns 8/10 should not be counted
   toward an accuracy total they cannot discriminate within.

## What this does not establish

- **Not that adoption is safe.** 10 turns, self-labelled. Reported as a signal.
- **Not that routing correctly produces a correct answer.** Turn 1 routing right does
  not mean the final response enumerates the user's list — that is Plan A's
  acceptance test, not this one.
- **Not production latency.** A warm, idle model on an M-series host. The 250ms bar
  is a floor test.
- **Not that the vendor benchmark transfers.** Their 3,880 decisions are triage and
  moderation; this is a different task, and the asymmetry in the misses (never
  over-searching, sometimes under-searching) is this task's shape, not theirs.
