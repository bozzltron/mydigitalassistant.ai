# Known Issues

Deferred from the 2026-08-25 full memory+pipeline audit. Not blocking, but should
be addressed before the system handles untrusted multi-user input.

## Bugs

- **A1**: correction branch + run_now: unguarded LLM calls after routing can 500
  mid-turn, stranding the user episode without its pair. Wrap and degrade like
  the generation path.
- **A2**: brain import drops owner_user_id / essential / priority /
  last_strengthened_at (user isolation regresses across a round-trip);
  overwrite-mode episode inserts can violate FKs on foreign user ids.
- **A3**: resurrection (`create_frame` on a tombstoned name) keeps stale
  type/confidence/source; IntegrityError fallback mints `__uNone` suffix for
  anonymous callers.
- **A5**: heartbeat upsert uses delete+reinsert on UNIQUE(name) — cascades
  away slot history and burns ids each beat; switch to ON CONFLICT UPDATE.

## Missing features

- **A4**: consolidation doesn't rewrite episodes.frame_ids to survivors during
  merge — topic view shows pre-merge frame references.
- **A8**: search-extracted facts never link to an episode (no frame_ids /
  source_episode_id) — provenance gap in observatory.

## Tech debt

- **A7**: `/feedback`+`/correction` API field named `episode_id` actually
  receives a session id — rename or accept both once clients are updated.
- **A9**: dedupe recent-episode digest when session has <2 turns (double
  render); dedupe the 11 duplicate episode groups / 31 rows in live data
  (retry artifact, pre-embedding era); review the 13 open conflicts.
- **A10**: model drift: Frame lacks task columns; dead `slots.last_accessed_at`
  column (schema only, never read/written); `_apply_merge -> None` returns a
  dict; stale catch-up-guard comment in runner; LIKE `_` wildcard nit.

## Schema drift

- `slots.last_accessed_at` column exists in schema/migration but is never
  read or written. Migration stays in place for existing DBs; column is
  harmless dead weight.
