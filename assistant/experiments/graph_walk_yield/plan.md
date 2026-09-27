# Experiment: Does the Graph Walk Recover Memory?

## Status

**PRE-REGISTERED. No data collected yet. No result may be written until this
document is committed.**

## Question

The frame budget experiment settled *how many* frames to put in the prompt
(recall peaks at 10; recall|shown 0.910 vs recall|hidden 0.011). It explicitly
did **not** settle *where those frames should come from*, and it said so: the
candidate pool in that experiment was 40 with the model seeing 10, whereas
production was returning 3.

Since then the walk was fixed. It previously admitted **0 of 877** reachable
in-scope edges (the score product collapsed to median 0.038 against a 0.3 gate),
and now separates inclusion (topological) from ranking (quality product).

**This experiment measures whether that fix actually recovers memory the
production path was leaving on the floor.** If it does not, the fix is
sophistication with no return, and the priority goes back to candidate
generation by some other means.

## Why this is worth measuring

The fix is already shipped, so there is a real risk of reading its code as
evidence that it works. Code that walks a graph is not evidence that the walk
returns anything useful. The honest test is: run production retrieval, with and
without the walk, and ask what changes for the user.

## Hypotheses

- **H1 (yield).** With the walk enabled, retrieval returns more frames per query
  than `top_k_direct` alone.
- **H2 (contribution).** A non-trivial share of frames in the final context comes
  from the walk rather than from direct semantic matches. If the walk contributes
  ~0 frames, the fix did not work despite now traversing edges.
- **H3 (quality).** Walk-sourced frames are *relevant*, not merely numerous. A
  walk that fills the budget with weakly-related neighbours would raise H1 and
  destroy recall — this is the failure mode that matters, and it is why the
  yield alone is not the result.
- **H4 (cost).** The walk costs latency per retrieval, and the cost should be
  roughly flat in the number of seeds (it is batched, two queries per hop).

## Variables

- **Condition A (control):** `max_graph_frames = 0` — walk traversed but nothing
  admitted. This isolates the *contribution* of walk results from their
  *existence*, holding the walk's I/O cost in both arms.
- **Condition B:** `max_graph_frames = 7` (production).
- **Condition C:** `max_graph_frames = 20` — is the current cap leaving
  candidates on the table, or is 20 already past the useful point?

Reported per condition: frames returned, frames rendered, walk-sourced frames,
their share, similarity distribution of walk frames, latency percentiles,
prompt memory chars, and truncation rate.

## What counts as evidence

- H2 is the primary test. **If walk-sourced frames are under ~20% of the final
  context, the fix did not deliver and I will say so.**
- H3 is decided by the similarity distribution of walk frames relative to direct
  matches, not by yield.
- Yield without relevance is explicitly treated as a *negative* result.

## Falsification conditions

1. *Walk yields frames but they are low-similarity noise* → report as a failure
   of the fix and revert the default toward a smaller cap.
2. *Walk contributes nothing because seeds have no in-scope edges* → the fix is
   inert on this brain; say so and do not claim a win.
3. *Condition C is no better than B* → 7 is not the binding constraint and
   further walk tuning is low value.
4. *Latency cost is superlinear in seeds* → the batching claim is wrong and the
   documented cost model needs correcting.

## Safety

Read-only, and structurally so:

- Runs against a **copy** in a scratch volume, never the live volume. The live
  volume is not mounted into the experiment container at all.
- `experiments/preflight.py` refuses to start unless: the experiment DB is not
  the live DB, the live volume is not mounted, a restorable `.assistant-brain`
  backup exists, and the SHA-256 of the copy being read is recorded.
- Table digests (frames, slots, associations, episodes, slot_history, conflicts)
  are captured before and after the run and written to `result.json`, so
  non-mutation is verifiable rather than asserted.
- The verification write-up is produced **before** `result.md` is written, and
  names threats that remain rather than only the checks that passed.

## Known threats, stated now rather than after seeing results

1. **These 11 queries are reused from the frame budget experiment.** Reusing them
   keeps the comparison honest against prior numbers but means the queries were
   selected for answerability, not drawn from real usage. Conclusions do not
   generalize to vague or multi-hop questions.
2. **Walk-sourced frames may be relevant yet not *answer-bearing*.** Relevance is
   not recall. Only a generation-based follow-up can establish whether a
   walk-recovered frame is actually used. This experiment bounds the value; it
   does not book it.
3. **`max_graph_frames = 0` is a control, not a production mode.** Condition A
   still pays the walk's I/O. That is intentional (it isolates contribution from
   cost) but it means A's latency is not a production baseline.
4. **n is small.** 11 queries. Enough to see a large effect in yield; not enough
   to make a fine-grained claim about the similarity distribution.
