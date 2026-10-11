# Experiment: which models make a good extraction council?

## Status

**PRE-REGISTERED. No data collected yet. No result may be written until this
document is committed.**

## Question

The learning path is a **judgement** — extract facts from a turn — and a miss becomes a
**stored belief**, the durable asset. A council of models could catch misses a single
extractor makes, but only if their errors are **decorrelated**: the Shuttle's four GPCs
run the same software, so they cannot out-vote a shared bug.

**The local fleet is almost one lineage.** `qwen2.5`, `qwen3.5`, `qwen3.6`, `qwen3.8`,
and the `sorc/qwen3.5-claude-4.6-opus-q4` fine-tunes are all Qwen-family. A council
drawn from it may be four GPCs. So:

1. **Is there a gap?** How often does the incumbent extractor (`qwen3.5:4b`) miss a fact
   a human would extract?
2. **Which models make a good council?** Which combination maximizes the union of facts
   caught, at acceptable disagreement — and does a **different-lineage** model add
   diversity a same-lineage one does not?

## Hypotheses

- **H1 (the gap).** The incumbent misses **≥ 1 in 10** facts on the labelled set. If it
  does not, there is nothing for a council to win.
- **H2 (the council ceiling).** The union across candidates catches materially more than
  the best single model (**≥ +5 points** recall).
- **H3 (diversity needs a different lineage).** A **cross-lineage** pair has lower error
  overlap than a **same-lineage** pair — i.e. its misses are more disjoint.
- **H4 (the cost is tolerable).** The winning council's disagreement rate is low enough
  that the confidence ladder is not flooded with spurious conflicts.

## Falsification conditions

1. H1 fails (incumbent recall **≥ 95%**) → no gap; **stop**, no council.
2. H2 fails (union ≈ best single) → the council adds nothing; **do not build it**.
3. H3 fails (cross-lineage overlap ≈ same-lineage) → family does not matter; **do not
   pull a new model**.
4. H4 fails (disagreements flood the ladder) → the council costs more than it catches.

## Variables

- **Labelled set (fixed, committed with this plan):** 18 turns, each with the facts a
  human would extract. Drawn from the categories the extractor actually handles:
  identity, entity attributes, preferences, plans/dates, household.
- **Candidates — same lineage:** `qwen3.5:4b` (incumbent), `qwen3.5:9b`, `qwen2.5:7b`,
  `sorc/qwen3.5-claude-4.6-opus-q4:4b`.
- **Candidates — cross lineage (to be pulled):** `llama3.2:3b` (Meta), `gemma3:4b`
  (Google). Both are small enough to sit beside the warm set.
- **Measured per model:** recall over the expected facts, and the **error set** (which
  facts it missed). Per pair: **miss overlap** (Jaccard) and the **union recall**.

## Scoring (fixed before the run)

A fact is **found** if the model emits a slot whose **normalised value** matches the
expected value, *regardless of the frame or key it invents* — because the frame/key name
is the model's choice, not the fact. Normalisation: lowercase, strip, collapse
whitespace, strip trailing punctuation; numbers match either form ("6" ↔ "six").

A stricter **schema-level** recall (frame + key + value) is reported as a secondary
metric, not the decision metric.

## Method

```bash
# pulls the two cross-lineage candidates, then runs every model on the set
python assistant/experiments/extraction_council/experiment.py
```

Calls `extract_facts` directly with each candidate as `utility_model` (the production
path), against local Ollama. No brain, no tools.

`result.md` is written only after `verification.md`.

## Threats and limits

- **18 turns is a probe**, not a rate.
- **Value-level matching is generous** (it ignores schema fidelity). It is the honest
  measure of *fact recall*, and the schema-level number is reported alongside so the
  generosity is visible rather than hidden.
- **The labelled set is hand-written.** Facts a human would extract are arguable at the
  margins; the set is committed before the run so it cannot be tuned to a winner.
- **Two cross-lineage models is not "cross-lineage" as a population** — it is one Meta
  and one Google model. If either is a poor extractor, low overlap may be incompetence
  rather than healthy diversity; recall is reported next to overlap for exactly this.
- **Disagreement ≠ error.** Two models disagreeing is not evidence either is wrong. H4
  measures the rate, not the value, of disagreement.
