# Verification — search_query_reuse

## What was run

`experiment.py` against a **read-only copy** of the dev brain on 2026-10-09. The
copy was staged from the `mydigitalassistantai-dev_assistant-data` volume (`.db` +
`-wal` + `-shm`) into scratch space; the live volume was **not** mounted into the
experiment container.

```
docker run --rm -v <scratch>/exp:/exp -v <scratch>/backups:/backups:ro \
  -v $(pwd):/app -w /app -e EXP_DB=/exp/assistant.db -e EXP_BACKUP_DIR=/backups \
  -e DB_KEY=… assistant sh -c \
  'python assistant/experiments/preflight.py && python assistant/experiments/search_query_reuse/experiment.py'
```

## Pre-flight (all PASS)

```
[PASS] experiment_db_is_not_live_db: /exp/assistant.db vs /live/assistant.db: distinct
[PASS] live_volume_not_mounted: /live is not present in this container
[PASS] restorable_backup_exists: 1 usable .assistant-brain file(s) in /backups
[PASS] experiment_db_readable_and_recorded: sha256=db0574db15f3e72c… size=111943680
```

## Non-mutation

Table digests identical before and after: `frames 4401, slots 7065, associations
4411, episodes 3701, slot_history 14108, conflicts 971`. `db_mutated: NO`.

## Raw output

```
search turns: 147   unique queries: 89   repeat rate: 39%
backend mix:  {'brave': 147}

windowed cache hits:
       1h: hits=3    (   2%)  same-session=2   cross-session=1    median gap=1721.0s
      24h: hits=24   (  16%)  same-session=2   cross-session=22   median gap=85379.5s
       7d: hits=58   (  39%)  same-session=2   cross-session=56   median gap=86420.0s
  forever: hits=58   (  39%)  same-session=2   cross-session=56   median gap=86420.0s

by session kind:
  interactive: turns=72   unique=71   repeat=   1%  24h hits=1 (1%)
    scheduled: turns=75   unique=18   repeat=  76%  24h hits=23 (31%)
```

The 7d and forever windows are identical: **every repeat happens within 7 days**,
and the median gap is 86,420s ≈ **24.0h** — a daily cadence.

## The mechanism check that decided it

The top repeated queries are all scheduled tasks, and every one of their
occurrences is in a `scheduled-*` session:

```
  6x (scheduled 6)  new job postings summary
  5x (scheduled 5)  earth environmental data daily report
  4x (scheduled 4)  latest ai developments 2024 tech news
  4x (scheduled 4)  monitor and log submission deadlines… mozilla calendar initiative
  …
```

So the headline 39% is not user re-asking. It is **18 daily tasks re-issuing the
same query**, and they are 51% of all search turns.

## Threats and limits

- **Exact match understates reuse** — a lower bound for a literal key cache, as
  pre-registered.
- **Persistence window** — `search_info` is recent; this is the turns since it was
  added, not all history.
- **Dev-brain workload** — heavy scheduled-task and test traffic; a production
  household might weight interactive vs scheduled differently, but the *mechanism*
  (scheduled tasks repeat; interactive turns do not) is structural, not incidental.
- **All 147 were Brave**, so the quota is genuinely at stake here — the finding is
  not an artifact of a free backend.
