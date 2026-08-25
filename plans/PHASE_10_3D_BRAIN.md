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

## Ops notes

- Backend exit 137 (2026-08-25): host-memory pressure while heavy
  build/test containers ran concurrently with live use. `restart:
  unless-stopped` self-heals; avoid running full pytest/builds while the
  household is chatting.
- Verify deploys via `backend_rev` field on `/memory/search`.
