# Known Issues

Deferred from the 2026-08-25 full memory+pipeline audit and re-triaged after the
2026-09-29 backend review remediation. Not blocking for a single-household
deployment, but the open items below should be addressed before the system
handles untrusted multi-user input.

## Closed by the 2026-09-29 remediation

- **A3** (resurrection kept stale type/confidence/source) — fixed; the tombstone
  path now binds the same tuple the INSERT uses. `test_merge_and_resurrection.py`.
- **A5** (heartbeat upsert delete+reinsert cascaded slot history and churned ids)
  — fixed; `upsert_scheduler_heartbeat` now uses an upsert that keeps the frame
  and slot ids stable. `test_p3_hygiene.py`.

## Open

### Bugs

- **A1**: correction branch + run_now: unguarded LLM calls after routing can 500
  mid-turn, stranding the user episode without its pair. Wrap and degrade like
  the generation path. (The streaming correction path no longer re-enters
  `chat()` — that duplicate-log defect is fixed — but the unguarded-call risk
  remains.)
- **A2**: brain import drops owner_user_id / essential / priority /
  last_strengthened_at (user isolation regresses across a round-trip);
  overwrite-mode episode inserts can violate FKs on foreign user ids.

### Missing features

- **A4**: consolidation doesn't rewrite episodes.frame_ids to survivors during
  merge — topic view shows pre-merge frame references.
- **A8**: search-extracted facts never link to an episode (no frame_ids /
  source_episode_id) — provenance gap in observatory.

### Tech debt

- **A7**: `/feedback`+`/correction` API field named `episode_id` actually
  receives a session id — rename or accept both once clients are updated.
- **A9**: dedupe recent-episode digest when session has <2 turns (double
  render); dedupe the 11 duplicate episode groups / 31 rows in live data
  (retry artifact, pre-embedding era); review the 13 open conflicts.
- **A10**: model drift: Frame lacks task columns; dead `slots.last_accessed_at`
  column (schema only, never read/written); `_apply_merge -> None` returns a
  dict; stale catch-up-guard comment in runner; LIKE `_` wildcard nit.

### Schema drift

- `slots.last_accessed_at` column exists in schema/migration but is never
  read or written. Migration stays in place for existing DBs; column is
  harmless dead weight.
