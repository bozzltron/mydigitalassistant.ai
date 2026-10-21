# Result

36 attribution generations (4 questions × 3 conditions × 3 replicates) and 40
routing classifications, against `qwen3.5:9b` (chat, temp 0.7) and `qwen3.5:4b`
(router, temp 0.0). Grading is deterministic and re-applied to stored answers; see
`verification.md` for the grader correction.

## Headline

| question | baseline | treatment | pre-registered bar | verdict |
|---|---|---|---|---|
| Does the cue list raise search on period queries? | **0.90** | **0.90** | +0.20 | **falsified — ceiling** |
| Does it over-trigger on stable queries? | 0.00 | 0.00 | ≤ +0.10 | pass, trivially |
| Stale value presented as current (A→C) | **1.00** | **0.667** | −0.50 | **missed** |
| Observation date cited (A→C) | **0.00** | **0.333** | +0.50 | **missed** |
| Dates alone, no rule (A→B) | 1.00 | **1.00** | — | **no effect at all** |

## What this says

**The router was never the problem.** Period-scoped questions already trigger
search 9 times out of 10 without the new cue list, and the single miss —
"What's on my calendar this afternoon?" — *should not* search the web: it is a
memory question. The extended cue list changed no decision. **The cue change is
reverted**; the prompt is snapshotted in the harness so the comparison stays
reproducible.

**Dating alone does nothing.** Condition B is byte-different from A only by
`as of 2026-09-05` on four slots, and its answers are A's answers, sometimes
verbatim. A date in the context is information, not a behaviour. Any future claim
that "we show dates now" is not a fix.

**The rule is a partial mitigation that missed its bar.** A→C moves the failure
rate 1.00 → 0.667 and dating 0.00 → 0.333: direction right, size short of the
pre-registered 0.50. Twelve of twelve A answers assert a 45-day-old value as
current; eight of twelve still do under C. Fisher exact on that split is
p ≈ 0.093 — suggestive, not established.

**Where it works and where it does not** (raw answers, `verification.md` §4):

| question | C result |
|---|---|
| NASA image of the day | **fixed** — "Based on my records from September 2026, … was a photograph of an Apollo 11 spacewalk." |
| Acme Widget price | **fixed** — "… costs $412.00 USD, based on information from September 5, 2026." |
| Acme Widget version | not fixed — "The latest version is 9.7.3." |
| my schedule this afternoon | not fixed — "You have a dentist appointment at 3:30 PM this afternoon." |

The two failures share a shape: the fact is the **user's own stored record** or a
bare attribute, not a "thing with a changing value" the question names. The rule
as written asks the model to notice period scope in the *question*; it does not
make a stored record carry a validity window.

## Decision

Per the pre-registered falsification, the prompt-only fix **did not clear the
bar**, so it is not reported as the fix. What ships:

- **Router cue list: reverted.** Measured no-op, and its one miss was correct
  behaviour.
- **Observation date on each memory slot: kept.** Neutral on behaviour (B = A),
  but it is the correct representation — the prompt was presenting undated
  claims — and it is the prerequisite for the next step. Cost +18 chars/slot,
  72 chars on four.
- **Time-validity rule: kept, labelled a partial mitigation.** It fixes the
  reported case and moved the aggregate by a third without measured harm. It is
  not sufficient on its own; anyone reading this should not treat "the rule is
  in the prompt" as solved.

## Next step (pre-registered in the plan, not taken here)

The remaining failures are claims that need a **validity window**, not wording:
`valid_as_of` (or `volatile: true`) on the slot, set at write time when the
extracted claim is period-scoped, and rendered as `valid for 2026-09-05` rather
than `observed 2026-09-05`. That makes "my schedule this afternoon" checkable
against the date instead of asking the model to infer intent from the question.

The experiment to run before building it: seed the same facts *with* a validity
window and measure A→C again with the same bar. If the window does not beat 0.50,
the failure is the model's, not the representation's, and a larger-n run of this
harness is the honest next step rather than more schema.

## Threats that remain

- **Sample size.** 12 observations per condition. A 0.333 effect is not
  distinguishable from noise at this n (Fisher p ≈ 0.093). The result is
  "did not clear a deliberately coarse bar", not "proven ineffective".
- **Synthetic memory.** Four slots, one frame each, no competing facts. Real
  retrieval returns several frames and the model must pick; attribution may be
  worse or better there.
- **The `cites_date` regex credits any date.** An answer mentioning the current
  date while repeating the old value would be scored as attributed. This
  understates the failure rate, so it errs against the hypothesis.
- **Prompt-side effects are unmeasured.** The rule could make the model hedge
  where it should be direct; nothing here tests that.
