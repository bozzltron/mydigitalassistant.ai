# Experiment: does the auto-escalation fire for the right tasks?

## Status

**PRE-REGISTERED. No data collected yet. No result may be written until this
document is committed.**

## Question

The reasoner auto-escalates a turn to the max-intelligence tier (`MAX_MODEL`, a
27B with thinking on and a 12-round tool ceiling) when the query looks
"multi-step" and memory is empty (`_should_use_max_intelligence`). A live turn —
*"I've updated muso-soup-offers.txt. Let's read it and then regenerate Muso Soup
Priority.md"* — escalated and ran **15+ minutes** across 6+ tool rounds on the
27B.

That task is a **mechanical file transformation**: read one file, regenerate
another. It has multiple *tool* steps, not multiple *reasoning* steps. The
trigger keys on `_is_multi_step`, which fires on the phrase "and then" — a proxy
that cannot tell a tool sequence from a hard problem.

## Hypothesis

- **H1 (the one that matters).** The auto-escalation **over-fires on mechanical
  tool/file tasks** — those that should run on the fast warm model.
- **H2.** It still fires for genuinely hard reasoning queries (the escalation is
  not simply broken).

## Variables

- **Query classes (fixed, committed with this plan):**
  - `tool_task` (~10): multi-step **file / mailbox / directory** work — read,
    then regenerate; find, then count; append, then save. Multiple steps,
    no reasoning difficulty.
  - `hard_reasoning` (~8): queries that genuinely need a bigger model — derive,
    prove, design a tradeoff, diagnose a root cause.
- **Decision path:** `_should_use_max_intelligence(query, "functional", NONE,
  escalate)` — the auto path with empty memory (sufficiency `NONE`), which
  isolates the heuristic. Also recorded: `_is_multi_step`,
  `_has_explicit_think_intent`.
- **Recorded per query:** the two sub-signals and the final `max_intelligence`.

## What counts as evidence

- **The `tool_task` escalation rate is the number.** Each one is a 27B turn that
  should have been a warm-9B turn — minutes of avoidable wait.
- The `hard_reasoning` rate guards the other direction: a fix that stops
  escalating hard work is a regression.

## Falsification conditions

1. `tool_task` escalation rate **< 20%** → the trigger is fine in practice; the
   live turn was an outlier. Do not change it.
2. `tool_task` escalation rate **≥ 50%** → the trigger systematically escalates
   mechanical work; change the trigger.
3. `hard_reasoning` escalation rate **< 50%** → the trigger misses hard work;
   any change must not make this worse.

## Method

Pure heuristics — no model, no brain, no network.

```bash
python assistant/experiments/escalation_trigger/experiment.py
```

`result.md` is written only after `verification.md`.

## Threats and limits

- **"Hard" and "mechanical" are judgements.** The classes are defined by whether
  the work needs *reasoning* beyond following a file recipe; the bar is set at
  50% for the falsification, deliberately below 100%.
- **Empty memory isolates the heuristic**, but production sufficiency varies:
  a query with relevant memory does not escalate regardless. So this measures the
  heuristic's behaviour, not the realised escalation rate.
- ~18 queries is a probe: it can confirm a systematic over-fire, not certify a
  rate.
