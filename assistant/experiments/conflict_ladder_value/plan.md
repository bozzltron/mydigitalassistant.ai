# Experiment: Is the Conflict Ladder Doing Anything?

## Status

**PRE-REGISTERED. No data collected yet. No result may be written until this document is
committed.**

## Question

`resolve_conflict` (`confidence.py:83`) implements a decision ladder:
`source_reliability → confidence → priority → recency`. The AGM machinery in
`belief_revision.py` is built on it, and `upsert_slot` uses it to decide whether a new
value replaces an old one.

Live data says the first three rungs never fire:

```
auto_resolved conflicts:                     2,381
  where resolved_value == new_value:         2,381   (100%)
  where resolved_value == existing_value:        0
```

The hypothesis is that extraction writes every slot with `source_reliability=null` → `0.5`,
and `revise()` passes `new_source_reliability=None` → also `0.5`, so rung one never
discriminates and the last rung (recency) decides everything.

**This experiment measures whether the ladder changes any decision, or whether it is
decoration around "new wins".** That determines whether Plan D is worth building and whether
the ladder should be fed properly or simplified.

## Why this is worth measuring

Plan D proposes replacing a comparator with a model that reasons. If the comparator is
already inert, then the honest options are (a) feed it properly, or (b) admit it is
last-write-wins and simplify — but **not** keep AGM-shaped code that never runs. Code that
*looks* like belief revision is not evidence of belief revision.

Also: if the ladder is inert, "the agent almost got renamed to `grok`" (conflict 3835) was
survived by luck, not by design — and the urgency of the protected-slot guard in Plan D
Phase 3 rises.

## Hypotheses

- **H1 (inertness).** With production inputs, the first three rungs fire on ~0% of writes,
  and the decision is always recency.
- **H2 (rung-1 liveness).** With `source_type`/`source_reliability` propagated honestly
  (user-stated 0.95 vs search 0.5 vs unknown 0.5), the reliability rung fires on a
  non-trivial share of writes.
- **H3 (decision churn).** Honest propagation *changes* the decision on some share of real
  historical writes. This is the quantity that matters: it is the difference between the
  ladder being decorative and the ladder being load-bearing.
- **H4 (identity exposure).** Some share of historical writes would have overwritten a
  protected slot (`identity_name`, `essential`) had they won. This sizes the Plan D guard.

## Variables

- **Condition A (current):** replay real historical writes through `resolve_conflict` with
  the inputs production actually supplies (`source_reliability=None` → 0.5 on both sides).
- **Condition B (fed):** same writes, with each side's `source_type` and
  `source_reliability` taken from the slot row it came from.

Reported per condition: decision distribution, which rung decided, and the disagreement
rate between A and B. Plus, for H4: how many historical losers were protected slots.

## What counts as evidence

- **H3 is the primary test.** If A and B agree on **≥95%** of sampled writes, the ladder is
  decorative and I will say so.
- If B changes the decision on **≥20%**, the ladder is load-bearing and the fix is to
  propagate `source_type` through extraction — a small change with large behavioural
  consequence.
- Between 5% and 20% is reported as unresolved, not rounded to a verdict.

## Falsification conditions

1. *A and B agree on ≥95% of writes* → the ladder is inert; recommend simplifying to
   last-write-wins plus protected slots, and do not build Plan D on the promise that a
   better-fed ladder fixes anything.
2. *The reliability rung fires but changes no outcome* → the rung is real but
   non-determinative; report as such rather than as a win.
3. *H4 finds zero protected slots in the loser position* → the `grok` case is rarer than
   the narrative suggests, and the Plan D guard is cheap insurance rather than urgent.
4. *Sampling cannot reconstruct provenance for most writes* → the experiment is
   inconclusive; say so rather than extrapolating from the subset that could be rebuilt.

## Safety

Read-only, and structurally so, following `graph_walk_yield`:

- Runs against a **copy** in a scratch volume, never the live volume. The live volume is not
  mounted into the experiment container at all.
- `experiments/preflight.py` refuses to start unless: the experiment DB is not the live DB,
  the live volume is not mounted, a restorable backup exists, and the SHA-256 of the copy is
  recorded.
- Table digests (frames, slots, associations, episodes, slot_history, conflicts) are
  captured before and after and written to `result.json`, so non-mutation is verifiable
  rather than asserted.
- The verification write-up is produced **before** `result.md` is written, and names threats
  that remain rather than only the checks that passed.

## Known threats, stated now rather than after seeing results

1. **Replay is not the original write.** Reconstructing the inputs of a past conflict is an
   approximation; episodes and provenance may be missing for older rows, and those must be
   excluded and counted rather than guessed.
2. **The sample is not the population.** The self-inflicted summary rows (Plan B's 3,307)
   must be excluded from the sample, or the result measures the summarizer, not the ladder.
3. **Decision churn ≠ better beliefs.** This experiment cannot show the fed ladder produces
   *truer* memory, only different memory. That is the `graph_walk_yield` → `walk_value`
   pattern, and Plan D's labelling work is what would answer it.
4. **One corpus, one owner, one period.** The behaviour may differ on a larger or older
   brain.
