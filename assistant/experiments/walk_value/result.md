# Result: Does the Graph Walk Make the Agent Better?

Run 2026-09-27. 11 queries × 22 retained gold facts × 3 replicates × 2 arms =
132 generations. `chat_model = qwen3.5:9b`, `temperature = 0.7` (production),
`think = False` (production), seed 20260927. Pre-registered in `plan.md`;
verified in `verification.md` before this file was written.

## Headline

| | walk off (`cap=0`) | walk on (`cap=7`) | change |
|---|---|---|---|
| **gold recall** | 0.667 | **0.803** | **+0.136** |
| recall \| shown | 0.917 (n=48) | 0.930 (n=57) | +0.013 |
| recall \| hidden | 0.000 (n=18) | 0.000 (n=9) | — |
| abstention | 0.030 | **0.000** | −0.030 |
| frames delivered | 3.00 | 9.73 | +6.73 |
| target frame present | 6/11 | 8/11 | +2 |
| **target reached via the walk** | 0/11 | **2/11** | +2 |
| generation p50 | 2475ms | 2667ms | +192ms |

Per-replicate recall: control 0.682 / 0.682 / 0.636, walk 0.818 / 0.864 / 0.727.
**Every replicate of the walk arm beats every replicate of the control.**

## The fix works, and the causal chain is closed

This is the part worth stating precisely, because "recall went up" is a much
weaker claim than what the data actually supports.

**The mechanism is inclusion, exactly as the frame budget experiment predicted.**
recall|hidden was 0.000 in both arms, so nothing was gained by the model
reasoning better. The walk converted **9 hidden fact-observations into visible
ones, and the model recalled all 9**. 9 is exactly 3 gold facts × 3 replicates.

**Those 3 gold facts came from exactly 2 frames, and the walk found both:**

| query | target frame | found at | gold facts recovered |
|---|---|---|---|
| `mtw_genre` | The Mountain & The Wolf (genre/release) | `graph_hop_2` | genre, release |
| `why_not_label` | Why Not | `graph_hop_1` | label |

The arithmetic closes with no residual: +9 observations out of 66 = +0.136
recall, which is the entire headline change. The walk did not make the model
smarter, did not rerank better, and did not improve anything on the 9 queries
where direct search already had the target.

`why_not_label` is worth singling out. `assistant/experiments/frame_budget/plan.md`
records it as the query that motivated frame chunking in the first place, because
"Why Not" ranked **457th of 1992** for a query naming it exactly. Semantic
similarity could not find it. One association hop from a frame that *was* in the
top 3 found it immediately.

## Attribution is clean, and it was checked for the obvious cheat

Because retrieval ran once per arm and Ollama's embeddings are not
bit-deterministic, a target could in principle have appeared in the walk arm
through direct search alone. That channel was measured and is **empty**: all 6
targets direct search found in the control were still found with the walk on, and
0 queries changed source in the direct→direct direction. The walk neither
displaced a direct match nor manufactured its own credit.

Two facts did move on noise — `studio/city` +0.33 and `festivaltopia/tagline`
−0.33, both on facts shown in *both* arms — and they cancel to approximately zero.
The 9 hidden→shown conversions are the whole story.

## The char budget now binds, and it only ever spends walk frames

With 3 frames the 12,000-char cap never bound. At 9.73 mean frames it does:
4 of 11 queries drop a whole frame, and `festivaltopia` drops 4 of 10 (fitted
memory 9,796 chars against a 9,935-char allowance).

Notably, **no direct frame was ever dropped** in any query in either arm. The
frame-aware fit sheds least-relevant-first, and walk frames carry the 0.5 decay,
so they rank below direct matches. The budget is effectively "direct frames are
free, walk frames pay." That is a coherent policy — it spends the surplus on
recall and protects the confident matches — but it is a policy, and it means
walk frames are the shock absorber. If a walk-supplied target had been the 4th
weakest frame on `festivaltopia`, it would have been cut.

## Falsification conditions, adjudicated

| condition from plan.md | outcome |
|---|---|
| 1. Recall does not rise and walk-supplied targets are rare | **Not met.** Recall rose 0.136; 2/11 targets walk-supplied. |
| 2. Recall rises but abstention does not fall | **Not met.** Abstention went 0.030 → 0.000. |
| 3. Latency regresses materially | **Not met, but not free.** +192ms p50 generation (+7.8%); the walk itself was latency-flat in `graph_walk_yield`. |
| 4. All recall comes from facts already reachable by direct search | **Not met.** Zero direct-search changes; both gains are walk-sourced. |

None of the four fired. For once the fix survived its own pre-registration.

## What this does not establish

Stated plainly, because the honest size of the win matters more than the win.

- **The gain rests on 2 of 11 queries.** On the other 9 the walk changed no
  recall. I would not defend 0.803 as a stable number for this system — I would
  defend "the walk recovers association-reachable memory that similarity search
  cannot reach, and on this corpus that was 2 of 11 queries and 0.136 recall."
- **3 queries still have no reachable target frame** (9 hidden observations, all
  unrecalled). Inclusion remains the open problem; this fixed one route into it,
  not the problem.
- **The grader is a substring match.** It credits a correct-looking answer and
  can miss a correct paraphrase. Same instrument as the frame budget experiment,
  so the comparison is like-for-like, but it is not semantic.
- **The queries were selected for answerability.** 11 direct questions on one
  corpus. Nothing here predicts behaviour on vague, multi-hop, or adversarial
  questions.
- **The recall|shown gap narrowed (0.917 → 0.930) and is not a finding.** Both sit
  on the published 0.910 within sampling noise at these n.

## The one durable engineering claim

Not "recall is 0.803" but this: **association edges reach frames that embedding
similarity cannot, and they reach them at ~zero latency cost.**

The second half is measured, and it is what makes the first half affordable.
`graph_walk_yield` found the walk is latency-flat (p50 541ms at `cap=7` vs 569ms
at `cap=0` — no regression), and `max_graph_frames=7` is provably the right
number rather than a tuned guess: at `cap=20` the delivered prompt is
**byte-identical** while 107 extra walk frames are ranked and discarded. Raising
the cap cannot help, because the char budget decides the count and 7 is what
survives it.

That is the shape to protect in future work: keep inclusion topological and
cheap, let the character budget arbitrate what the model actually sees, and
spend no effort on presenting memory the model is already proven to read at
0.91 when given.
