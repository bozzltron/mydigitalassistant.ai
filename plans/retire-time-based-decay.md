---
date: 2026-09-29
status: active
estimated_hours: 4
---

# Retire time-based memory decay

## Objective

Nothing in the agent's memory should be forgotten unless the user says so.
Today the memory system runs a weekly garbage collector that, unprompted,
lowers slot priority and soft-deletes frames (dropping their embedding vectors).
We will remove that autonomous behavior and keep only *explicit* forgetting.

Two user-facing goals drive this:

1. **"It remembers" must mean it.** A fact disappearing on a timer is the single
   most trust-damaging behavior a memory assistant can have, and it is
   undetectable until the user is let down by it.
2. **Less machinery.** The decay subsystem is large relative to what it does,
   and what it does is largely inert (see Findings).

## Findings (verified by reading the code)

1. **Decay is reachable only by already-forgotten data.**
   - `upsert_slot` writes every new slot with `priority=0.5`, and nothing lowers
     priority in normal operation.
   - The slot-decay gate is `0 < priority < 0.5` (`gc.py:67`) and the
     frame-delete gate is `priority < 0.2` (`gc.py:118`). Both exclude `0.5`.
   - The only writers of a sub-0.5 priority are `forget_frame`/`forget_slot`
     (which set exactly `0`, and are already skipped) and `run_gc` itself.
   - **Therefore the weekly GC can only act on data the user already explicitly
     forgot.** It is redundant with the forget action, just delayed.

2. **There is no capacity pressure to relieve.**
   - Retrieval fetches `top_k_direct * 2` candidates and caps the graph walk at
     `max_graph_frames=7`, so roughly 10 frames reach the prompt no matter how
     large memory grows (`retrieval.py:328,380`).
   - The `frame_budget` experiment shows recall *peaks* at 10 frames and is worse
     at 20/40. More memory does not mean a bigger prompt; it means better
     ranking. Decay's classic justification (bounded store, cheap lookup) does
     not apply here.

3. **A better selector already exists.** Retrieval ranks by
   `similarity × confidence × priority` (`retrieval.py:357`). Relevance is the
   honest selector; an age-based curve is a second, cruder one that can disagree
   with it (an old-but-perfect match loses to a recent-but-mediocre one).

4. **The one automatic destructive action is silent.** `run_gc` sets
   `deleted_at` and deletes `frame_embeddings` rows with no user-visible signal.

## Non-goals

- **Do not touch retrieval's `graph_decay`** (`retrieval.py:253`). That is a
  per-hop relevance multiplier within a single query's graph walk — completely
  unrelated to memory aging. The name collision is the only connection.
- **Do not remove `forget`.** Explicit forgetting is the feature we are keeping.
- **Do not add a replacement time-based mechanism.**

## Changes

### 1. Reduce `run_gc` to a no-op-ish reporter (or delete it)

Preferred: **delete the destructive paths** and keep `run_gc` as a
bookkeeping/diagnostic that reports what *would* be affected, never mutating.

- Remove `compute_decayed_priority` and the slot-decay loop.
- Remove the stale-frame soft-delete loop and its `frame_embeddings` deletion.
- `GcReport` shrinks accordingly (drop `decayed`, `soft_deleted`,
  `frames_soft_deleted`, or repurpose to counts-only).
- Decide whether `gc.py` still earns its place. If nothing remains, delete the
  module and its callers rather than keeping an empty shell (Clean ship).

### 2. Remove the scheduler's GC timer

- `scheduler/runner.py`: drop `_run_memory_gc`, the startup call (`:430`), the
  weekly branch (`:445-447`), `last_gc`, and the now-unused `GC_INTERVAL_S`
  (`:38`).
- Keep `_is_new_week` only if another timer uses it; otherwise remove and update
  `test_phase9_scheduler_consolidation.py`.

### 3. CLI

- `assistant db gc` (`cli/app.py:760`, `cli/db.py:289`) must either go, or be
  re-described honestly. Recommendation: **remove the command** — with no decay
  there is nothing for it to do, and a `gc` command that reports zeros is
  misleading. Update the `db` subcommand help and the `status, gc, consolidate,
  reembed` list (`cli/db.py:431`).

### 4. Keep explicit forgetting

- `forget_frame` / `forget_slot` stay and remain the only way memory is removed
  without user-initiated deletion of a frame/file.
- Verify retrieval still excludes `deleted_at IS NOT NULL` frames
  (`retrieval.py:589` already does) so a deliberate forget stays forgotten.

## Tests

- **Delete** tests that pin the removed behavior:
  - `assistant/tests/test_gc.py` — the decay-math and stale-frame tests
    (retain any that cover explicit forget, if present).
  - `assistant/tests/test_review_fixes.py::test_gc_purges_vectors_of_tombstoned_frames`
    — the vector-purge-on-GC behavior no longer exists. **Replace** it with a
    test proving explicit forget still drops the frame from retrieval, so the
    guarantee survives in the form that remains.
- **Add** regression tests:
  - `test_memory_is_not_decayed_over_time`: a slot created with default priority
    and an old `last_strengthened_at` (simulate a year) is untouched by any
    scheduled maintenance path — i.e. nothing in the scheduler mutates it.
  - `test_explicit_forget_removes_from_retrieval`: `forget_frame` → the frame is
    absent from `Retriever.retrieve` results (guards the behavior we keep).
  - If `run_gc` is retained as a reporter: `test_run_gc_is_non_destructive` —
    a frame at low priority and old access is still present after a run.
- **Update**: `test_phase9_scheduler_consolidation.py` if `_is_new_week` is
  removed.

## Docs to update (alignment)

- `assistant/AGENTS.md:416-417` — the `gc.py` description ("slot priority decay
  + stale-frame soft-delete ... runs weekly") must become the new truth, or the
  entry goes.
- `assistant/AGENTS.md:326` — "memory GC weekly" in the housekeeping list.
- `docs/FILES.md:88` — "`forget_frame` (priority → 0) is only for GC" is wrong
  once GC is gone; `forget_frame` becomes a plain explicit forget.
- `docs/TESTING.md:155` — the `test_gc.py` row.
- `docs/SECURITY.md` / `assistant/SECURITY.md` if they claim decay protects the
  brain (check before editing).
- `README.md` / `docs/RELEASE_NOTES.md` — if either mentions automatic memory
  management, correct it. (Release notes describe shipped behavior; a post-alpha
  change should be noted, not silently rewritten.)

Explicitly **not** changed: `ARCHITECTURE.md` and the `experiments/*` docs that
discuss retrieval `graph_decay` — those describe a different mechanism.

## Rollback

The change removes behavior rather than adding it, so rollback is
`git revert` of the single commit. No schema change and no data migration: the
`priority` column and `deleted_at` remain, so existing tombstones stay
tombstoned and existing low-priority rows are simply never auto-decayed again.
If a future need for a memory-pressure valve appears, it should be
*relevance*-based (drop vectors for frames that never match), not age-based, and
that would be a new plan.

## Acceptance criteria

- [ ] No scheduled or automatic path mutates `priority` or sets `deleted_at`.
- [ ] `upsert_slot` remains the only writer of default `priority=0.5`; nothing lowers it except explicit forget.
- [ ] Explicit `forget_frame`/`forget_slot` still work and are covered by tests.
- [ ] `ruff check .` clean; full `pytest assistant/tests/` passes; `./run_ci.sh` green.
- [ ] Every doc listed above matches the new behavior (no stale "weekly GC" claim remains).
- [ ] Frontend unaffected (verify no UI references the GC report).
