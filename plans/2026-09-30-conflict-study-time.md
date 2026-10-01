---
date: 2026-09-30
status: closed
estimated_hours: 10
---

# Plan D — Conflict study time: CLOSED, not built

## Why this plan was closed

**The gate was met in the negative.** The plan was written on a reading of the
conflict data that measurement has since disproven. It would duplicate a mechanism that
already works, which is the principle this project has spent its effort removing
rather than adding.

### The premise was wrong

The plan's justification began:

> *"Measured: of 2,377 auto-resolved conflicts, 2,377 (100%) resolved to `new_value` —
> the last rung (recency) decides every case, because extraction writes everything at
> `source_reliability=null` → 0.5, so the first rung never discriminates."*

Both halves of that are false, and both were checked against the live brain:

1. **The 100% was ties, not a broken ladder.** `revise()` passes
   `new_source_reliability=None`, which defaults to 0.5 — a fact about the *write path*,
   not evidence that stored slots lack provenance. Measured: `search` slots carry
   provenance on 2197 of 2197 rows.
2. **The ladder discriminates when the sides differ.** A user-stated fact (0.99) against
   a search attempt (0.5) produced `auto_resolved` with `resolved_value` = the user's
   value. The first rung fires.

See `experiments/conflict_ladder_value/result.md` and `result_audit.md`.

### The `grok` case was not a comparator failure

The plan argued a comparator *"cannot tell that `Echo`/`grok` is a naming mistake
(conflict 3835)"*, citing the near-rename as evidence for a reasoning pass.

That exact shape was tested. The ladder held the user's value. **The near-rename was
not luck** — it was the first rung working. It *read* as luck only because
`EXISTING_WINS` recorded itself as `pending` instead of `resolved`, which is now fixed
(see the status fix in `2026-10-01`).

### It would have duplicated a mechanism

Adding a model that reasons about conflicts, on top of a comparator that already
decides them correctly, is a second mechanism for one job — the same defect this
session removed four times: the `alerts` table duplicating frames, four sites writing
file content into memory, `FILE_CONTENT_HINT_SLOTS` defined twice, and (earlier) the
static rules that stood in for the model's judgement.

AGENTS.md: *lean on the model* does not mean *add a model* where the existing mechanism
is measurably sound. It means prefer the model's judgement over scripted logic when a
judgement is what is needed. Here the judgement is not needed — the comparison is.

### The backlog it targeted was largely an artifact

Plan D was written to work a queue of 279 "pending" conflicts. Measured:

```
279 pending
  252 (90%)  the slot still holds the existing value — the decision was made and applied
   27        genuinely open
```

The ladder had already decided 90% of them; the label said otherwise. A study task
would have spent its budget re-deciding decisions, on hand-labelled ground truth the
gating experiment's own plan admitted was not ground truth.

## What was kept from the plan

Two ideas in the plan are sound and survive independently of it:

- **A conflict is a fact-level disagreement; an alert is a communication event.** A
  conflict exists whether or not anyone is told; an alert exists because the agent
  decided to tell the user. A conflict escalated to an alert **links** to it via
  `about` and never duplicates it. This distinction is now in the code comments.
- **A conflict's decision inputs belong on the row.** The plan assumed this would be
  needed for its own reasoning; it turned out to be needed to audit the *existing*
  mechanism, and was added for that. See the provenance columns in `memory.store`.

## What replaced it

**The provenance columns** (`existing_source_reliability`, `new_source_reliability`,
`existing_confidence`, `new_confidence`, `existing_priority`, `new_priority` on
`conflicts`) and **the status fix**, which together make a conflict's decision
inspectable after the fact.

The remaining work, if it is ever wanted, is much smaller than this plan: **~27 rows**
that are genuinely open, now distinguishable from the 252 that were mislabelled. That
is a review, not a subsystem.

## The orphaned experiment

`experiments/conflict_reasoning_accuracy` was written solely to gate this plan. With the
plan closed it has no purpose and is deleted with it.

`experiments/conflict_ladder_value` is retained: it is what disproved this plan, its
audit harness is reusable, and its `result.md` documents the instrumentation gap that
the provenance columns closed.

---

## Original plan text (kept for the reasoning it records, superseded above)

The sections below are the plan as written on 2026-09-30. They are retained because the
*thinking* is worth keeping even though the conclusion was wrong — specifically the
distinction between a conflict and an alert, and the insistence that the agent should
not search on its own during study time without telling the user.


## Important: conflicts are kept

An earlier proposal was to delete the conflict system and replace it with a single
last-write-wins rule. **That is rejected.** Conflicts are kept, cleaned (Plan B), and worked
here. The distinction that must survive into the code and the docs:

- **A conflict is a fact-level disagreement.** It exists whether or not anyone is told.
- **An alert is a communication event.** It exists because the agent decided to tell the user.

A conflict escalated to an alert **links** to it (`about`) and does not duplicate it. Without
this distinction the two decay into sounding like "the agent's to-do list" — they are not.

## Why this is worth building (and how it is proven)

Today's resolution is a *comparator*, and a comparator cannot reason. Measured: of 2,377
auto-resolved conflicts, **2,377 (100%) resolved to `new_value`** — the last rung (recency)
decides every case, because extraction writes everything at `source_reliability=null` → 0.5,
so the first rung never discriminates.

A comparator also cannot tell that `Echo`/`grok` is a naming mistake (conflict 3835,
`identity_name.full_name`, now pending) or that `Austin`/`Austin, TX` is a truncation rather
than a disagreement. Most real conflicts are "is this even the same claim?" — a judgement
call, which is exactly what there is a model for.

**But this is a hypothesis, not an established fact**, so it is gated by **two** experiments,
run in order:

> 1. **`conflict_ladder_value`** (automated, no labels) — is the existing mechanism doing
>    anything, and how exposed are protected slots? Establishes whether there is a
>    meaningful baseline to beat and sizes the `grok` risk.
> 2. **`conflict_reasoning_accuracy`** (requires hand-labelled cases) — can the model
>    actually beat "always adopt new"?
>
> **If the reasoning pass does not beat the baseline by ≥15 percentage points, this plan is
> not built.** The pre-registered bar is in that experiment's plan, decided before any data
> was collected.

## The search question: settled

**The agent does not search during study time.** This is a privacy decision the user owns,
and AGENTS.md already requires explicit user action for external calls. It is also useless
for most of the queue, which is first-person facts (`Austin` vs `Austin, TX`) the web knows
nothing about.

Instead, when the agent cannot settle a conflict from memory, it **raises an alert asking
to look online**. The agent's memory is its own authority; the internet is the user's to
authorize; the alert is the bridge.

Note this conflicts with the other scheduled tasks, which always search (a monitor task
must not answer from yesterday's frames). The difference is deliberate and must be stated
in the task's own prompt: **a monitor task reports on the world; a study task reasons about
the agent's own memory.**

## Phases

### Phase 1 — Evidence assembly (~3 h)

A verdict from `existing_value` vs `new_value` alone is guesswork. Each conflict must be
presented with the evidence needed to judge it:

1. Both candidate values with their provenance (`source_type`, `source_reliability`,
   `confidence`, `priority`).
2. The slot history — what the value was before
   (`slot_history`: `old_value`, `new_value`, `reason`, `source_episode_id`).
3. The source episode(s) that produced each side, so the model can see what was said.
4. Any related slots on the same frame (context for the claim).

Reuses `get_conflicts_for_frame` and the existing `slot_history`; no new storage.

**Acceptance:** the assembled prompt for a conflict contains both values, both provenances,
and the originating episode text.

### Phase 2 — The reasoning task (~4 h)

1. A scheduled task on its **own timer** — `CONFLICT_RESOLUTION_INTERVAL_HOURS`, default 6h,
   like consolidation. Not folded into the daily tick: this is maintenance, not something
   the user wants in their morning briefing.
2. Batched: process a bounded set per run (`CONFLICT_MAX_PER_RUN`), so an unattended run
   cannot rewrite the whole brain in one pass.
3. The model returns a verdict per conflict:
   `ADOPT_EXISTING | ADOPT_NEW | MERGE | ESCALATE`.
4. **`ESCALATE` is a success, not a failure.** A model forced to pick a winner on a
   genuinely ambiguous case will invent one — the `grok` failure mode with more confidence.
   Leaving it pending, as an alert, is the correct outcome.
5. The prompt states the authority rule from AGENTS.md, so the model reasons with the same
   rule the rest of the system uses: user authoritative on their own world, internet on the
   external world, neither on the other's domain.

**Acceptance:** a run produces a verdict per conflict; `ESCALATE` is a reachable outcome;
the run is capped and reschedules.

### Phase 3 — Apply verdicts safely (~3 h)

1. **Protected slots are off-limits regardless of verdict.** `identity_name` and any
   `essential` slot cannot be resolved by this task. Even a correct-sounding verdict that
   overwrites the agent's own name is a failure that is not cheaply undone. Enforced in the
   runner, not in the prompt.
2. Applied verdicts go through the existing write path (`upsert_slot`), so confidence,
   `slot_history`, and the conflict record all behave as they do for any other change.
3. `ESCALATE` creates a Plan C alert linked via `about`.
4. Runs are recorded as memory like any scheduled task — a `daily_run`-style event frame,
   so "what did my memory review conclude?" is answerable.

**Acceptance:** an `identity_name` conflict is never auto-resolved; an applied verdict writes
history; an escalated conflict produces a linked alert; the run is queryable afterwards.

### Phase 4 — Feed the ladder (scope note)

The experiment may show that `source_type`/`source_reliability` should be threaded through
extraction so the existing ladder's first rung works. That is **not** part of this plan
until the experiment says so — it is named here so the finding is not lost, not so it is
pre-emptively built.

## What this plan does not do

- **Does not search.** See "The search question: settled".
- **Does not replace the write-path resolution.** Inline `resolve_conflict` keeps behaving
  as it does today, so the two mechanisms stay comparable while this is proven.
- **Does not delete the conflict system.** It works it.
- **Does not drain the queue in one pass.** Bounded per run by design.

## Requirements

**Functional**

| # | Requirement | Phase |
|---|---|---|
| R1 | Each conflict is presented with both candidate values, both provenances (`source_type`, `source_reliability`, `confidence`, `priority`), the slot history, and the originating episode text. | 1 |
| R2 | The task runs on its own timer (`CONFLICT_RESOLUTION_INTERVAL_HOURS`, default 6h), separate from the daily tick. | 2 |
| R3 | Processing is capped per run (`CONFLICT_MAX_PER_RUN`); an unattended run cannot rewrite the whole brain. | 2 |
| R4 | The model returns exactly one of `ADOPT_EXISTING`, `ADOPT_NEW`, `MERGE`, `ESCALATE`. | 2 |
| R5 | `ESCALATE` is a valid, non-failure outcome that leaves the conflict pending and creates a linked alert. | 2, 3 |
| R6 | The task's prompt states the AGENTS.md authority rule, so it reasons with the same rule as the rest of the system. | 2 |
| R7 | `identity_name` and any `essential` slot are never auto-resolved, regardless of verdict. | 3 |
| R8 | Applied verdicts go through `upsert_slot`, so confidence, `slot_history`, and the conflict record behave as for any other change. | 3 |
| R9 | The run is recorded as an event frame, so "what did my memory review conclude?" is answerable. | 3 |

**Non-functional**

| # | Requirement |
|---|---|
| N1 | **No external search during a study run.** No network call is made. Verified by test. |
| N2 | Off the hot path entirely — a background timer, never a chat turn. |
| N3 | Disable-able by `CONFLICT_RESOLUTION_INTERVAL_HOURS=0`, matching the other timers' convention. |
| N4 | The protected-slot guard is enforced in the runner, **not** in the prompt — a model instruction is not a guarantee. |
| N5 | Gated by **two** experiments, in order: `conflict_ladder_value` (does the comparator do anything?) then `conflict_reasoning_accuracy` (can the model beat it?). If the reasoning pass does not beat "always adopt new" by ≥15pp on the labelled sample, this plan is not built. |

**Constraints**

- **Does not duplicate the conflict queue.** An escalated conflict is linked via `about`.
- **Does not touch the inline write path.** The two mechanisms stay comparable.
- **The search prohibition is deliberate and differs from every other scheduled task**
  (which always search). The task's own prompt must state the difference: a monitor task
  reports on the world; a study task reasons about the agent's own memory.

## Dependencies and ordering

- **Depends on Plan B** — the queue must contain real disputes (277 expected, not 1,476).
- **Depends on Plan C** — the escalation target.
- **Gated by two experiments, in order:** `conflict_ladder_value` (automated — run first,
  it needs no labels), then `conflict_reasoning_accuracy` (needs hand-labelled cases).
  Both must pass before any work here begins.
- Within the plan: 1 → 2 → 3. Phase 4 is a scope note, not work.

## Test strategy

- `test_conflict_verdict_parsed.py` — a model verdict is parsed into one of the four outcomes.
- `test_conflict_escalate_is_valid.py` — an ambiguous conflict can escalate without a winner.
- `test_conflict_protected_slot_untouched.py` — an `identity_name`/`essential` conflict is
  never auto-resolved even with an `ADOPT_*` verdict.
- `test_conflict_verdict_writes_history.py` — an applied verdict writes `slot_history`.
- `test_conflict_run_is_capped.py` — a run never exceeds `CONFLICT_MAX_PER_RUN`.
- `test_conflict_escalation_creates_alert.py` — an `ESCALATE` produces a linked Plan C alert.
- `test_conflict_task_does_not_search.py` — no external call is made during a study run.

Gates: `pytest assistant/tests/test_daily_schedule.py assistant/tests/test_review_fixes.py`,
then the full suite, then `ruff check .`.

## Principle alignment

| Principle | How |
|---|---|
| Lean on the model | The verdict is the model's, not a ladder's — the whole justification for the plan. |
| Model-first correction | `ESCALATE` sends the unresolvable case to the user via the model's own reasoning, not a scripted fallback. |
| Scheduled tasks are memory | The run is an event frame; verdicts are ordinary slot changes. |
| Speed-first | Off the hot path entirely — a background timer, never a chat turn. |
| Safety & Privacy | **No search**, by design: the agent asks before touching the internet. |
| Stability | Each behaviour has a regression test; protected slots have a dedicated one. |
| Clean ship | No new table; reuses conflicts, `slot_history`, and frames. |

## Rollback

Phases 1–3 are additive: a new scheduled task, a prompt, a runner guard. Disabling it is
`CONFLICT_RESOLUTION_INTERVAL_HOURS=0` (the convention used by the other timers). Applied
verdicts write to `slot_history` and are individually revertible through the correction
path. No schema migration.
