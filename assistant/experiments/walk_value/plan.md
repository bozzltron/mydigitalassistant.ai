# Experiment: Does the Graph Walk Make the Agent Better?

## Status

**PRE-REGISTERED. No generations run yet. No result may be written until this
document is committed.**

## Question

`graph_walk_yield` established that the fixed graph walk is *active*: it supplies
68% of the candidate pool and 65% of the frames the model actually sees
(65 walk frames vs 33 direct across 11 queries). It also established that
`max_graph_frames = 7` is exactly right, because raising it to 20 changes the
delivered prompt by zero characters while 107 extra walk frames are ranked and
discarded.

Neither fact says the agent is **better**. Walk frames carry median query
relevance 0.03–0.18 against 0.12–0.19 for direct matches, so they are 3–4× less
similar to the query text. That is *expected* — the entire point of an
associative walk is to reach frames the embedding search would not surface — but
expected is not the same as useful. Six queries' worth of weakly-related frames
occupying two thirds of the memory section could just as easily be six queries'
worth of noise.

**This experiment measures the user-visible outcome: does the walk change what
the agent answers correctly?**

## Why this is the right next step

`graph_walk_yield` was a yield experiment and said so in its own threat list:
"Walk-sourced frames may be relevant yet not *answer-bearing*. Relevance is not
recall. Only a generation-based follow-up can establish whether a walk-recovered
frame is actually used." This is that follow-up, and it is the last thing standing
between the shipped fix and a defensible claim about it.

It is also the cheapest possible test of the recommendation. My recommendation
was that the pipeline should spend its effort on inclusion rather than ranking.
The walk is the mechanism that delivers inclusion. If it does not move recall, the
recommendation is wrong and should be withdrawn rather than defended.

## Hypotheses

- **H1 (recall).** Walk-enabled retrieval raises gold recall over walk-disabled.
- **H2 (abstention).** Walk-enabled retrieval lowers the abstention rate — the
  user-visible cost of a memory the agent holds and refuses to use.
- **H3 (attribution).** The frames that make recall possible include
  walk-sourced ones, i.e. the walk supplies *target* frames that direct semantic
  search did not rank into the top 3. This is the mechanism check: H1 without H3
  would mean the walk only displaced better frames.
- **H4 (no cost).** Latency does not regress. `graph_walk_yield` showed the walk
  is latency-flat, so any regression here is noise or an interaction with
  generation, not the walk itself.

## Variables

- **Condition A (control):** `max_graph_frames = 0`. The walk is traversed but
  nothing is admitted. This is the **shipped-before-the-fix behaviour** in
  effect — the old gate admitted 0 of 877 edges, so cap=0 reproduces the pre-fix
  production path while still paying the walk's I/O, isolating *contribution*
  from *cost*.
- **Condition B:** `max_graph_frames = 7` (production).

Both arms use the production `Retriever` construction from `main.py`:
`top_k_direct=3`, `min_relevance=0.3`, `embedding_model=settings.embedding_model`.
11 queries × 22 gold facts × 3 replicates = 66 generations per arm, 132 total.

Generation settings mirror production exactly: `temperature = 0.7`,
`think = False`, seed recorded, `build_system_prompt(task_type="functional")`,
and the `max_system_prompt_chars` ceiling applied as the orchestrator applies it.

Reusing the frame budget experiment's queries and gold facts keeps the numbers
comparable to the already-published recall|shown = 0.910 result and avoids
re-screening queries against a fix that had not shipped when those facts were
chosen.

## What counts as evidence

- **H3 is the primary test, not H1.** Yield was already established. A recall
  rise with no walk-sourced target frames would mean the walk is pure cost. A
  walk-sourced target frame that the agent then answers correctly is the whole
  claim, end to end.
- Recall is reported alongside **recall|shown and recall|hidden**, because
  recall|shown ≈ 0.91 and recall|hidden ≈ 0.011 mean the effect of any memory
  change arrives entirely through which facts are shown. Aggregate recall alone
  would hide a change that merely reorders already-delivered facts.
- A difference is only claimed if it survives 3 replicates. Per-replicate spread
  is reported so the reader can judge that directly.

## Falsification conditions

1. *Recall does not rise, and walk-sourced target frames are rare* → the fix is
   not worth its complexity and the default should revert toward direct-only.
   I will say this plainly if the data says it.
2. *Recall rises but abstention does not fall* → the win is partial and the
   abstention problem is not a memory-access problem.
3. *Latency regresses materially* → the walk trades user-visible speed for
   recall, which needs to be a deliberate choice, not a default.
4. *All recall comes from facts already reachable by direct search* → the walk
   is redundant with the embedding index and the ANN/slot-level work is the
   better investment.

## Threats, stated now rather than after seeing results

1. **Queries are reused from the frame budget experiment**, where they were
   selected for answerability. They are not a sample of real usage, and 11
   queries cannot support a claim about vague or multi-hop questions. This
   experiment bounds the walk's value on direct questions; it does not
   characterise it in general.
2. **`max_graph_frames = 0` is a control, not a shipped configuration.** It pays
   the walk's I/O in both arms, which is the right design for isolating
   contribution but means neither arm's latency is a clean production number.
3. **Recall is graded by normalized substring match** on 22 facts. That is a
   blunt instrument — it can credit a correct-looking answer and miss a
   correct paraphrase. It is the same instrument the frame budget experiment
   used, so the comparison is like-for-like, but it is not a semantic grader.
4. **One brain, one user, one corpus.** 2058 frames, 1958 of them shared and 51
   user-owned. A walk's value depends on association density, and this corpus's
   density is not necessarily typical.
5. **The walk's frames are confirmed low-relevance** (median 0.03–0.18). If they
   are noise, the honest outcome is a negative result for the fix, and the
   correct response is to shrink the cap rather than to re-tune the walk until it
   looks good.
