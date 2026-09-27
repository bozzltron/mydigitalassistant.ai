# Result: Does the Graph Walk Recover Memory?

Run 2026-09-27. 11 queries × 3 conditions, 33 retrievals. Read-only against a
copy; pre-registered in `plan.md`, verified in `verification.md` before this file
was written. Companion experiment: `walk_value`, which asks the follow-up question
this one deliberately does not answer.

## Headline

| | `cap=0` (control) | `cap=7` (production) | `cap=20` |
|---|---|---|---|
| walk frames admitted (mean) | 0.00 | 6.55 | 15.64 |
| walk share of candidate pool | 0% | 68% | 81% |
| **walk share of what the model sees** | 0% | **65%** | **65%** |
| frames delivered (mean) | 3.00 | 8.91 | 8.91 |
| memory chars, fitted (mean) | 3,641 | 6,965 | 6,965 |
| queries where the fit drops a frame | 0/11 | 4/11 | 9/11 |
| retrieval p50 | 569ms | 541ms | 536ms |

**The fix works.** The pre-registered bar was "if walk-sourced frames are under
~20% of the final context, the fix did not deliver and I will say so." The
measured share of delivered frames that came from the walk is **65%**, and the
walk supplies 6.55 of 9.55 mean frames where it previously supplied none of the
877 edges it reached.

## The result that settles the cap: `max_graph_frames = 7` is not a tuned guess

This is the finding I did not go looking for, and it is the more useful one.

**Raising `max_graph_frames` from 7 to 20 changes the delivered prompt by zero
characters.** Per query, the fitted memory section is byte-identical:

| query | fitted at `cap=7` | fitted at `cap=20` | extra frames ranked and discarded |
|---|---|---|---|
| `cbs_hq` | 5,126 | 5,126 | 7 |
| `perseverance` | 5,632 | 5,632 | 9 |
| `penn_state` | 6,090 | 6,090 | 8 |
| `mtw_members` | 9,534 | 9,534 | 14 |
| `festivaltopia` | 9,796 | 9,796 | 17 |

Across the experiment: **172 walk frames entered the `cap=20` pool, 107 of them
were ranked and then dropped at the character budget, and the model saw the same
65 walk frames either way.**

The count of walk frames that survive is set by the 9,935-char memory allowance,
not by the cap. Seven is what fits. The cap's only job is to stop the walk
over-fetching past that, which it does at essentially no latency cost
(`cap=20` p50 536ms vs `cap=7` 541ms — flat, not cheaper).

This converts a tuned constant into a derived one, and it means the earlier
suggestion that further walk gains might come from raising `max_graph_frames` is
**wrong by measurement**. They cannot. The lever is candidate *generation* —
association density, or better edges — not the cap.

## The char budget binds now, and it only ever spends walk frames

At 3 frames the 12,000-char system prompt cap never bound. At 9.55 mean frames it
does: 4 of 11 queries drop a whole frame at `cap=7`, and `festivaltopia` drops
4 of 10 (9,796 chars against a 9,935-char allowance).

The policy that emerges is worth stating because it is not the one I expected:
**no direct frame was dropped in any query, in any condition.** The frame-aware
fit sheds least-relevant-first, walk frames carry the 0.5 decay, and so walk frames
rank below direct matches and absorb the entire cut. The budget operates as
"direct matches are free, walk frames pay." That is a defensible allocation — it
spends surplus on recall while protecting the confident matches — and it also
means walk frames are the shock absorber. A walk-supplied target that happened to
be the 4th weakest frame would be cut.

## Walk frames are weakly related to the query. That is the honest tension.

| | median relevance to query |
|---|---|
| direct matches | 0.12 – 0.19 |
| walk frames | 0.03 – 0.18 |

Walk frames run 3–4× less similar to the query text than direct matches. This is
partly the point — a frame the embedding search would have found is not worth a
graph hop — but it is also exactly what noise looks like, and no relevance metric
can tell the two apart.

**This experiment therefore cannot say whether the walk makes the agent better.**
It can only say the walk reliably delivers frames, cheaply, that they are not
similar to the query, and that the cap is already correct. Whether those 6.55
weakly-related frames per query are signal is the question
`assistant/experiments/walk_value` was written to answer, and its answer should be
read as the verdict on this experiment's yield.

## Falsification conditions, adjudicated

| condition from plan.md | outcome |
|---|---|
| 1. Walk yields frames but they are low-similarity noise | **Unresolved by design.** Yield established; usefulness deferred to `walk_value`. |
| 2. Walk contributes nothing because seeds have no in-scope edges | **Not met.** 65% of delivered frames. |
| 3. `cap=20` is no better than `cap=7` | **Met.** Byte-identical prompts, 107 frames discarded. `max_graph_frames=7` confirmed. |
| 4. Latency cost is superlinear in seeds | **Not met.** 569 / 541 / 536ms across `cap` 0 / 7 / 20. Flat. |

## What this does not establish

- **Not that the agent is better.** Yield ≠ value. See `walk_value`.
- **Walk frames are not proven relevant**, only proven numerous. 11 queries cannot
  resolve a relevance distribution finely, and the relevant question is
  answer-bearingness, not similarity.
- **The memory allowance used here is the most generous production can issue** —
  the smallest persona prefix. Real turns are tighter, so production drops
  slightly more frames than these numbers show.
- **Reused queries, one corpus, one owner.** 2058 frames, 1958 shared and 51
  user-owned. The walk's value scales with association density, and this corpus's
  density is not established as typical.
- **Retrieval is not bit-reproducible.** An independent run of the same production
  configuration (`walk_value`) measured 9.73 mean frames against this run's 9.55 —
  a ~2% spread from Ollama's embeddings being non-deterministic. Individual
  per-query frame counts therefore carry a couple of frames of jitter, and any
  future comparison that retrieves separately per condition has to check for
  direct-search drift the way `walk_value/verification.md` does rather than
  attributing the difference to the variable under test.

## Recommendation this experiment changed

I had proposed that further recall gains might come from raising
`max_graph_frames`, or failing that from tuning hops and decay. The first is now
**ruled out by measurement**. The second remains untested, but the yield data
suggests a better target: the walk's job is to reach frames similarity cannot, so
what matters is not how many frames it admits (7 is already all that fit) but how
often an admitted frame is the *right* one. That is a question about association
coverage and edge quality, not about a cap — and it is a considerably larger
project than tuning two constants.
