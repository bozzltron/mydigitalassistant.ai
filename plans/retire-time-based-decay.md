---
date: 2026-09-29
status: done
estimated_hours: 6
---

# Retire time-based decay; make memory maintenance frequent and non-destructive

**Implemented 2026-09-29.** Summary of what landed:

- Deleted `assistant/backend/memory/gc.py` and its 16-test `test_gc.py`.
- Removed the GC timer, `GC_INTERVAL_S`, `_run_memory_gc`, and `_is_new_week`
  from `scheduler/runner.py`; removed the `assistant db gc` CLI command.
- Merges are **ad hoc**: `_run_consolidation` plans, and applies as soon as there
  are merges (bounded by the per-run cap). Merging is not gated on the backup
  clock.
- Backups are on their **own 12h clock** (`_run_backup_snapshot`,
  `BACKUP_INTERVAL_HOURS=12`), so snapshot count does not scale with merge
  frequency. A merge that happens without a fresh snapshot takes one first, so a
  merge is never applied unprotected.
- Embedding top-up is separate and every 6h, with no backup.
- Intervals: consolidation 6h, summarization 6h; new
  `EMBEDDING_TOPUP_INTERVAL_HOURS=6` and `BACKUP_INTERVAL_HOURS=12`.
- Added `assistant/tests/test_memory_maintenance.py` (5 tests); replaced the GC
  vector-purge test with an explicit-forget-retrieval test.
- Docs updated: `assistant/AGENTS.md`, `docs/FILES.md`, `docs/TESTING.md`,
  `docs/BACKUP_SYSTEM.md`, `.env.example`.

## Objective

Two coupled changes with one theme — maintenance should *enrich* memory, never
subtract from it, and its cadence should match its cost:

1. **Nothing is forgotten unless the user says so.** Remove the weekly garbage
   collector that lowers slot priority and soft-deletes frames (dropping their
   embedding vectors).
2. **Run the good maintenance more often, and stop paying for it.** Embedding
   freshness and duplicate-frame merging are valuable but currently run only every
   12h *and* take a full DB backup every time. Split them: run the cheap,
   non-destructive part every 6h, and take a backup only when there is actually
   destructive work to protect.

The second change is not cosmetic: four runs/day with a backup each is 12
snapshots/day of the household brain for work that is mostly idempotent embedding
top-up.

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

Part A removes the destructive timer. Part B splits the valuable maintenance so
frequency no longer costs a backup.

### A. Delete the decay subsystem

**A1. Delete `gc.py` and its callers.**

The decision is to remove it outright, not reduce it to a reporter — a module
whose only remaining job is to report that it does nothing is not worth keeping.

- Remove `compute_decayed_priority`, the slot-decay loop, the stale-frame
  soft-delete loop, and the `frame_embeddings` deletion.
- `GcReport`, `DECAY_AFTER_DAYS`, `DECAY_RATE`, `SOFT_DELETE_THRESHOLD`,
  `FRAME_STALE_PRIORITY`, `FRAME_STALE_DAYS` all go with it.

**A2. Remove the scheduler timer.**

- `scheduler/runner.py`: drop `_run_memory_gc`, the startup call (`:430`), the
  weekly branch (`:445-447`), `last_gc`, `GC_INTERVAL_S` (`:38`), and
  `_is_new_week` (its only caller was GC; remove the test too).

**A3. Remove the CLI command.**

- Delete `assistant db gc` (`cli/app.py:760,1031`, `cli/db.py:289,410,431`).
  With no decay it has nothing to do; a `gc` that prints zeros is misleading.

**A4. Keep explicit forgetting.**

- `forget_frame` / `forget_slot` stay and remain the only way memory is removed
  without user-initiated deletion of a frame/file.
- Retrieval already excludes `deleted_at IS NOT NULL` frames
  (`retrieval.py:589`); verify with a test (below).

### B. Split maintenance: cheap-and-frequent vs destructive-and-protected

Currently `_run_consolidation` does four things in one run, every 12h, each run
taking a full DB backup:

1. `_backup_db(...)` — full snapshot; **aborts the whole run if it fails**
2. episode embedding top-up (cap 100)
3. stale-frame re-embedding (cap 100)
4. the merge pass (tombstones losers)

Steps 2–3 are idempotent, non-destructive, and are the ones that *want* to be
frequent (a frame whose slots changed is only findable by one chunk until
re-embedded — so freshness is user-visible recall quality). Step 4 is the only
destructive one, and the backup exists solely to protect it.

**B1. Run the embedding top-up every 6 hours, with no backup.**

Extract steps 2–3 into their own function and schedule them on a 6h interval
(`EMBEDDING_TOPUP_INTERVAL_HOURS=6`, new setting). No snapshot: they mutate only
embedding rows, which are idempotent and regenerable.

**B2. Run the merge pass on its own cadence, backing up only when it will
actually mutate.**

- Keep steps 1 + 4 together, but compute the plan **first** (it is already
  computed via `dry_run=True` at `:400`). If the plan has zero merges, skip the
  backup entirely — there is nothing to protect.
- Only if `plan.planned_merges` is non-empty do `_backup_db(...)` and then apply.
- Set the merge cadence to 6h as well (`CONSOLIDATION_INTERVAL_HOURS=6`). With
  the lazy backup this is *cheaper* than today: a quiet brain writes ~0 backups
  per interval; a busy one writes one per interval that has real merge work.

Net backup behaviour: from **12 fixed snapshots/day → back up only when merges
happen** (bounded by `CONSOLIDATION_BACKUPS_TO_KEEP`). The circuit breaker
(`consolidation_max_merges_per_run`) and the abort-on-backup-failure semantics
are preserved — they just now sit behind the "is there work?" check instead of in
front of it.

**B3. Summarization cadence** — leave the interval configurable; set
`SUMMARIZATION_INTERVAL_HOURS=6` for consistency, but note it is self-gating: it
only summarizes sessions with ≥`SUMMARIZATION_MIN_TURNS` (default 10) and caps
at `SUMMARIZATION_MAX_SESSIONS_PER_RUN`. A 6h poll with nothing eligible is a
cheap no-op, so the interval change is low-risk but also low-value unless
`MIN_TURNS` is lowered.

### C. Settings added/changed

| Setting | New default | Purpose |
|---|---|---|
| `CONSOLIDATION_INTERVAL_HOURS` | `6` | merge-pass cadence |
| `EMBEDDING_TOPUP_INTERVAL_HOURS` | `6` | embedding top-up cadence (new) |
| `SUMMARIZATION_INTERVAL_HOURS` | `6` | summarization cadence |

`GC_INTERVAL_S` is removed. Document all three in `.env.example`.


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
