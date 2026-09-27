# Verification: Does the Graph Walk Recover Memory? (graph_walk_yield_2026_09_27)

Status: COMPLETE. Conducted before `result.md` was written, against the
pre-registered plan in `plan.md`.

## 1. Was the manipulation real?

`max_graph_frames` is the only variable. The walk's own output is the manipulation
check: it must be 0, then ~7, then up to 20, monotonically.

| arm | walk frames admitted (mean of 11) | walk share of pool |
|---|---|---|
| `max_graph_frames = 0` | 0.00 | 0% |
| `max_graph_frames = 7` | 6.55 | 68% |
| `max_graph_frames = 20` | 15.64 | 81% |

The control admits nothing while still traversing the graph, so both arms pay the
walk's I/O. The zero arm is the pre-fix production path reproduced: the old gate
admitted 0 of 877 reachable edges, so `cap=0` and "before the fix" deliver the
same frames.

## 2. The measurement that changed the question

The plan's H2 asked whether the walk contributes frames, and the answer is an
unambiguous yes (68% of the pool). That is the less interesting number, and
building the harness made it obvious why.

**The frame-aware fit drops least-relevant-first, and walk frames carry the 0.5
decay, so they rank below direct matches and are the first candidates dropped when
the character budget binds.** Pool share is therefore the wrong metric. The number
that matters is the share of frames that survive to the model.

Measuring it required attributing rendered headers back to their source frame,
which is why `walk_frames_fitted` exists. The gap turned out to be small at
`cap=7` and total at `cap=20` (below).

## 3. Non-mutation

Table digests identical before and after every run: `frames 2058, slots 2891,
associations 1873, episodes 2307, slot_history 8819, conflicts 3775`.

Structurally guaranteed: the run read a copy in the `graph-walk-run` scratch
volume, and the live volume was not mounted into the experiment container at all.
`experiments/preflight.py` gates the run on the experiment DB not being the live
DB, `/live` being absent, a restorable `.assistant-brain` backup existing, and the
copy's SHA-256 being recorded. The copy's digest (`496f27618a10ae25…`, 99,942,400
bytes) matched the live DB at snapshot time.

## 4. Harness bugs, and what they would have cost

Three were caught. The second is the dangerous one and is documented in full in
`walk_value/verification.md`, since it applies identically here.

1. **`embedding_model` omitted** — crashed loudly on a 768-vs-1024 dimension
   mismatch. Caught by the error, not by judgement.
2. **`retrieval_min_distance` assigned to `min_relevance`** — *silent*. It tightened
   the similarity gate from 0.3 to 0.7 and returned **0 frames for 5 of 11
   queries**, while still writing a complete, plausible-looking result file. The
   only tell was cross-checking per-query outcomes against the frame budget
   experiment, which had already shown those queries to be answerable. This would
   have been reported as "the walk still doesn't work."
3. **Substring frame counting** — counting `"### "` matched slot values containing
   that text and reported 11 headers for a 10-frame context, i.e. `dropped -1`. A
   negative count is self-evidently wrong, which is the only reason it did not
   survive into a result.

A fourth issue was a metric error, not a bug: `memory_chars` was first measured
with `max_memory_chars=None`, which is the *unfitted* render. Production passes a
real allowance via `Orchestrator._memory_char_budget`
(`max_system_prompt_chars` minus the measured persona prefix, 9,935 chars here).
Reporting the unfitted number would have overstated the fit by ~2k chars and hidden
truncation the user experiences. Corrected to measure the fitted render.

## 5. Threats that remain

1. **Relevance is not usefulness.** This experiment measures what the walk
   *delivers*, not whether it *helps*. That is the entire reason a second
   experiment was run; it is not a limitation of this one's conclusions, it is the
   boundary of them.
2. **11 queries reused from the frame budget experiment**, selected for
   answerability. The shared corpus makes the two experiments comparable and the
   query set unrepresentative of real usage at the same time.
3. **Walk frames are confirmed weakly related to the query**: median relevance
   0.03–0.18 against 0.12–0.19 for direct matches. That is what an associative
   walk is supposed to produce, and it cannot be distinguished from noise by a
   relevance metric alone.
4. **The memory budget used here is the most generous production can hand out** —
   the `functional` task type with no plan and no self context, which is the
   smallest possible persona prefix. A real turn's prefix is larger, the fit is
   tighter, and more frames are dropped. Production is therefore slightly harsher
   than these numbers.
5. **One corpus, one owner, one query style.** The walk's value depends on
   association density, and this corpus's density is not established as typical.
