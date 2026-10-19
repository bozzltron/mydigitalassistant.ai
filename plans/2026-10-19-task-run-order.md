---
date: 2026-10-19
status: active
estimated_hours: 10
---

# Daily task run order — inferred from dependencies, not configured

## Objective

Make the daily tick run tasks in an order where each can build on what earlier ones
learned, with the **system working that order out and the user never configuring it**.

Two halves, and only one of them is about ordering:

| | the question | the failure if skipped |
|---|---|---|
| **Ordering** | which task runs first | tasks run in a plausible-looking order |
| **Propagation** | can the next task *see* what the last one learned | the order is cosmetic — a coherent sequence where nothing actually passes between tasks |

The second decides whether the feature is real. It is currently broken for the richest
kind of learning, and fixing it is worth shipping whether or not the ordering ever ships.

## What is and is not being built

**Built:** an inference step at the top of the tick, a validated topological sort, an
audit record, and event-driven indexing so a task's learning is visible to the next one.

**Not built — deliberately, per the user's preference for ease of use:**

- no `order` slot, no priority field, no Tasks-page contract change
- no UI to set, drag, or inspect the order
- no new dependency
- no re-architecture of the scheduler; the inference is one call site

## Current behaviour (measured, not assumed)

- `store.get_due_scheduled_tasks` has **no `ORDER BY`** and the runner does not sort, so
  today's sequence is SQLite's natural row order — not contractual. An index, a plan
  change, or a VACUUM could reorder it.
- The runner fires **sequentially** (`for task in due: await _execute_task(...)`) — no
  `create_task`, no `gather` — and the scheduler's firing path has **no timeout**. So the
  tick's wall time is the sum of the task durations, and one hung task blocks every task
  after it *and* all housekeeping (the loop is blocked for the whole tick).
- Indexing on write already exists for some paths: episodes (best-effort at write),
  daily-run frames (embedded immediately, "so it's retrievable immediately"), and
  search-extracted frames (in-turn). The 6-hourly top-up
  (`embed_missing_episodes` + `embed_stale_frames`, capped at 100) is a **repair** loop in
  intent, not the primary indexer.
- **The gap:** frames learned by *conversational* extraction are written, their ids are
  collected, and then used only for `update_episode_frame_ids` — they are never embedded.
  They wait for `embed_stale_frames`, i.e. up to six hours. `runner.py` states the
  consequence in its own docstring: *"an unindexed frame is invisible to semantic recall."*
  There is no comment giving a reason for the difference, and the search path does the
  opposite for the same class of frames.

## Prerequisites

These three are independent of the feature and worth shipping on their own.

### P0 — measure the trail (0.5h, read-only, gates the feature)

The measurement already specified as Phase 0 of `plans/2026-10-15-daily-tasks-ui.md`:
how many task runs leave an output episode carrying `frame_ids`?

**Why it gates this:** if runs mostly leave no frames, there is nothing for a dependency to
propagate and the ordering would present a coherent sequence that is not one. Do not build
the feature on an empty trail.

**Falsification:** if the trail is mostly empty, the feature stops here and the plan is
reduced to P1 + P2.

### P1 — event-driven indexing (2–3h)

Embed the frames a turn just learned, instead of waiting for the top-up.

- **Where:** at the **end of the turn**, beside episode logging — *not* in the extraction
  block, which runs before generation and would add to `turn_pregen`. The post-response
  slot already exists and is already timed as `episode_ms`.
- **How:** `store.embed_frames(ids, embed_fn, model)` is already batched, and the ids are
  already in hand at the point where `update_episode_frame_ids` is called.
- **Shape:** best-effort. A failed embed logs at WARNING and leaves the frame for the
  repair loop; it must never fail the turn.
- **The timer stays, demoted to repair.** The same argument `AGENTS.md` already makes for
  consolidation applies unchanged: *"the interval is a look cadence, not a merge cadence."*
  Writes can fail, consolidation invalidates embeddings, and a burst is capped at 100 per
  pass — so the periodic job remains the backstop, not the indexer.

**Ships regardless of the ordering feature.** "You told me something and I cannot recall it
until after lunch" is a defect that needs no other justification.

### P2 — a per-task timeout on the firing path (1h)

The scheduler's firing path has no cap today. Dependency ordering makes the serial chain
load-bearing: a hung task does not merely delay the queue, it means everything after it —
including the dependent task — never runs. This must land before F2.

## Phases

### F1 — probe: can the model tell dependency from topic? (1.5h, pre-registered)

The feature rests on one empirical claim. Measure it before building anything.

- **Input:** ~15 labelled pairs — genuinely dependent (*gather the news* → *summarise the
  news*) versus merely related (*music scene* → *ship tracker*).
- **Output scored:** dependency edges, not an ordering. A model asked for an ordering
  always produces one; a model asked for edges can answer "none".
- **Bar, set before the data:** every dependent pair found **and zero edges on independent
  pairs.** Anything less is a heuristic that surprises the user on a morning nobody is
  watching.
- **Also measured on the same set:** a deterministic alternative — cosine similarity between
  B's prompt and A's *last run's* output, using the existing embedder. No model call, fully
  auditable. Its weakness is the mirror image: it detects relatedness and will over-detect.
  Choose on the numbers.
- **Write `assistant/experiments/task_dependency_detection/plan.md` before collecting any
  data.**

**Falsification:** edges on independent pairs → it is detecting topic, not dependency. Stop;
the feature does not ship.

### F2 — the mechanism (2.5h)

At the top of the tick, when ≥2 tasks are due:

```
build a small prompt (name + description + prompt per task)
  → candidate returns [{"after", "before", "why"}] or []
  → code validates: every name known, no self-edges, no duplicates, drop cycles
  → stable topological sort (edgeless tasks keep their existing relative order)
  → persist edges + reasons on the daily_run frame
  → log edges and their reasons at INFO
  → any failure: leave the order exactly as it was
```

One model call per tick, only when ≥2 tasks are due, entirely off the hot path. The tick
must never block or fail on this.

### F3 — stability and visibility (1h)

- **Infer once, persist, and re-infer only when the task set or a prompt changes.**
  Re-deriving every morning means the order can churn for no reason, and a silent order
  that changes is worse than a fixed one: the user cannot build a mental model, and
  "why did this change?" has no answer.
- **The record is the visibility.** The user cannot set the order; they must be able to
  read why it ran that way. The `daily_run` frame already carries `date`, `status` and
  `tasks_run`; the edges and reasons join it there, alongside the confidence ladder's
  precedent — the user does not set the ladder, but every decision is auditable.

### F4 — the correction path (1.5h, later, optional)

If the order is wrong, the user says so in chat — *"run the ship tracker before the
briefing"* — and that becomes a stated edge, persisted, with the model not asked about it
again. No UI and no new surface: it is the correction path the project already has, and
task management here is chat-only by design.

## Technical approach — the decisions that matter

**Edges, never a permutation.** Asking for an ordering forces a dependency claim. Edges
allow "none", which is the answer most mornings.

**The model judges; the code decides structure.** Same division as `needs_action` deciding
the *kind* of act while `action_performed` decides structurally whether one happened. The
model proposes edges; the code rejects unknown names, self-edges and cycles, and owns the
sort.

**Automatic, not unobservable.** Removing user control raises the cost of being wrong, so
the record is not optional: edges, reasons, and the resulting order are persisted and
logged.

**Fallback is always the current order.** No failure mode of the inference may prevent the
tick from running.

## Where the rules will land (deletion gate)

Per `RUNBOOK.md` §4, this plan is a workspace and is deleted once its reasoning has a
durable home:

| rule | home |
|---|---|
| the tick's order is inferred, edges not permutations, and must never block the tick | `assistant/AGENTS.md`, scheduled-tasks section |
| why conversational frames were not indexed until the top-up, and that they now are | the code comment at the embed site + `assistant/AGENTS.md` |
| the top-up is a repair loop, not the indexer | `assistant/AGENTS.md`, housekeeping table note |
| what shipped and when | `docs/RELEASE_NOTES.md` |

No code comment may cite this plan as its reason; a `See plans/…` pointer outlives the plan
and becomes a dead link the day it is deleted.

## Test strategy

| phase | test |
|---|---|
| P1 | **write a fact, then query it in the next turn** and assert it is retrieved. This is the proof the propagation half works, and it is the same test the feature needs. |
| P1 | an embed failure does not fail the turn (best-effort path) |
| P2 | a task that exceeds the per-task timeout does not block the rest of the tick |
| F1 | the labelled-pair score, recorded in the probe's `result.md` |
| F2 | validator rejects unknown names, self-edges and cycles; a cycle falls back to the existing order |
| F2 | a stable sort leaves edgeless tasks in their current relative order |
| F2 | an inference failure leaves the tick's order untouched |
| F3 | the daily_run frame records the edges and reasons actually used |
| F3 | unchanged task set and prompts → identical order on the next tick |

## What would make us stop

1. **P0** finds no usable trail → the ordering feature is cancelled; P1 and P2 still ship.
2. **F1** detects topic rather than dependency → the feature is cancelled; the deterministic
   alternative is evaluated on the same data before abandoning the idea entirely.
3. Unknown names or cycles are **frequent** rather than occasional → the model cannot do this
   reliably, and validation cannot make a wrong order meaningful.
4. The order still churns after F3's caching → the inference is unstable and should not drive
   the tick.

## Rollback

Each phase is independently revertable and none is load-bearing for the others:

- **P1**: removing the end-of-turn embed returns to timer-only indexing; frames still get
  indexed, just eventually. No data migration — the repair loop already handles missing
  embeddings.
- **P2**: removing the timeout restores today's behaviour exactly.
- **F2/F3**: removing the inference restores SQLite's natural order. Nothing depends on the
  edges being present; the `daily_run` slot is additive and its absence is not an error.

No schema change and no migration is required by any phase.

## Open question carried forward

P1 makes a task's learned *frames* visible to the next task. Episodes are already embedded
at write and recent-episode digests render regardless of budget, so the next task sees a
~240-character digest of the previous answer today. Whether that digest plus the newly
indexed frames is *enough* for a real dependency to be satisfied is the thing F1's data and
P1's retrieval test should show — not something to assume.
