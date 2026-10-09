# Result — does the assistant search when it should?

**On unambiguous external queries: yes, 100%.** The internet source is **not**
systematically suppressed. The flakiness is narrower than it looked: the router
under-searches **borderline** queries ("explain recent research…", "best X for
beginners"), judging them general knowledge.

## By hypothesis

| | Verdict | Evidence |
|---|---|---|
| **H1 — recall (decisive)** | **holds** | must-search **8/8 (100%)**; every one set `wants_search=True` with a distilled query. |
| **H2 — specificity** | **holds** | must-not-search **7/7 (100%)**; none searched. |

## Falsification conditions, resolved

1. must-search recall < 90% → **not triggered** (100%).
2. must-not-search specificity < 90% → **not triggered** (100%).

## The real finding: borderline queries

The live flakiness was three turns that did not search. Running those exact
queries through the path showed the router vetoes them as general knowledge:

```
Explain recent research on lithium-ion battery degradation.  wants=False -> no search
What are the best acoustic guitar strings for beginners?     wants=False -> no search
Explain how photosynthesis works.                            wants=False -> no search
Summarize the latest research on sleep and memory.           wants=True  -> search
```

So the trigger is correct where the answer *must* come from the internet, and
inconsistent where the answer *could* come from knowledge but would be **better
with current sources** — a product recommendation, "recent research", or a
fast-moving topic. That is a router-judgment gap, not a broken pipeline.

## Verdict

- **The pipeline is sound.** The query-distillation fix (yesterday) plus this
  measurement means a search, when it runs, targets the right thing; and it runs
  for queries that need it.
- **The improvement is targeted:** bias the router toward search for
  `recent / latest / current` research and **recommendation** queries ("best X",
  "which Y"), where a stale answer is worse than a search. That needs its own
  pre-registered probe on borderline queries — this one was deliberately
  unambiguous, so it cannot certify the borderline rate.

## What is still unmeasured

- **Borderline-query trigger rate** — the class where the flakiness lives.
- **Corroboration** — of the facts extracted from a search, how many are
  corroborated by ≥2 independent domains (the triangulation metric).
