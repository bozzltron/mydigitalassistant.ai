# Verification: Does the Graph Walk Make the Agent Better? (walk_value_2026_09_27)

Status: COMPLETE. Conducted before `result.md` was written, against the
pre-registered plan in `plan.md`.

## 1. Was the manipulation real?

`max_graph_frames` is the only thing that differs between arms. Verified from the
recorded per-query retrieval data:

| arm | mean frames returned | mean walk frames | target present | target via walk |
|---|---|---|---|---|
| `max_graph_frames = 0` | 3.00 | 0.00 | 6/11 | 0/11 |
| `max_graph_frames = 7` | 9.73 | 6.73 | 8/11 | **2/11** |

The control is a true control: it traverses the graph and admits nothing, so
both arms pay the walk's I/O and the comparison isolates *contribution* rather
than *cost*.

## 2. The confound this design introduced, and the check that killed it

**The threat:** retrieval ran separately per arm, and Ollama's embedding endpoint
is not bit-deterministic. Any query whose *direct* results differed between arms
would be noise, not the walk. Direct search is parameter-identical across arms
(`top_k_direct=3`, `min_relevance=0.3`, same `embedding_model`), so a target that
appears via `direct_match` in one arm and not the other cannot be caused by the
walk.

**The check:** compared `target_source` arm 0 vs arm 7 for all 11 queries.

| outcome | count |
|---|---|
| identical source in both arms | 9 |
| target absent in both arms | 0 |
| **direct-search noise** (present via direct in one arm only) | **0** |
| target supplied by the walk | 2 (`mtw_genre` at hop 2, `why_not_label` at hop 1) |

Zero direct-search noise. All 6 targets that direct search found in arm 0 were
still found in arm 7, so the walk did not displace any direct match, and the 2
additional targets are genuinely walk-sourced. The noise channel this design
exposed is empty.

## 3. Two harness bugs, both caught by refusing to report implausible numbers

Recorded because both would have produced a confident, wrong result.

**Bug 1 — `embedding_model` omitted.** The first run died on
`Vector dimension mistmatch. First vector has 768 dimensions, while the second has
1024`. `Retriever.__init__` defaults to `"nomic-embed-text"` (768 dims) while the
brain is embedded with `settings.embedding_model` (1024 dims). A loud crash, not a
silent wrong answer — the best kind of bug.

**Bug 2 — `retrieval_min_distance` conflated with `min_relevance`.** This one was
**silent and would have invalidated the experiment.** `retrieval_min_distance`
(0.7) is a sqlite-vec *distance* threshold already passed to
`search_similar_frames` as `min_distance`; `min_relevance` (0.3) is a separate
*similarity* gate. Assigning the former to the latter tightened similarity to 0.7
and **zeroed the result set for 5 of 11 queries** while still producing plausible
output, a summary block, and a result file. The tell was not any error — it was
that `cap=0` returned 0 frames for `label_address`, `cbs_hq`, `perseverance`,
`penn_state` and `mtw_genre`, which the frame budget experiment had already shown
to be answerable. Cross-checking against a prior experiment's per-query outcome is
what caught it. Production's construction in `main.py:204` is now mirrored
explicitly, with both traps documented at the call site.

**Bug 3 — a summary metric undercounted the result.** `target_via_walk_rate`
hardcoded `graph_hop_1` and so reported 1/11, missing the `graph_hop_2`
discovery. Corrected to 2/11. The correction is a pure function of the stored
per-query retrieval data, so it was recomputed from `result.json` without
re-running any generation; recall, abstention and latency remain the original
run's values. The note recording this is in `result.json` under
`summary_recomputed_note`.

**Bug 4 — a frame-counting method that reported negatives.** Counting
`"### "` as a substring matched slot values containing that text and reported 11
headers for a 10-frame context (`dropped -1`). A negative drop count is not a
subtle bias, it is an obviously wrong number, and it was replaced with a
line-anchored count.

## 4. Non-mutation

Table digests are identical before and after the run:

```
frames 2058, slots 2891, associations 1873, episodes 2307,
slot_history 8819, conflicts 3775   ->  unchanged
```

Structurally guaranteed rather than merely observed: the run used a copy in the
`graph-walk-run` scratch volume, and the live volume was not mounted into the
experiment container at all. `experiments/preflight.py` refuses to start unless
the experiment DB is not the live DB, `/live` is absent, a restorable
`.assistant-brain` backup exists, and the SHA-256 of the copy is recorded. The
copy's digest (`496f27618a10ae25…`, 99,942,400 bytes) matched the live DB at the
time of the snapshot.

## 5. Threats that remain

Stated because they bound the claim, not because they were all resolved.

1. **n = 11 queries, 22 facts, 3 replicates, one corpus.** Every replicate of the
   walk arm beat every replicate of the control (0.727–0.864 vs 0.636–0.682), so
   the effect is not a sampling artifact *on these queries*. It says nothing about
   vague, multi-hop, or non-English questions, and nothing about a corpus with
   different association density.
2. **The gain rests on 2 queries.** The entire recall improvement comes from
   `mtw_genre` and `why_not_label`. On the other 9 queries the walk changed
   nothing about recall. A 2-query effect that reproduces is worth keeping; a
   2-query effect is also one corpus quirk away from being noise, and I would not
   claim a percentage as stable.
3. **Recall|hidden stayed 0.000, so there is still 9 unrecalled hidden
   observations** (3 queries whose target frame neither direct search nor the walk
   reached). Inclusion is not solved.
4. **The grader undercounts what the model can use.** `mtw_genre/genre` and
   `mtw_genre/release` were answered correctly at 1.00 while their literal surface
   forms were absent from the prompt, i.e. the model derived them from the frame
   rather than copying. The "shown" test is therefore conservative, which biases
   recall|shown *down*, not up — the direction that makes the headline gain larger,
   not smaller.
5. **Latency moved +192ms p50** (2475 → 2667ms generation). Small, but it is a real
   cost and it is not free.
6. **Two harness bugs in one experiment is a signal about the harness, not the
   system.** The `min_relevance` confusion is a live footgun in the production
   constructor's defaults; any future experiment that constructs a `Retriever` by
   hand can silently reproduce it. The mitigation is comments at the call site
   plus cross-checking per-query outcomes against a prior experiment, and that is
   a process fix rather than a code fix.
