# Experiment: Does a Long List Survive a Transform, and Does Decomposition Save It?

## Status

**PRE-REGISTERED. No data collected yet. No result may be written until this
document is committed.**

## Question

The user pasted **45 bare URLs** and asked for them ranked by impact, expecting the
agent to research what each link was first ("I was hoping the agent would take the
knowledge which url mapped to high impact sources and sort them accordingly so that I
could start at the top").

What happened:

- at **7 links** (this experiment's harness), the agent ranks the list correctly;
- at **45 links** (live), the agent wrote an essay about one station and the list
  appeared nowhere in the answer.

Same task, same model, same prompt shape. The only thing that changed is N. Three
attempts to fix this with static rules all failed, and the fourth was going to be a
transform pipeline — so this measures the thing that decides whether that pipeline is
warranted:

**H1: At some N, a one-shot transform stops preserving the item set.**
**H2: Per-item decomposition preserves it at the N where one-shot fails.**
**H3: If decomposition works, the enriched items can then be ordered without losing them.**

## Why this is worth measuring

It decides between three genuinely different builds:

1. **One-shot is fine; 45 was bad luck.** → no new machinery, maybe a retry.
2. **One-shot degrades with N; decomposition holds.** → a transform pipeline over
   items is warranted, and this experiment is its justification.
3. **Both corrupt at high N.** → this is model capability, not architecture, and the
   answer is a fleet comparison (which model does this best) rather than a pipeline.

Building the pipeline without this measurement would be the fourth static design on
this thread, and the previous three are recorded in
`assistant/tests/test_model_chooses_tools.py` as a cautionary note.

## Hypotheses, stated as measurable claims

- **H1 (the cliff).** Corruption rate rises with N in the one-shot condition. The
  experiment reports the N at which it exceeds the bar, or that no cliff appears
  within the range tested.
- **H2 (decomposition).** At the largest N, per-item corruption is materially lower
  than one-shot.
- **H3 (ordering after enrichment).** Ordering an enriched set preserves every item
  that the enrichment preserved.

## Variables

- **N ∈ {7, 20, 45}**: one-shot, single prompt, list in, ranking out.
- **Condition B (decomposed)**: for each item, one call producing that item's
  description and impact note; then one ordering call over the *annotations* (not the
  original list), which must return each item's identifier exactly once.

Reported per condition and N: items in, items out, **items added** (invented),
**items dropped**, **items mutated** (URL text changed), and whether order is a
permutation of the input.

The primary metric is **corruption rate**, defined as the fraction of input items that
are dropped or mutated, plus any invented items counted separately. Fluency is not
measured and is not a result.

## What counts as evidence

- **H1 is the primary test.** The bar, fixed now: a condition **passes** at a given N
  if corruption is **0** — every input item present, none mutated, none invented.
  Anything else fails at that N. There is no partial credit, because a list you cannot
  trust is not a list you can work from top-down.
- **H2** is decided at the largest N where one-shot fails: decomposition must reach 0
  corruption to be worth building. Decomposition that merely corrupts *less* is
  reported as a failure of the build case, not as a win.
- **H3** is decided by set equality between the enriched set and the ordered output.

## Falsification conditions

1. *One-shot passes at all N including 45* → no cliff, the live failure was not N,
   and the transform pipeline is not justified by this evidence. Say so.
2. *Decomposition fails at 45 too* → this is model capability. Do not build the
   pipeline; the follow-up is a fleet comparison, and that is a different experiment.
3. *Corruption is present but stable across N* → N is not the variable; investigate
   the prompt shape instead of building decomposition.
4. *Decomposition preserves items but the ordering call drops them* → H3 fails, and
   the pipeline's hard part is the merge, not the per-item work.

## The list, and why it is the real one

The 45 URLs are the user's actual submission list, frozen in `experiment.py`. Using the
real list matters: it is the artefact that failed, it is long enough to be a genuine
test at the top N, and its items are heterogeneous (station contacts, Google Forms,
a Reddit thread, Bandcamp, an eBay listing) which is what makes enrichment hard.

For N=7 and N=20 the first 7 and 20 items are taken **in list order**, and this is
recorded — a prefix is not a random sample, and the first 20 skew toward radio
contact pages.

## Safety

**No brain.** This experiment reads no database, opens no store, and writes no memory.
It is isolated by construction, like `decision_routing_value`, and the isolation is
demonstrated rather than asserted:

- imports recorded in `verification.md` (stdlib + `httpx` only);
- `assistant.db` SHA-256 and mtime recorded before and after;
- **it does search**, unlike `decision_routing_value` — enrichment requires it. That
  means queries derived from the user's URLs leave the machine via the configured
  backend (Brave, in this configuration). This is stated plainly because it is the
  one experiment so far that entails external requests, and the user's own request is
  the thing that authorises it: they asked for these links to be researched. The
  queries are the URLs themselves plus a short disambiguation, not conversation text.
- The experiment records every search query it issues, so what left the machine is
  auditable afterwards rather than inferred.

## Known threats, stated now rather than after seeing results

1. **N and input length are confounded.** 45 URLs is both more *items* and more
   *tokens*. This experiment cannot separate "too many things to track" from "too much
   text for the context", and it will say so rather than claiming item count is the
   cause.
2. **One run per cell.** Model output is not deterministic. A single sample per (N,
   condition) cannot distinguish a stable failure from a bad draw. The plan records
   `runs_per_cell` and this result reports whatever variance it can, but a small
   sample is a small sample.
3. **The corruption metric is mechanical and generous.** It checks URL text survival,
   not whether the *ranking* is any good. A condition can pass at 0 corruption while
   ranking uselessly.
4. **Enrichment quality is not measured.** "What each thing is" correctness (the live
   run said WKDU was University of Kansas; it is Drexel) is out of scope here. This
   measures whether the *list* survives enrichment, not whether enrichment is true.
5. **The list is one user's, one genre.** Item difficulty varies by corpus.
6. **Search results vary run to run.** Enrichment inputs are not reproducible, which is
   a further reason one sample per cell is weak.
