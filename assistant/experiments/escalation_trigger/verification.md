# Verification — escalation_trigger

## What was run

`experiment.py` on 2026-10-10. Pure heuristics (`_is_multi_step`,
`_has_explicit_think_intent`, `_should_use_max_intelligence`) — **no model, no
brain, no network**, so nothing to mutate and `preflight.py` does not apply.

```
docker run --rm -v $(pwd):/app -w /app assistant \
  python assistant/experiments/escalation_trigger/experiment.py
```

## Raw output

```
tool_task      escalation: 100% (10/10)
hard_reasoning escalation: 0% (0/8)
```

Every `tool_task` fired `max_intelligence=True`; not one `hard_reasoning` query
did.

## Why the hard set scored zero

`_should_use_max_intelligence` requires **all** of `escalate`, `sufficiency ==
NONE`, and `_is_multi_step(query)`. The hard queries carry explicit reasoning cues
("work through", "step by step", "reason through") — so `escalate` is `True` — but
they are **not** `_is_multi_step` (no "and then"), so the final `and
_is_multi_step` fails. The one clause that is supposed to identify hard work is
the one that excludes it.

## The mechanism

`_is_multi_step` fires on a **sequence phrase** — `_SEQUENCE_MARKERS` includes
"and then", "after that", "first," — or on ≥2 clause conjunctions in a long
query. In practice "and then" marks a *recipe* ("read it and then regenerate it"),
which is exactly the mechanical shape. So the proxy is **anti-correlated** with
reasoning difficulty: it selects tool sequences and rejects reasoning requests.

## Threats and limits

- **"Hard" and "mechanical" are judgements**, as pre-registered; the classes are
  separated by whether the work needs reasoning beyond following a file recipe.
- **Empty memory isolates the heuristic** — production sufficiency varies, so this
  is the heuristic's behaviour, not the realised escalation rate.
- 18 queries: confirms a systematic inversion, does not certify a rate.
