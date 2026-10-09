# Experiment: do search queries repeat enough to justify a response cache?

## Status

**PRE-REGISTERED. No data collected yet. No result may be written until this
document is committed.**

## Question

A Brave turn spends **two** API requests (web + images), and the free allowance is
~1,000 requests/month (~500 turns). A response cache keyed by query would spend
one request per *unique* query instead of one per *turn*. Whether that pays
depends on how often queries actually repeat — which is unmeasured. This measures
it before anything is built.

## Hypotheses

- **H1 (repeat rate — the decisive one).** The share of search turns whose query
  has been asked before is **≥ 20%**, i.e. a cache would remove at least a fifth
  of requests.
- **H2 (locality).** Repeats are dominated by **cross-session** recurrence
  (scheduled/daily work and returning users), not same-session re-asks — so a
  cache needs a TTL measured in hours-to-days, not seconds.

## Variables

- **Source:** `episodes.search_info` on a read-only copy of the brain — the
  `query` and `backend` of every turn that actually searched.
- **Metric A — exact repeat rate:** `1 - unique/total`, over case/whitespace-
  normalised queries.
- **Metric B — TTL-windowed hit rate:** for a query occurrence, the share that had
  an identical query earlier within a window of **1h / 24h / 7d / forever**. This
  is the realistic saving, since a cache with a TTL expires.
- **Metric C — locality:** of repeat pairs, same-session vs cross-session.
- **Metric D — backend mix:** how many stored searches were Brave (the only
  quota-bearing backend) vs SearXNG.

## What counts as evidence

- **The TTL-windowed hit rate is the number that decides.** The repeat rate is the
  ceiling; a 24h window is the realistic design.
- Requests saved ≈ hits × requests-per-search (2 for Brave, 1 for SearXNG).

## Falsification conditions

1. exact repeat rate **< 10%** → a cache does not pay; do not build it.
2. 24h-windowed hit rate **< 10%** → a short TTL does not help; report and stop.
3. repeat rate ≥ 20% **and** 24h hit rate ≥ 15% → the cache is worth building.

## Method

Read-only against a brain copy; `preflight.py` gates the run (experiment DB is not
the live DB, `/live` absent, a restorable backup exists, the copy's digest is
recorded). Table digests are captured before and after.

```
EXP_DB=/exp/assistant.db python assistant/experiments/search_query_reuse/experiment.py
```

`result.md` is written only after `verification.md`.

## Threats and limits

- **Exact match understates reuse.** The distiller may phrase the same need
  differently across turns, so the measured repeat rate is a **lower bound** on
  how much a semantic cache could save. It is the honest number for a *literal*
  key cache.
- **Persistence is recent.** `search_info` was added to episodes recently, so the
  sample is the turns since then, not all history. Recorded so the window is
  known.
- **The dev brain is not a production workload** — it carries experiment and
  testing traffic, which may inflate or deflate repetition.
- **A hit is not always the same need** ("weather" asked twice in a day is a
  legitimately fresh request). A TTL bounds that staleness; it does not eliminate
  it.
