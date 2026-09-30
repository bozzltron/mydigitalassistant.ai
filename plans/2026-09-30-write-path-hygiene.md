---
date: 2026-09-30
status: active
estimated_hours: 6
---

# Plan B — Write-path hygiene: stop the system polluting itself

## Objective

The agent must not create bad data about its own world. A memory system that reasons over
its own filing errors produces confident nonsense, and every downstream measurement
becomes untrustworthy.

This plan makes the write path refuse blank/degenerate records, and stops the summarizer
from writing counters as if they were beliefs.

**Blocks Plan A** (which creates frames and would otherwise inherit the blank-name path)
**and Plan D** (which reasons over the conflict queue that this plan de-fouls).

## Measured damage (live brain, 2026-09-30)

```
empty frame names:          1      frame 4387 — from the link-sort turn
blank slot keys:            3
blank slot values:          3
'none'/'null' values:       1
frames missing embeddings:  88
total conflicts:            3,857
  └─ from conversation_summary frames: 3,307  (86%)
     ├─ pending:        1,199
     └─ auto-resolved:  2,108
pending conflicts:          1,476
  └─ from conversation_summary: 1,199  (81%)
```

`summarizer.py:_upsert_summary_frame` (lines 183–191) writes `turn_count`, `date_start`,
and `date_end` as slots, and **re-upserts all of them on every summarization run**:

```python
slots = {
    "summary": summary,                       # a whole paragraph, as one slot value
    "key_entities": ", ".join(key_entities),  # a rendered list
    "open_questions": ", ".join(open_questions),
    "session_id": session_id,
    "turn_count": str(turn_count),            # a counter
    "date_start": date_range[0],              # a moving timestamp
    "date_end": date_range[1],
}
```

A counter rewritten every run is not a belief, so every run "conflicts" with the last.
Frame 1406 alone accounts for 258 pending rows; frame 1348 for 226. The queue is dominated
by the summarizer diffing a document against itself.

This also explains the blank values: `key_entities` / `open_questions` render as `""` when
the list is empty, and empty strings are written as slot values.

The self-inflicted share is **3,307 of 3,857 (86%)** — and only a third of it is the
pending queue. The larger half (2,108) is auto-resolved rows recording a summarizer that
silently overwrote its own previous summary through the conflict path on every run.

## Phases

### Phase 1 — Refuse degenerate records at the write path (~2 h)

The invariant: **no frame without a name, no slot without a key or a value.** Model output
is untrusted input, so this is enforced where the write happens, not where the model is
asked nicely.

1. `resolve_or_create_frame` (`extractor.py:253`) rejects empty/whitespace names.
2. `apply_extraction` (`extractor.py:527`) drops slots and associations whose
   `frame_name` is empty, alongside the existing `value is None` and
   `RESERVED_SLOT_PREFIXES` guards.
3. The same guards on the search and correction paths
   (`apply_search_extraction`, `apply_correction`).
4. **Surfaced, not silent.** Log at WARNING with the offending name, following the
   reserved-`file_*` precedent — a model emitting blank names is misbehaving and that
   should be visible in the extraction summary, not look like "nothing was learned".
5. Tombstone frame 4387 (no slots, no edges, one episode link).

**Acceptance:** a payload with a blank `frame_name` / `key` / `value` produces no frame, no
slot, and no association, and logs a WARNING.

### Phase 2 — Summarizer stops writing counters as beliefs (~2 h)

Counters are useful (the UI can say "this summary covers 240 turns") but they are not
beliefs and must not travel the conflict path.

1. Write `turn_count`, `date_start`, `date_end` (and `session_id`) **directly** via a
   dedicated store method, bypassing `upsert_slot`'s `revise()`/conflict machinery.
   They are bookkeeping about the summary, not claims about the world.
2. Keep `summary` and `key_entities` / `open_questions` as slots — those genuinely are
   content — but **omit them when empty** rather than writing `""`.
3. `summary` is a paragraph in a single slot value. That is defensible (it is prose
   content, retrievable as one unit); the defect was only that counters shared its path.

**Acceptance:** running summarization twice on an unchanged session creates **zero** new
conflicts. Currently it creates one per counter per run.

### Phase 3 — Repair the existing damage (~1 h)

One-off, scripted, backed up first:

1. Re-index the 88 frames missing embeddings.
2. **Delete** every conflict row whose frame is a `conversation_summary` — **3,307 rows**
   (1,199 pending + 2,108 auto-resolved), not only the pending ones. Both halves are
   pollution:

   | Field group | Rows | Pending | Auto-resolved |
   |---|---|---|---|
   | bookkeeping (`turn_count`, `date_start`, `date_end`, `session_id`) | 1,172 | 1,118 | 54 |
   | content (`summary`, `key_entities`, `open_questions`) | 2,135 | 81 | 2,054 |
   | **total** | **3,307** | **1,199** | **2,108** |

   They are not disagreements — they are the summarizer diffing a document against
   itself. The 2,054 auto-resolved content rows are the summarizer silently overwriting
   its own previous summary through the conflict path on every run. Leaving them would
   mean the conflict ledger records thousands of events that never happened.
3. Report the count that remains genuinely pending — expected **277**, across `concept`
   (168), `entity` (89), `event` (20). That set is Plan D's real input.

**Acceptance:** `pending` conflicts drop from 1,476 to the genuinely-disputed set, and the
drop is itemized by cause.

**Backup is mandatory before this step and is not negotiable.** Unlike Phases 1–2 this is
not revertible by `git revert`, and the memory system's data health is the foundation the
rest of the system stands on. The backup is taken and its `integrity_check` verified
before any row is deleted.

### Phase 3c — the 92 unvectorised frames (diagnosed, resolved by the top-up)

Diagnosis first, because the naive "re-index 88 frames" would have hidden the cause.

**The 92 split into two different things:**

| Group | Count | Reality |
|---|---|---|
| Zero-slot frames | 34 | Created as **association endpoints** (`ai_control_system`, `kexp.org`, `news_summary_skill`). 35 of 35 appear in an association. Nothing to embed — not a bug. |
| Slot-bearing frames | 58 | Real misses: `open_meteo` (3 slots), `sxsx_music_festival`, `acl_music_festival`… |

**Cause of the 58:** both are non-chat write paths — `search` (27) and `scheduled_task`
(22). Chat extraction embeds via `apply_extraction`; the search path attempts embedding
*after* its pipeline inside a `search_timeout`-bounded `wait_for`, and the scheduled-task
path only explicitly embeds the `daily_run` frame. The safety net for exactly this — the
6-hourly `_run_embedding_topup` — **has not run since 2026-09-23**, because
`SCHEDULER_ENABLED=false`.

**Resolution: fix the mechanism, not the data.** `embed_stale_frames` checks the invariant
directly (a frame's stored vector count must equal what its slots imply) rather than
auditing call sites, so it catches both missing and stale vectors. Run manually:

```
episode top-up: 0 indexed
stale frame top-up: 100 re-indexed   (cap)
→ 2 remaining, then 61, then 0, 0, 0
```

Converged, and **idempotent** — three consecutive passes reported 0. Final state:

```
live frames missing a qwen3 vector:  0
episodes missing a qwen3 vector:     0
frame_embeddings:   qwen3-embedding:0.6b = 3,508   (sole model)
episode_embeddings: qwen3-embedding:0.6b = 2,592   (sole model)
```

The startup embedding audit (`main.py:_check_embedding_model_mismatch`) is now silent.

**What this establishes and what it does not.** It proves the top-up fixes the class of
defect. It does **not** prove it will keep up in production: the cap is 100 frames per run
on a 6-hour timer, and the job is currently off. Whether 100-per-6h is sufficient is
unmeasured, and the scheduler being disabled is Plan B Phase 4's subject.

**Nomic removal (was part of this phase).** The 3,643 non-`qwen3` rows
(`frame_embeddings` 1,714 + `episode_embeddings` 1,929) were stale supersets from the
nomic→qwen3 migration: every nomic-bearing frame already had a qwen3 vector
(`nomic_frames == both == 1,714`), so nothing was stranded and retrieval was unaffected.
The startup audit sanctions the prune explicitly ("the old vectors are dead weight, not a
defect… Safe to prune"). Deleted after a pre-check found **one genuinely stranded episode**
(1986, "Say hello in 3 words", 2026-09-24 — a 768-dim nomic-only vector from the migration
window), which was re-embedded to qwen3 (1024-dim) *before* deletion. Verified: no
production code reads `nomic-embed-text` by label.

**Acceptance:** zero live frames and zero episodes lack a vector under the configured
model; three consecutive top-up passes report 0; zero non-`qwen3` embedding rows remain.

### Phase 4 — Fold in the manual-run timeout (~1 h)

`POST /tasks/run-due` returned **504 at 300s** while the work completed successfully
(all six tasks finished, verified in the logs and as episodes). The scheduler itself is
fine — it is a background loop, unaffected by HTTP timeouts:

- `SCHEDULER_ENABLED=false` is why nothing had run since 2026-09-23 (heartbeat frozen at
  `2026-09-23T17:26:36`). The mechanism is **verified working** on manual trigger.
- The endpoint runs tasks serially in the request; Caddy's `response_header_timeout 300s`
  cuts the response before the work finishes.

Fix: make `run-due` return immediately with a job acknowledgement and let the existing
runner loop do the work, so the endpoint and the scheduler share one execution path. This
also removes the duplicate serial implementation in the endpoint.

**Acceptance:** `run-due` returns quickly; tasks still execute and appear as episodes.

## What this plan does not do

- **Does not touch conflict resolution.** The ladder and `resolve_conflict` are Plan D's
  subject. This plan only stops the system manufacturing conflicts.
- **Does not change the summarizer's output content**, only the mechanism it uses to
  store bookkeeping fields.
- **Does not carry the conflicts table's history forward for the self-inflicted rows.**
  Phase 3 deletes the 1,199 counter-conflicts. The pre-deletion backup is the record if
  they are ever needed.

## Requirements

**Functional**

| # | Requirement | Phase |
|---|---|---|
| R1 | No frame can be created with an empty or whitespace-only name. | 1 |
| R2 | No slot can be created with an empty key or an empty/`None` value. | 1 |
| R3 | No association can be created with an empty frame name on either side. | 1 |
| R4 | Guards apply on every write path: conversational, search, and correction extraction. | 1 |
| R5 | A rejected record is logged at WARNING with the offending name, and counted in the extraction summary — never silently dropped. | 1 |
| R6 | `turn_count`, `date_start`, `date_end`, `session_id` are written directly, bypassing conflict detection. | 2 |
| R7 | Empty `key_entities` / `open_questions` write no slot rather than an empty string. | 2 |
| R8 | Summarizing an unchanged session twice creates zero new conflict rows. | 2 |
| R9 | Frames missing embeddings are re-indexed. | 3 |
| R10 | All `conversation_summary` conflict rows are deleted (3,307 at time of writing). | 3 |
| R11 | `POST /tasks/run-due` returns without blocking on task execution; tasks still run to completion. | 4 |

**Non-functional**

| # | Requirement |
|---|---|
| N1 | Phase 3 takes a backup and verifies `integrity_check` **before** deleting any row. Non-negotiable — this is the system's memory, and this is the one irreversible step. |
| N2 | Phase 3 reports the drop itemized by cause, and the remaining genuine count (expected 277). |
| N3 | No new external network calls. |
| N4 | Cloud LLM prohibition respected — all inference stays local. |
| N5 | The `alerts`/`conflicts` tables are not merged or renamed by this plan. |

**Constraints**

- **Do not delete non-summary conflicts.** The 277 genuine pending are Plan D's input; they
  are the signal, and they must survive Phase 3.
- **Do not bind to anything but 127.0.0.1.** Unchanged, but stated because Phase 4 touches an
  endpoint.
- **Order is load-bearing.** Phase 1 and 2 must land before Plan A begins; Phase 3 before
  Plan D. Within the plan, 1 → 2 → 3 → 4.

## Dependencies and ordering

- **Blocks Plan A** — A creates frames and would inherit the blank-name path.
- **Blocks Plan D** — D reasons over the queue this plan de-fouls.
- **Independent of Plan C** (except that C's alert frames benefit from R1).
- **Phase 4** could ship separately if needed — it is unrelated to hygiene and was folded in
  only because it is a known defect in the same subsystem.

## Test strategy

- `test_blank_frame_name_rejected.py` — empty / whitespace / `None` frame names produce no
  frame, slot, or association, and log a warning.
- `test_blank_slot_fields_rejected.py` — blank key or value is dropped at the write path.
- `test_summary_counters_do_not_conflict.py` — summarizing the same session twice creates
  no new conflict rows (currently creates one per counter).
- `test_summary_omits_empty_lists.py` — a session with no open questions writes no
  `open_questions` slot rather than an empty one.
- `test_run_due_returns_immediately.py` — the endpoint acknowledges without blocking on
  task execution.

Gates: `pytest assistant/tests/test_daily_schedule.py assistant/tests/test_review_fixes.py`,
then the full suite, then `ruff check .`.

## Principle alignment

| Principle | How |
|---|---|
| Clean ship | The duplicate serial task-run implementation in `run-due` is removed in favour of the runner's path. Phase 3 removes pollution rather than archiving it. |
| Stability | Each defect gets a regression test named for it; Phase 3 is scripted and backed up. |
| Lean on the model | Unchanged — this constrains the *store*, not the model's reasoning. No scripted user-facing text. |
| Safety & Privacy | No new external calls. Phase 3 writes a backup before mutating. |
| Accuracy of the user's world | This is the plan that makes the agent's beliefs about the user trustworthy rather than its own filing noise. |

## Rollback

Phases 1–2 are validation guards and a storage-path change; revert with `git revert`, no
schema migration. Phase 3 **deletes rows and is the one irreversible step in either
plan** — it must be preceded by a backup whose `integrity_check` passes, and the same
pattern is used for merges in `_run_consolidation`: mutate nothing unprotected. Phase 4
changes one endpoint's response shape.
