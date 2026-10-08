# Experiment: does the Q4 candidate write *better* answers than `qwen3.5:9b`?

## Status

**PRE-REGISTERED. No data collected yet. No result may be written until this
document is committed.**

## Question

The fleet comparison (`model_fleet_q4`) found the candidate `q4:9b` **ties**
`qwen3.5:9b` mechanically — tools work, JSON parses, ~40 tok/s. It measured no
*quality*. A chat swap is only worth it if the candidate's **answers are better**,
because nothing else changed. This measures that, blind and pairwise.

## Hypotheses

- **H1 (quality win).** On a fixed prompt set, the candidate's answers are
  preferred over `qwen3.5:9b`'s clearly more often than not.
- **H0 (no win — the incumbent's default).** If the win rate is not clearly above
  50%, there is no quality win and `qwen3.5:9b` stays.

## Variables

- **Models:** candidate `sorc/qwen3.5-claude-4.6-opus-q4:9b`; incumbent
  `qwen3.5:9b`. Both `think=False`, `temperature=0` (production chat defaults).
- **Prompts (~10):** factual QA, multi-step reasoning, instruction-following (a
  hard constraint), summarization, advice, and a short creative/format task.
- **Judge:** `qwen3.8:27b` — a **different family** from both contestants. Blind
  (answers labelled A/B, model names hidden), and **position-randomized**: each
  pair is judged twice with A/B swapped.

## What counts as evidence

- The primary number is the candidate's **position-robust win rate**: a pair counts
  as a candidate win only when the judge prefers the candidate in **both** orders.
  An order-flip is recorded as a position-bias flip, not a win; a double-tie is a
  tie.
- **The bar is set in advance:** a win rate ≤ 50% (i.e. not more wins than losses)
  is a **no-win** result — keep the incumbent.

## Falsification conditions

1. Position-robust win rate ≤ 50% → no quality win → keep `qwen3.5:9b`.
2. The judge flips with position on most pairs → the measurement is unreliable;
   report it and fix the judge, do **not** decide on it.

## Method

`experiment.py` asks both models each prompt, then has the judge compare each pair
blind in both orders, and reports the position-robust tally. Pure Ollama I/O; no
brain, so `preflight.py` does not apply.

```
python assistant/experiments/chat_quality_q4/experiment.py
```

`result.md` is written only after `verification.md`.

## Threats and limits

- **LLM-judge bias is real** (self-preference, family, verbosity, position). A
  different-family judge and a position swap reduce it; they do not eliminate it.
  The judge's per-pair reasoning is stored so a human can audit it.
- **Ten prompts is a probe**, not a benchmark.
- Length/verbosity can sway a judge; the judge prompt says to ignore length, which
  is a mitigation, not a guarantee.
