# Experiment: use more of Brave's LLM Context — budget rebalance and freshness

## Status

**PRE-REGISTERED. No data collected yet. No result may be written until this
document is committed.**

## Question

The LLM Context endpoint returns **966–5585 chars per URL** (5–14 chunks), but we
feed the extractor at most **1000 chars per result** (`_SEARCH_RESULT_TEXT_CHARS`)
and the prompt shows only the first chunk of the top 3. We also never set the
`freshness` parameter, so nothing filters by page age. Does using more of the
content we already receive — and filtering by freshness — improve the facts the
assistant extracts, at the **same request cost**?

## Hypotheses

- **H1 (budget rebalance — decisive).** Feeding **fewer results with more content
  each** at the same token budget yields more accurate slots per turn than the
  current 8 × ≤1000 chars.
- **H2 (freshness).** Setting `freshness` on time-sensitive turns raises accuracy
  on the monitor/deals class, where stale pages currently pass as current.
- **H3 (date signal).** Surfacing `sources[].age` (shipped) reduces
  "stale stated as current" errors.

## Variables

- **Arms** (same query set, same models, scratch store):
  - **A — current:** 8 results × ≤1000 chars, no `freshness`.
  - **B — rebalanced:** fewer results × more chars each (e.g. 4 × ≤2000), same
    ~8k-char budget.
  - **C — B + freshness:** `freshness=pw|pm` on time-sensitive queries only.
- **Query set:** the queries that actually ran (`episodes.search_info.query`),
  split into **time-sensitive** and **stable** classes.
- **Metrics:**
  - **M1 — accurate slots/turn** (hand-audited sample).
  - **M2 — "stale stated as current" errors** (the date-specific failure).
  - **M3 — extraction-input tokens** and the prompt's `context_usage`/`truncated`.
  - **M4 — wall-clock per turn.**
  - **M5 — Brave requests** (must not change: 1/turn on this path).

## The bar, stated in advance

- **Ship B** if M1 improves by **≥ 20%** at **≤ the same M3** (token budget).
- **Ship C** on top of B if M2 falls by **≥ 30%** on the time-sensitive class
  without M1 regressing.

## Falsification conditions

1. M1 improvement **< 10%** → the per-result cap is not the constraint; keep A.
2. M1 improves but M3 exceeds the utility model's window (`utility_num_ctx=4096`)
   → it cannot ship as-is.
3. C's M2 does not fall → freshness is not the failure mode; drop H2.

## Threats (named before the run)

- **Ground truth.** M1/M2 need a hand-audited sample; the extractor's own
  confidence is not evidence.
- **The window is the real bound.** `utility_num_ctx=4096` caps total input, so
  "more content" trades directly against "more results" — B must hold the budget.
- **Freshness can cut the answer.** A `freshness` filter may remove the very pages
  that answer a *stable* query, so it must be scoped to time-sensitive turns, never
  applied globally.
- **Date coverage is partial.** `age` is empty for undated pages, so H3 only helps
  where Brave knows the date.
- **One extractor.** Results are the utility model's; a different extractor could
  move M1. State the model and hold it fixed.

## Method

Read-only query set from a brain copy; scratch store; `preflight.py`-gated.

```
EXP_DB=/exp/assistant.db python assistant/experiments/brave_context_budget/experiment.py
```

`result.md` is written only after `verification.md`. The harness
(`experiment.py`) is written when the run is scheduled.
