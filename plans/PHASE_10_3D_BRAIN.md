# Phase 10 — Remaining Work

Status: in progress
Date: 2026-08-25

Deploy of the consolidation/search/brain-search fixes and the 3D fly-through
brain view are done and verified live (see git log `11c1ee2..ec416df`).
Completed phase plans were pruned; history lives in git.

## Review follow-ups (from the pre-commit code review)

Ordered:

- **M3**: `_apply_merge` spans several short-lived connections; make
  copy+redirect+tombstone one transaction (or per-merge cursor like the
  strengthen pass) so retries can't inflate confidence without evidence.
- **M5**: application-level lock (metadata lease row) so CLI consolidate,
  scheduled consolidation, and GC can't mutate concurrently.
- **L5**: rewrite episodes.frame_ids to survivors during merge so topic view
  shows merged history (done by hand for M&W; automate it).
- **L3**: bound `search_frames_lexical` candidate SQL once corpora grow.
- **T4**: add generic-name absorption test for consolidation pass 3.
- Cosmetic: SearXNG filler pattern hits hyphenated compounds ("hi-fi").

## Review follow-ups (from the 2026-08-25 full memory+pipeline audit)

Fixed same day (locked by `test_review_fixes.py`): feedback no-op
(session→memory-turn resolution), /memory/search similarity double-inversion,
graph-walk tombstone leak, scheduled-task assistant episodes + `resume`
prompt corruption, episode top-up outside the consolidation breaker, WAL
checkpoint busy check, busy_timeout on all connections, GC vector purge,
forgotten-frame semantic exclusion, cross-user history guard, INFO logging.

Deferred:

- **A1**: correction branch + run_now: unguarded LLM calls after routing can
  500 mid-turn, stranding the user episode without its pair. Wrap and
  degrade like the generation path.
- **A2**: brain import drops owner_user_id / essential / priority /
  last_strengthened_at (user isolation regresses across a round-trip);
  overwrite-mode episode inserts can violate FKs on foreign user ids.
- **A3**: resurrection (`create_frame` on a tombstoned name) keeps stale
  type/confidence/source; IntegrityError fallback mints `__uNone` suffix for
  anonymous callers.
- **A4**: consolidation still doesn't rewrite episodes.frame_ids (=L5 above).
- **A5**: heartbeat upsert uses delete+reinsert on UNIQUE(name) — cascades
  away slot history and burns ids each beat; switch to ON CONFLICT UPDATE.
- **A6**: dead config knobs (`conflict_auto_resolve`, `search_tls_cert`) —
  wire or remove. Legacy `assistant/.env.example` stub advertises one.
- **A7**: `/feedback`+`/correction` API field named `episode_id` actually
  receives a session id — rename or accept both once clients are updated.
- **A8**: search-extracted facts never link to an episode (no frame_ids /
  source_episode_id) — provenance gap in observatory.
- **A9**: dedupe recent-episode digest when session has <2 turns (double
  render); dedupe the 11 duplicate episode groups / 31 rows in live data
  (retry artifact, pre-embedding era); review the 13 open conflicts.
- **A10**: model drift: Frame lacks task columns; dead `slots.last_accessed_at`;
  `_apply_merge -> None` returns a dict; backend_rev default 1 vs actual 2;
  stale catch-up-guard comment in runner; LIKE `_` wildcard nit.

## Ops notes

- Backend exit 137 (2026-08-25): host-memory pressure while heavy
  build/test containers ran concurrently with live use. `restart:
  unless-stopped` self-heals; avoid running full pytest/builds while the
  household is chatting.
- Verify deploys via `backend_rev` field on `/memory/search`.
