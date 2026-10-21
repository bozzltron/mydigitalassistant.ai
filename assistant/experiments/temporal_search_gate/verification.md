# Verification

Checked before `result.md`.

## 1. The memory really was sufficient

The harness asserts `assess_memory_sufficiency` returns `high` for all 20 queries
before writing any result. A frame at relevance 0.85 and confidence 0.8 clears the
`_HIGH_CONFIDENCE_THRESHOLD = 0.6` bar, so `baseline` can never search — which is
the bug being reproduced, not a harness artefact. Had the assertion failed the run
would be void (the plan's "ceiling" falsifier).

## 2. The decision function is the production gate

`decides()` mirrors `orchestrator.py:1567-1591` exactly:

- search starts as `sufficiency == NONE`;
- `wants_search is False` vetoes, in both policies;
- `wants_search is True` forces when `sufficiency == NONE` (baseline) or
  unconditionally (`router_positive`).

The only difference between policies is the `sufficiency == NONE` condition on the
force branch — the one line the experiment tests.

## 3. Router labels are stable

Three replicates per query, temperature 0.0. `flapped_queries` is empty: every
query returned the same `wants_search` three times. The majority vote used for the
decision is therefore not hiding disagreement.

## 4. Recall and false positives are reported, not assumed

- Period recall 0.90 — the one miss is "What's on my calendar this afternoon?",
  which is the user's own calendar and should not web-search.
- Stable false-positive rate 0.00 — no stable question is pulled into search.

Both are measured on this run, not carried over from `temporal_validity`.

## 5. What was not run

No web search, no chat generation, no database. The harness calls the router model
and the reasoner's pure function only. There is no internet variance, no sampling
variance beyond the router's three labels, and no memory to mutate.

This also means **the cost of the added searches is not measured here**: the run
counts decisions, not round trips. Stated in the plan and in the result.

## 6. Threat that remains

The memory context is synthetic: one frame, high relevance, high confidence. Real
retrieval returns several frames of varying confidence, and `sufficiency` is an
average — a real context could be `PARTIAL` rather than `HIGH`. The gate change
makes that irrelevant for the force branch (it no longer consults sufficiency),
which is why the result is not sensitive to this; but the *baseline* number (0.0)
is a synthetic-context number and would be somewhat higher in a real `PARTIAL`
case, where `search_needed` is still false but the router can force only when
`NONE`. The direction is unchanged.
