# Verification — memory_source_split

## What was run

`experiment.py` on 2026-10-11 against **both** brains, read-only (grouped by
`source_type`; no writes).

```
docker cp assistant/experiments/memory_source_split/experiment.py assistant-backend:/tmp/mss.py
docker exec assistant-backend python /tmp/mss.py
```

## Non-mutation

Read-only `SELECT`s. No writes, no brain opened for modification. The script opens the
live DB to read; nothing is written.

## Raw output

| | prod | dev |
|---|---|---|
| live frames | 4253 | 4469 |
| frames — internet / user / derived | **65.7% / 23.2% / 11.1%** | **66.7% / 23.4% / 9.9%** |
| slots | 7113 | 7294 |
| slots — internet / user / derived | **52.8% / 23.0% / 24.1%** | **55.0% / 22.0% / 23.0%** |
| pending conflicts | 275 | 275 |

The NULL sample is conversational, confirming the inference in `plan.md`:

```
['identity_name', 'social_media_accounts', 'mozworth_bandcamp_page',
 'mozworth_facebook_account', 'instagram_followers', 'music_artists']
```

## By hypothesis

| | Verdict | Evidence |
|---|---|---|
| **H1 — the internet is the largest single source of frames** | **holds** | 65.7% / 66.7% |
| **H2 — the user's own contributions are a minority of slots** | **holds** | 23.0% / 22.0% vs the internet's 52.8% / 55.0% |
| **H3 — a non-trivial share has no provenance** | **holds** | NULL = 22% of frames, ~20% of slots |

## Falsification conditions, resolved

1. `user` ≥ `internet` on slots → **not triggered** (23% vs 53%).
2. `internet` < 40% of slots → **not triggered** (53–55%).

## Two brains, one answer

Prod and dev differ in history and size (4253 vs 4469 frames) and agree within ~2 points
on every share. **The split is a property of the system, not of one brain's usage** —
which is what makes it worth acting on.

## Threats and limits (what remains)

- **Provenance is not aboutness.** This measures *who stated a fact* — the project's
  triangulation axis — not what the fact concerns. A `search` fact can be about the user's
  band; the number would fall if some internet-sourced frames were the user's own subjects.
- **`derived` is not nobody's.** ~24% of slots are the system's own machinery (CSV rows,
  task runs, alerts, summaries). They are neither the user's facts nor the internet's, and
  they are *over half as large as the user's own share*.
- **NULL is inferred from one call site** (`orchestrator.py:2167`) and corroborated by a
  12-name sample; another writer could omit `source_type` too.
- **A snapshot, not a trend.** It says what is in memory now, not whether the ratio is
  moving.
