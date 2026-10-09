# Experiment: does the assistant search when it should?

## Status

**PRE-REGISTERED. No data collected yet. No result may be written until this
document is committed.**

## Question

Search is one of the project's **two sources** (the user and the internet). If a
query that needs the internet is answered from memory or the model's training
instead, the assistant answers **blind and does not know it** — the worst failure
for a system whose job is to triangulate. This measures the trigger.

## Hypotheses

- **H1 (recall — the one that matters).** Queries that need the internet
  (current events, live data, facts the user cannot know first-hand) trigger a
  search **≥ 90%** of the time.
- **H2 (specificity).** Queries that must *not* search (storage statements, the
  user's own files/memory, general knowledge) do **not** search **≥ 90%** of the
  time.

## Variables

- **Queries:** ~8 must-search, ~7 must-not-search, fixed and committed with this
  plan.
- **Decision path (as in `_run_turn`):** `route()` (router) → `classify_intent()`
  (reasoner) → the storage veto → the final decision.
- **Recorded per query:** `task_type`, `wants_search`, `search_query`,
  `search_needed`, and the final decision.

## What counts as evidence

- **Recall on must-search is the number that matters.** A suppressed search is an
  answer given blind; anything under the bar is reported as the failure it is.
- Specificity on must-not-search matters too: searching a storage statement wastes
  a call and sends the statement to the engine.

## Falsification conditions

1. must-search recall < 90% → the internet source is being suppressed; report it
   and find the stage that drops it.
2. must-not-search specificity < 90% → the assistant searches its own storage.

## Method

`experiment.py` drives the **decision path only** (router + reasoner + veto) with
the utility model — no generation, no brain writes, so it is fast and side-effect
free. Pure Ollama I/O.

```
python assistant/experiments/search_trigger/experiment.py
```

`result.md` is written only after `verification.md`.

## Threats and limits

- **The reasoner runs on an EMPTY memory context** (sufficiency `NONE`), which
  biases toward search; production memory could change sufficiency. This isolates
  the router's veto and the reasoner's trigger, not the full retrieval outcome.
- The router is a model; `temperature=0` but not perfectly deterministic.
- ~15 queries is a probe: it can expose a suppression, not certify a rate.
