# Result: Is the Conflict Ladder Doing Anything?

Run 2026-10-01. Read-only against a brain copy (`SHA-256 217f9a94816dd202…`,
byte-identical to the live DB at snapshot time). Pre-registered in `plan.md`,
verified in `verification.md` before this file was written.

## Headline: **inconclusive, by the plan's own falsification condition 4**

The plan fixed this outcome in advance:

> *Sampling cannot reconstruct provenance for most writes → the experiment is
> inconclusive; say so rather than extrapolating from the subset that could be
> rebuilt.*

**Provenance cannot be reconstructed for 100% of the sample.** `slot_history`, which
records every belief change including a matching `old_value`/`new_value` pair for all
556 conflicts, **has no `source_reliability` column**. The reliability a conflict was
decided with is simply not retained anywhere.

So the primary test (H3: does honest propagation change the decision?) cannot be run
on historical data. I am reporting that rather than the number a broken harness
produced.

## What the run actually produced, and why it is wrong

The harness first reported **189 of 277 auto-resolved conflicts (68%) contradicting
what the ladder would decide** — a dramatic "the ladder is being bypassed" finding.
It is an artifact.

The harness read each side's provenance from the slot row as it exists *now*. Conflict
1 is the demonstration:

```
conflict 1:  existing='Assistant'  new='Luna'   resolved='Luna'
slot now:    full_name = 'Echo'  (source_type=user_correction, reliability=0.99)
slot_history: … 'Echo'→'grok' (revise), 'Echo'→'Echo' (expand) …
```

The harness attributed `Echo`'s 0.99 reliability to `Assistant`, three value-changes
earlier. Every "disagreement" of that shape is the harness comparing a decision to
inputs from a different point in time.

**This is plan threat 1, stated before the run and then ignored while building:**
*"Replay is not the original write."* The threat was written down and the harness went
ahead anyway. Worth recording as a process failure, not just a technical one.

## What the data does support

Two things, both arithmetic rather than replay:

**1. `resolve_conflict` with production's inputs is recency by construction.**

```
both sides rel=0.5, conf=0.5, prio=0.5  →  new_wins
```

`revise()` passes `new_source_reliability=None`, which defaults to 0.5. When the
existing side is also 0.5, rungs 1–3 all tie and rung 4 (recency) decides. That is not
a bug — it is what a tiebreak is for. The earlier reading of "100% new-wins" is
therefore **consistent with ties, not evidence of a bypass.**

**2. Extraction *does* write provenance, so the ladder has real inputs at write time.**

```
search            n=2197  null_reliability=0
scheduled_task    n=124   null_reliability=0
web_fetch         n=67    null_reliability=0
user_correction   n=16    null_reliability=0
None (untyped)    n=1143  null_reliability=206
```

This **contradicts a premise of the plan**, which asserted that "extraction writes
every slot with `source_reliability=null`". It does not: the main extraction paths
write it. The 206 nulls sit overwhelmingly on untyped slots.

So the honest state of the original question — *is the ladder doing anything?* — is
**still open**, and the reason is now precise: **we do not record the inputs, so we
cannot audit the decisions.**

## Falsification conditions, adjudicated

| # | Condition from `plan.md` | Outcome |
|---|---|---|
| 1 | A and B agree ≥95% → ladder inert | **Not adjudicated.** The A/B comparison is invalid (see above). |
| 2 | Reliability rung fires but changes no outcome | **Not adjudicated.** |
| 3 | H4 finds zero protected slots in the loser position | **Partly.** 47 conflicts involve `identity_name`/`full_name`. This is not zero, and the `grok` row is among them. |
| 4 | Provenance unreconstructable → inconclusive | **MET.** 100% unreconstructable. This is the outcome. |

## Recommendation

**Do not build Plan D on this.** The gating experiment did not produce the evidence it
was written to produce, and Plan D's justification ("a comparator cannot reason, a
model can") rests on the comparator's behaviour being understood. It is not.

The cheapest thing that would settle it is **one column**: persist
`existing_source_reliability` and `new_source_reliability` on the `conflicts` row at
write time. Every future decision is then auditable, and this experiment becomes a
query. That is a much smaller change than Plan D, it improves the brain's own
accountability, and it makes the next attempt at this question possible.

That is the recommendation: **instrument the decision, then re-ask.**

## What this does not establish

- **Not that the ladder is inert.** The 100% new-wins figure is consistent with ties.
- **Not that the ladder works.** No decision was successfully audited.
- **Not that Plan D is unwarranted** — only that this experiment did not warrant it.
- **Not a defect in `resolve_conflict`.** Nothing here shows the code misbehaves;
  what it shows is that we cannot see what it decided or why.
