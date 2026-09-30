# Experiment: Can the Agent Reason About Its Own Conflicts?

## Status

**PRE-REGISTERED. No data collected yet. No result may be written until this document is
committed.**

## Question

Plan D proposes giving the agent scheduled time to reason about conflicting beliefs, and to
adopt what it concludes. Its premise is that **a model reasoning over evidence beats a
comparator applying a rule.**

That premise is a claim about capability, and it has never been tested. The current
mechanism is a comparator: `resolve_conflict` (`confidence.py:83`) ranks two strings and
picks one. Measured on the live brain, it does not even do that — 2,381 of 2,381
auto-resolved conflicts (100%) went to `new_value`, because extraction writes everything at
`source_reliability=0.5` and the first rung never discriminates.

**This experiment measures whether the model produces correct verdicts on real conflicts,
and whether it beats the trivial alternative.** If it cannot beat "always adopt new", Plan D
is sophistication with no return and must not be built.

## Relationship to `conflict_ladder_value`

They are sequential, and both are needed.

- **`conflict_ladder_value`** (automated, no labels) answers *"is the existing mechanism
  doing anything, and how exposed are protected slots?"* It establishes whether there is a
  meaningful baseline to beat, and it sizes the `grok` risk.
- **This experiment** (requires labels) answers *"can reasoning actually do better?"*

Run `conflict_ladder_value` first. It is fully read-only and needs no manual effort, and it
tells us whether the second experiment is worth the labelling cost.

## Why this is worth measuring

The alternative readings, all plausible before measurement:

1. The model reasons well and beats the comparator — Plan D is worth building.
2. The model reasons poorly and is worse than "always adopt new" — Plan D would *degrade*
   memory while looking sophisticated.
3. The model reasons plausibly but defaults to the newer value anyway (recency bias in the
   model, mirroring the comparator) — Plan D would add cost and no accuracy.
4. The cases are genuinely ambiguous and the model correctly escalates — Plan D's value is
   in *triage* (knowing what needs a human), not in resolution. This would be a different,
   still-useful finding.

Reading 4 is the one most likely to be missed by a plan that assumes resolution is the goal.
It is therefore an explicit outcome here, not a failure mode.

## Hypotheses

- **H1 (accuracy).** On a hand-labelled sample of real conflicts, the reasoning pass produces
  a correct verdict meaningfully more often than "always adopt new".
- **H2 (discrimination).** The model is not simply recency-biased: it adopts the *existing*
  value on a non-trivial share of cases where that is correct.
- **H3 (calibration).** `ESCALATE` correlates with genuine ambiguity — the cases it declines
  are the hard ones, not a random or lazy subset.
- **H4 (evidence sensitivity).** Verdict quality improves when the evidence bundle (slot
  history + source episodes + provenance) is provided, versus values alone. This tests
  Plan D Phase 1's premise directly.

## Variables

- **Baseline:** "always adopt new" — accuracy of the trivial rule on the same sample.
- **Condition A:** reasoning pass with values + provenance only.
- **Condition B:** reasoning pass with the full evidence bundle (Plan D Phase 1 as specified).
- Reported per condition: verdict distribution, accuracy against labels, ESCALATE rate,
  and per-case disagreement with the baseline.

## What counts as evidence

- **H1 is the primary test.** The bar is stated in advance: **if the reasoning pass does not
  beat "always adopt new" by at least 15 percentage points on the labelled sample, Plan D is
  not built.**
- **H4 is the secondary test.** If B is no better than A, Plan D's Phase 1 evidence assembly
  is unnecessary work and should be cut.
- **H3 must be examined, not assumed.** An ESCALATE rate above ~60% means the agent is
  triaging rather than resolving — report that as the finding, and reconsider whether Plan D
  is a resolution task or a routing task.

## Falsification conditions

1. *Reasoning accuracy ≤ baseline + 15pp* → Plan D is not built. The comparator stays, and
   the honest follow-up is to fix the ladder's inputs (thread `source_type` through
   extraction) rather than to add a reasoning layer.
2. *The model adopts the newer value regardless of evidence* → recency bias; the reasoning
   pass is a more expensive comparator. Not built.
3. *Condition B ≈ Condition A* → evidence assembly is not earning its place; Plan D Phase 1
   is cut.
4. *ESCALATE dominates (>60%)* → the task is triage, not resolution. Rewrite Plan D as a
   routing task and re-budget it, rather than shipping it as designed.
5. *Labelling is impossible to do reliably* (even the human cannot say which is correct) →
   the sample is genuinely ambiguous and the correct behaviour is escalation, not
   resolution. A finding, not a failure.

## Sample and labels

1. Draw from **genuine** conflicts only — Plan B's expected ~277 pending, after the
   self-inflicted summary rows are removed. Sampling from the current queue would measure
   the summarizer, not the reasoning.
2. **Hand-label each case**: `existing_was_right | new_was_right | ambiguous`. The third
   label is required and must be used honestly — a two-way label would force a guess on
   genuinely undecidable cases and bias the accuracy figure.
3. **The labeller must not be the model under test.** Labels are human judgement.
4. Report the sample size and the label distribution; a sample that is 90% one label cannot
   support the conclusion.

## Safety

Read-only, against a **copy** in a scratch volume, exactly as `graph_walk_yield`:

- The experiment DB path must not be the live DB; the live volume must not be mounted.
- `experiments/preflight.py` **refuses to start** unless: experiment DB ≠ live DB, live
  volume not mounted, a restorable backup exists, and the copy's SHA-256 is recorded.
- Table digests (frames, slots, associations, episodes, slot_history, conflicts) captured
  before and after and written to `result.json`, so non-mutation is verifiable rather than
  asserted.
- `verification.md` is produced **before** `result.md`, and names threats that remain rather
  than only the checks that passed.
- **No verdict is applied.** This measures verdict quality; nothing is written to the brain
  under test or the live brain.

## Known threats, stated now rather than after seeing results

1. **The labelling is subjective and small.** A few dozen cases judged by one person is
   enough to detect a large effect, not to make a fine-grained claim. Say so in the result.
2. **The sample is the easy end of the distribution.** The 277 surviving conflicts are
   whatever the comparator and extraction produced; they may not represent conflicts the
   system would face later, including ones with richer evidence.
3. **The model under test may change.** Ollama's non-determinism means runs are not
   bit-reproducible; accuracy should be reported with the variance across repeats, not as a
   single figure.
4. **Accuracy ≠ better memory.** A verdict can be more accurate in this experiment and still
   produce a worse brain if applied at scale (e.g. by overwriting a rarely-exercised fact
   that was right). This bounds the value; it does not book it — the same limit
   `graph_walk_yield` recorded toward `walk_value`.
5. **The human labeller is not ground truth.** They may be wrong about the user's world.
