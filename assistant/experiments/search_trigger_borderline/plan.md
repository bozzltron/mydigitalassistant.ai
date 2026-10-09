# Experiment: does the router search on borderline queries?

## Status

**PRE-REGISTERED. No data collected yet. No result may be written until this
document is committed.**

## Why this, after `search_trigger`

`search_trigger` measured **unambiguous** queries and found the trigger sound
(must-search recall 8/8). The live flakiness it could not explain was the
**borderline** class: a query whose answer *could* come from the model's knowledge
but would be **better with a current source** — "best X for beginners", "recent
research on Y", "current best practices". Running four such queries through the
path post-hoc showed the router vetoes them as general knowledge. That was a
follow-up, not a pre-registered measurement. This is.

## Question

On queries that benefit from current sources but are not obviously external, does
the router search? And does it stay quiet on genuinely stable knowledge?

## Hypotheses

- **H1 (borderline recall — the one that matters).** Recommendation, "recent
  research", and "current best practices" queries trigger a search **≥ 80%** of
  the time. A stale answer to "best X" or "latest research on Y" is worse than a
  search.
- **H2 (general-knowledge specificity).** Stable, definitional queries do **not**
  search **≥ 90%** of the time. Searching them wastes a request and adds nothing.

## Variables

- **Query classes (fixed, committed with this plan):**
  - `borderline_search` (~10): recommendations, recent research, current best
    practices, fast-moving topics.
  - `general_knowledge` (~8): stable definitions and facts.
- **Decision path (as in `_run_turn`):** `route()` → `classify_intent()` (empty
  memory) → the storage veto. Utility model only; no generation, no brain writes.
- **Recorded per query:** `task_type`, `wants_search`, `search_query`,
  `search_needed`, `vetoed`, final decision.

## What counts as evidence

- **Borderline recall is the number.** These are the queries where the internet
  source is being skipped while a current source would help.
- Specificity guards the opposite failure: over-searching stable knowledge.

## Falsification conditions

1. borderline recall **< 50%** → the router systematically under-searches this
   class; the fix is a router bias toward search for these patterns.
2. borderline recall **≥ 80%** → the prior observation was noise; no change.
3. general-knowledge specificity **< 80%** → the router over-searches stable
   knowledge.

## Method

```bash
python assistant/experiments/search_trigger_borderline/experiment.py
```

`result.md` is written only after `verification.md`.

## Threats and limits

- **"Should search" is a judgment.** Some borderline queries are defensible
  either way; the class is defined by the project's rule that the internet is
  authoritative about the external world and a stale recommendation is a poor
  answer. The bar (80%) is deliberately below 100% to allow for that.
- Empty memory context (as in `search_trigger`) isolates the router/veto.
- The router is a model; `temperature=0` but not perfectly deterministic.
- ~18 queries is a probe: it can confirm or refute a systematic gap, not certify a
  rate.
