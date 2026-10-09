# Result — do search queries repeat enough to justify a response cache?

**No. Do not build a query-keyed response cache.** The 39% repeat rate is real, but
it is **scheduled tasks**, and scheduled tasks are the one class that must *not* be
cached: their whole purpose is to find new information.

## By hypothesis

| | Verdict | Evidence |
|---|---|---|
| **H1 — repeat rate ≥ 20%** | **holds on paper** | 39% (58/147). |
| **H2 — repeats are cross-session** | **holds** | 56 of 58 at 7d are cross-session; median gap ≈ 24.0h. |

## The pre-registered falsification, resolved — and overridden

Condition 3 said "repeat rate ≥ 20% **and** 24h hit rate ≥ 15% → build it." The raw
numbers meet it (39%, 16%). **The mechanism analysis overrides it:**

```
interactive: turns=72  unique=71  repeat=  1%   24h hits=1  (1%)
  scheduled: turns=75  unique=18  repeat= 76%   24h hits=23 (31%)
```

Split by session kind, the entire repeat rate lives in scheduled work:

- **Interactive turns barely repeat (1%).** A cache would remove ~1% of the
  searches a person actually triggers — not worth the code, the TTL, or the
  staleness risk.
- **Scheduled turns repeat 76%** — 18 daily tasks re-issuing the same query — but
  they run with `force_search=True` precisely because *a standing task always
  checks for new information*. Caching them would serve yesterday's results to a
  monitor whose job is to notice today's. The saving and the harm are the same
  turns.

So the 39% is not headroom. It is the set of searches that most need to be fresh.

## Verdict

- **The cache does not pay.** Build nothing.
- **The real quota driver is scheduled-task volume**, not query duplication:
  scheduled runs are **51% of all search turns** from only **18 unique queries**.
  If Brave quota matters, the lever is the number/cadence of daily tasks (or
  routing them to the free local SearXNG), **not** a response cache.

This is a negative result that saved the work: a query-keyed cache would have
looked justified by the headline 39%, and would have quietly made every daily
monitor report stale.

## What is still unmeasured

- **Whether scheduled tasks should use a cheaper backend.** 51% of Brave requests
  come from 18 recurring task queries; routing those to SearXNG would cut quota
  roughly in half, at some cost to result quality. A separate experiment.
